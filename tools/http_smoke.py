#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP smoke: дашборд, вердикты ДемоА/Б/В, сохранение форм на throwaway-пациенте."""
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5578"


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoFollow())
urllib.request.install_opener(_opener)

ATC_RE = re.compile(r"\bJ\d{2}[A-Z]{2}\d{2}\b")
GAP_RE = re.compile(
    r"\b(not_first_line_abt|missing_cbc|missing_crp|no_abt|"
    r"hospitalization_indicated|icu_indicated|cxr_indicated)\b"
)
FAIL = 0


def get(path):
    r = urllib.request.urlopen(BASE + path, timeout=20)
    return r.status, r.read().decode("utf-8", "replace")


def post(path, data, headers=None):
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, r.headers.get("Location", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return (
            e.code,
            e.headers.get("Location", "") if e.headers else "",
            e.read().decode("utf-8", "replace"),
        )


def check(cond, msg):
    global FAIL
    if cond:
        print(f"  OK    {msg}")
    else:
        print(f"  FAIL  {msg}")
        FAIL += 1


def find_demo_pids(dash):
    """Жёстко: ссылка сразу содержит имя (без DOTALL — иначе цепляет соседний ряд)."""
    out = {}
    for name in ("ДемоА", "ДемоБ", "ДемоВ"):
        m = re.search(rf'href="/patient/(p-[a-f0-9]+)">\s*{name}\b', dash)
        if m:
            out[name] = m.group(1)
    return out


def verdict_section(html):
    m = re.search(
        r'<section class="verdict-panel[^"]*"[^>]*>.*?</section>',
        html,
        re.S,
    )
    return m.group(0) if m else ""


def encounter_id(html):
    m = re.search(r'name="encounter_id"[^>]*value="(e-[a-f0-9]+)"', html)
    if m:
        return m.group(1)
    m = re.search(r'value="(e-[a-f0-9]+)"', html)
    return m.group(1) if m else ""


def main():
    print(f"HTTP smoke → {BASE}")
    st, dash = get("/")
    check(st == 200, f"dashboard {st}")
    check("ДемоА" in dash and "ДемоБ" in dash and "ДемоВ" in dash, "на дашборде ДемоА/Б/В")

    pids = find_demo_pids(dash)
    check(set(pids) == {"ДемоА", "ДемоБ", "ДемоВ"}, f"pid map={pids}")
    if len(pids) < 3:
        return 1

    cards = {}
    for name, pid in pids.items():
        st, html = get(f"/patient/{pid}")
        cards[name] = html
        check(st == 200 and name in html, f"{name}: карточка 200")
        v = verdict_section(html)
        check("Сейчас по протоколу" in v, f"{name}: блок вердикта")
        check(not ATC_RE.search(v), f"{name}: в вердикте нет ATC")
        check(not GAP_RE.search(v), f"{name}: в вердикте нет gap-кодов")
        check("Unexpected" not in html, f"{name}: нет JS/HTML Unexpected")

    a, b, c = cards["ДемоА"], cards["ДемоБ"], cards["ДемоВ"]
    va, vb, vc = verdict_section(a), verdict_section(b), verdict_section(c)

    check("verdict-ok" in va or "Соответствует" in va, "ДемоА: соответствует")
    check("Есть отклонения" not in va, "ДемоА: без badge отклонений")
    check("амоксициллин" in va.lower(), "ДемоА: терапия амоксициллин")

    check("Есть отклонения" in vb, "ДемоБ: есть отклонения")
    check("амоксициллин" in vb.lower(), "ДемоБ: подсказка амоксициллин")
    check("клавулан" not in vb.lower(), "ДемоБ: не амокс/клав (нет лишнего фактора риска)")
    check("Азитромицин" in vb or "азитромицин" in vb.lower(), "ДемоБ: видно неверную АБТ")
    check("Азитромицин" in b, "ДемоБ: назначение Азитромицин в карте")

    check("Есть отклонения" in vc, "ДемоВ: есть отклонения")
    check(
        any(x in vc.lower() for x in ("тяжёл", "госпитал", "орит", "амокси", "цефтриак", "антибиот")),
        "ДемоВ: клиническая подсказка в вердикте",
    )

    for name, expect_ok in (("ДемоА", True), ("ДемоБ", False), ("ДемоВ", False)):
        st, body = get(f"/api/protocol-cap/{pids[name]}")
        check(st == 200, f"api {name} {st}")
        try:
            data = json.loads(body)
            check(data.get("applicable") is True, f"api {name} applicable")
            check(data.get("compliant") is expect_ok, f"api {name} compliant={expect_ok}")
        except Exception as e:
            check(False, f"api {name} json: {e}")

    # --- Запись: throwaway-пациент, демо не портим ---
    st, loc, _ = post("/patient/new", {
        "family": "SmokeТест",
        "given": "Проверка",
        "patronymic": "",
        "gender": "male",
        "birth_date": "1980-01-01",
    })
    m = re.search(r"/patient/(p-[a-f0-9]+)", loc or "")
    if not m and st == 200:
        # иногда redirect в теле/другой форме
        st2, dash2 = get("/")
        m = re.search(r'href="/patient/(p-[a-f0-9]+)">\s*SmokeТест\b', dash2)
    pid = m.group(1) if m else ""
    check(bool(pid), f"throwaway patient pid={pid} (POST /patient/new → {st})")
    if not pid:
        print(f"\nИТОГ http_smoke: {FAIL} FAIL (нет throwaway)")
        return 1

    st, card = get(f"/patient/{pid}")
    eid = encounter_id(card)
    if not eid:
        st, loc, _ = post(f"/patient/{pid}/encounter", {
            "class": "ambulatory",
            "date": "2026-07-25",
            "reason": "smoke",
        })
        st, card = get(f"/patient/{pid}")
        eid = encounter_id(card)
    check(bool(eid), f"throwaway encounter_id={eid}")

    marker = "QG-ANAM-smoke25"
    st, _, _ = post(f"/patient/{pid}/anamnesis", {"encounter_id": eid, "text": marker})
    check(st in (302, 303) or (300 <= st < 400), f"POST anamnesis → {st}")
    st, after = get(f"/patient/{pid}")
    check(marker in after, "анамнез сохранился и виден")

    st, _, _ = post(f"/patient/{pid}/observation", {
        "encounter_id": eid,
        "code": "8867-4",
        "value_numeric": "93",
        "date": "2026-07-25",
    })
    check(st in (302, 303) or (300 <= st < 400), f"POST observation → {st}")
    st, after = get(f"/patient/{pid}")
    check("93" in after, "ЧСС=93 видна после сохранения")

    st, _, body = post(
        f"/patient/{pid}/observation",
        {
            "encounter_id": eid,
            "code": "8310-5",
            "value_numeric": "37.2",
            "date": "2026-07-25",
        },
        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    )
    ajax_ok = False
    try:
        ajax_ok = json.loads(body).get("ok") is True
    except Exception:
        ajax_ok = False
    check(st == 200 and ajax_ok, f"AJAX observation → {st}")

    st, _, _ = post(f"/patient/{pid}/flag", {
        "encounter_id": eid,
        "key": "recent_travel",
    })
    check(st in (302, 303) or (300 <= st < 400), f"POST flag → {st}")
    st, after = get(f"/patient/{pid}")
    check("поездк" in after.lower() or "перемещен" in after.lower(), "флаг сохранился")

    st, _, _ = post(f"/patient/{pid}/service_request", {
        "encounter_id": eid,
        "code": "CXR",
        "occurrence_date": "2026-07-25",
    })
    check(st in (302, 303, 200) or (300 <= st < 400), f"POST service_request CXR → {st}")
    st, after = get(f"/patient/{pid}")
    check("Рентгенография" in after or "грудной" in after.lower(), "заказ R-графии виден")

    st, _, _ = post(f"/patient/{pid}/medication", {
        "encounter_id": eid,
        "code": "J01CA04",
        "display": "Амоксициллин",
        "dose": "500 мг",
        "frequency": "3 раза в день",
        "route": "oral",
        "med_date": "2026-07-25",
        "period_end": "2026-08-01",
        "confirm": "1",
    })
    check(st in (302, 303) or (300 <= st < 400), f"POST medication → {st}")
    st, after = get(f"/patient/{pid}")
    check("Амоксициллин" in after, "назначение Амоксициллин сохранилось")

    # ДемоБ после write-тестов не должен «поплыть»
    st, b2 = get(f"/patient/{pids['ДемоБ']}")
    vb2 = verdict_section(b2)
    check("Есть отклонения" in vb2, "ДемоБ после smoke всё ещё с отклонениями")
    check("клавулан" not in vb2.lower(), "ДемоБ не испорчен фактором риска из smoke")

    # Убрать throwaway, чтобы дашборд оставался демо-чистым
    st, _, _ = post(f"/patient/{pid}/delete", {})
    check(st in (302, 303, 200) or (300 <= st < 400), f"cleanup throwaway → {st}")
    st, dash2 = get("/")
    check("SmokeТест" not in dash2, "SmokeТест удалён с дашборда")

    print(f"\nИТОГ http_smoke: {'PASS' if FAIL == 0 else f'{FAIL} FAIL'}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
