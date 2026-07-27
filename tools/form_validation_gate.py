#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Комплексный гейт: валидация форм приёма + подсветка ошибок в UI.

Покрывает:
  API (Flask test_client) — контракт ошибок observation/anamnesis/GC/CDS
  UI (Playwright) — поле получает .is-invalid, текст ошибки в .note/.field-error,
                    soft/hard CDS-диалог

Запуск:
  python3 tools/form_validation_gate.py
"""
from __future__ import annotations

import importlib.util
import io
import os
import socket
import sys
import tempfile
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.pop("DATABASE_URL", None)
os.environ["DEMO_MODE"] = "1"

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False  # noqa: E731

import db  # noqa: E402

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
db.DB_PATH = os.path.abspath(_TMP.name)
os.environ["CLINIC_DB"] = db.DB_PATH

import fhir_store as fs  # noqa: E402

fs.init_db()

PASS = FAIL = 0
HDR = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}


def check(cond: bool, msg: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK    {msg}")
    else:
        FAIL += 1
        print(f"  FAIL  {msg}")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed():
    path = os.path.join(REPO, "tools", "seed_ten.py")
    spec = importlib.util.spec_from_file_location("seed_ten_fvg", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_ten_fvg"] = mod
    spec.loader.exec_module(mod)
    mod._clear_clinical()
    dr = fs.add_practitioner("Терапевт", "Анна", "терапия")
    with io.StringIO() as buf, contextlib_redirect(buf):
        stories = mod.seed_ten(dr)
        mod._ensure_drugs()
    import protocol_dispatch as pdisp

    for pid, _name, _story in stories:
        pdisp.refresh_protocol_cache(pid)
    return {name: pid for pid, name, _ in stories}


class contextlib_redirect:
    def __init__(self, buf):
        self.buf = buf

    def __enter__(self):
        self._old = sys.stdout
        sys.stdout = self.buf
        return self.buf

    def __exit__(self, *a):
        sys.stdout = self._old


def _start_server(app, port: int):
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", port, app, threaded=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    import urllib.error
    import urllib.request

    deadline = time.time() + 20
    last = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return server
        except urllib.error.HTTPError:
            return server
        except Exception as e:
            last = e
            time.sleep(0.2)
    raise RuntimeError(f"server not up: {last}")


def run_api(client, by_name: dict) -> str:
    """API-контракты ошибок. Возвращает pid гостя (Соколов) для UI."""
    print("\n[API] Observation — допустимые / недопустимые значения")
    pid = by_name["Соколов"]
    encs = fs.get_encounters(pid)
    eid = encs[0]["id"]
    before = len(fs.get_observations(pid))

    r = client.post(
        f"/patient/{pid}/observation",
        data={"encounter_id": eid, "code": "8310-5", "value_numeric": "100", "date": "2026-07-27"},
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(r.status_code == 200, f"t=100 → HTTP {r.status_code}")
    check(data.get("ok") is False, f"t=100 → ok=false (got {data})")
    check("Допустимо" in (data.get("error") or ""), f"t=100 → текст Допустимо (got {data.get('error')})")
    check(len(fs.get_observations(pid)) == before, "t=100 не сохранено")

    r = client.post(
        f"/patient/{pid}/observation",
        data={"encounter_id": eid, "code": "8310-5", "value_numeric": "abc", "date": "2026-07-27"},
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is False and "Некорректное" in (data.get("error") or ""),
          f"t=abc → Некорректное (got {data})")

    r = client.post(
        f"/patient/{pid}/observation",
        data={"encounter_id": eid, "code": "59408-5", "value_numeric": "200", "date": "2026-07-27"},
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is False and "Допустимо" in (data.get("error") or ""),
          f"SpO2=200 → Допустимо (got {data.get('error')})")

    r = client.post(
        f"/patient/{pid}/observation",
        data={"encounter_id": eid, "code": "8310-5", "value_numeric": "36,6", "date": "2026-07-27"},
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is True, f"t=36,6 → ok (got {data})")
    check(len(fs.get_observations(pid)) == before + 1, "t=36,6 сохранено")

    r = client.post(
        f"/patient/{pid}/observation",
        data={"encounter_id": eid, "code": "6690-2", "value_numeric": "999", "date": "2026-07-27"},
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is False and "Допустимо" in (data.get("error") or ""),
          f"WBC=999 → Допустимо (got {data.get('error')})")

    print("\n[API] Анамнез / общее состояние")
    r = client.post(
        f"/patient/{pid}/anamnesis",
        data={"encounter_id": eid, "text": "   "},
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is False, f"пустой анамнез → ok=false (got {data})")

    r = client.post(
        f"/patient/{pid}/general_condition",
        data={"encounter_id": eid, "key": "not-a-key"},
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is False, f"GC unknown → ok=false (got {data})")

    r = client.post(
        f"/patient/{pid}/general_condition",
        data={"encounter_id": eid, "key": "moderate"},
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is True, f"GC moderate → ok (got {data})")

    print("\n[API] CDS soft/hard на назначении")
    pid_p = by_name["Пустова"]
    eid_p = fs.get_encounters(pid_p)[0]["id"]
    before_med = {m["id"] for m in fs.get_medications(pid_p, status="active")}
    r = client.post(
        f"/patient/{pid_p}/medication",
        data={
            "encounter_id": eid_p, "code": "J01FA10", "display": "Азитромицин",
            "dose": "500 мг", "frequency": "1 раз в день", "route": "oral",
            "med_date": "2026-07-25", "period_end": "2026-08-01", "confirm": "",
        },
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("need_confirm") is True and data.get("level") == "soft",
          f"Пустова soft need_confirm (got {data.get('level')})")
    check({m["id"] for m in fs.get_medications(pid_p, status="active")} == before_med,
          "soft без confirm не сохраняет")

    pid_a = by_name["Аллергова"]
    eid_a = fs.get_encounters(pid_a)[0]["id"]
    for m in list(fs.get_medications(pid_a, status="active")):
        if (m.get("code") or "").startswith("J01"):
            fs.stop_medication(m["id"])
    r = client.post(
        f"/patient/{pid_a}/medication",
        data={
            "encounter_id": eid_a, "code": "J01CA04", "display": "Амоксициллин",
            "dose": "500 мг", "frequency": "3 раза в день", "route": "oral",
            "med_date": "2026-07-25", "period_end": "2026-08-01", "confirm": "",
        },
        headers=HDR,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("need_confirm") is True and data.get("level") == "hard",
          f"Аллергова hard need_confirm (got {data.get('level')})")

    return pid, by_name["Пустова"]


def run_ui(port: int, pid: str, pid_soft: str) -> None:
    print("\n[UI] Playwright — подсветка ошибок и CDS-диалог")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        check(False, "playwright не установлен — UI-часть пропущена")
        return

    base = f"http://127.0.0.1:{port}"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"{base}/patient/{pid}", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(400)

            # Открыть Осмотр
            exam = page.locator("details#flow-exam, details:has(summary:has-text('Осмотр'))").first
            if exam.count():
                if not exam.evaluate("e => e.open"):
                    exam.locator("summary").first.click()
                page.wait_for_timeout(200)

            # Форма показателей (не lab)
            form = page.locator("form.enc-sub-add[action*='/observation']:not([data-lab])").first
            if not form.count():
                form = page.locator("form.enc-sub-add[action*='/observation']").first
            check(form.count() > 0, "форма observation на странице")
            if not form.count():
                return

            code = form.locator("select[name='code']")
            code.select_option(value="8310-5")
            page.wait_for_timeout(100)
            val = form.locator("input[name='value_numeric']")
            val.fill("100")
            form.locator("button[type='submit']").click()
            page.wait_for_timeout(500)

            has_invalid = val.evaluate("el => el.classList.contains('is-invalid')")
            aria = val.get_attribute("aria-invalid")
            note = form.locator("xpath=..").locator(".note, .hint").inner_text()
            check(has_invalid is True, "t=100 → input.is-invalid")
            check(aria == "true", f"t=100 → aria-invalid=true (got {aria})")
            check("Допустимо" in note, f"t=100 → note с Допустимо (got {note!r})")

            val.fill("36.6")
            page.wait_for_timeout(100)
            cleared = val.evaluate("el => !el.classList.contains('is-invalid')")
            check(cleared is True, "правка значения снимает is-invalid")
            form.locator("button[type='submit']").click()
            page.wait_for_timeout(800)
            check(val.input_value() == "", "после ok значение очищено")

            code.select_option(value="59408-5")
            val.fill("")
            form.locator("button[type='submit']").click()
            page.wait_for_timeout(300)
            check(val.evaluate("el => el.classList.contains('is-invalid')"),
                  "пустое значение → is-invalid")

            # Soft-stop UI: Пустова + макролид (id из сида — дашборд кликабелен по tr, не по <a>)
            page.goto(f"{base}/patient/{pid_soft}", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(400)
            check(True, f"открыта Пустова {pid_soft}")

            # Предпочитаем полную форму «Другой препарат» / «Препарат»
            for sel_sum in (
                "details.cds-alt > summary",
                "details.add-panel > summary:has-text('Препарат')",
            ):
                s = page.locator(sel_sum).first
                if s.count():
                    try:
                        s.click(timeout=2000)
                        page.wait_for_timeout(200)
                    except Exception:
                        pass

            # Любой select code с J01FA10
            selected = page.evaluate(
                """() => {
                  const sels = [...document.querySelectorAll('form[action*="medication"] select[name="code"]')];
                  for (const sel of sels) {
                    const opt = [...sel.options].find(o => o.value === 'J01FA10');
                    if (opt) { sel.value = 'J01FA10'; sel.dispatchEvent(new Event('change', {bubbles:true})); return true; }
                  }
                  return false;
                }"""
            )
            check(selected is True, "выбран азитромицин в форме")
            if selected:
                # Сабмитим форму из «Другой препарат» / add-panel, не now-action CTA.
                submitted = {"ok": False, "body": None}

                def _on_response(resp):
                    try:
                        if "medication" in resp.url and resp.request.method == "POST" and "/check" not in resp.url:
                            submitted["ok"] = True
                            submitted["body"] = resp.json()
                    except Exception:
                        pass

                page.on("response", _on_response)
                page.evaluate(
                    """() => {
                      const preferred = [...document.querySelectorAll(
                        'details.cds-alt form[action*="medication"], details.add-panel form[action*="medication"]'
                      )];
                      const forms = preferred.length ? preferred
                        : [...document.querySelectorAll('form[action*="medication"]')];
                      for (const form of forms) {
                        const sel = form.querySelector('select[name="code"]');
                        if (!sel) continue;
                        sel.value = 'J01FA10';
                        sel.dispatchEvent(new Event('change', {bubbles:true}));
                        const cf = form.querySelector('input[name="confirm"]');
                        if (cf) cf.value = '';
                        const btn = form.querySelector('button[type="submit"]');
                        if (btn) { btn.click(); return true; }
                        form.requestSubmit();
                        return true;
                      }
                      return false;
                    }"""
                )
                page.wait_for_timeout(1200)
                body = submitted["body"] or {}
                check(
                    body.get("need_confirm") is True,
                    f"soft-stop: API need_confirm (got {body})",
                )
                visible = page.evaluate(
                    """() => {
                      const nodes = [...document.querySelectorAll('.cds-confirm, .cds-ov')];
                      return nodes.some(n => {
                        const s = getComputedStyle(n);
                        return s.display !== 'none' && s.visibility !== 'hidden'
                          && (n.textContent||'').trim().length > 10;
                      });
                    }"""
                )
                check(visible, "soft-stop: диалог CDS виден")
        finally:
            browser.close()


def main() -> int:
    print("FORM VALIDATION GATE — API + UI highlight")
    print("=" * 70)
    print("\n[0] Seed")
    by_name = _seed()
    check("Соколов" in by_name and "Пустова" in by_name and "Аллергова" in by_name,
          f"демо-пациенты: {sorted(by_name)}")

    os.environ.pop("DATABASE_URL", None)
    from app import app

    app.config["TESTING"] = True
    client = app.test_client()
    pid, pid_soft = run_api(client, by_name)

    port = _free_port()
    server = _start_server(app, port)
    try:
        run_ui(port, pid, pid_soft)
    finally:
        server.shutdown()

    print("\n" + "=" * 70)
    print(f"ИТОГ form_validation_gate: {PASS} ok, {FAIL} fail")
    print("=" * 70)
    try:
        os.unlink(_TMP.name)
    except OSError:
        pass
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
