#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Doctor gate — с нуля: чистая БД → seed_ten → Flask test_client → пути врача.

Если что-то не работает для врача — падаем здесь.
Запуск:
  python3 tools/doctor_gate.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.pop("DATABASE_URL", None)
os.environ["DEMO_MODE"] = "1"

# app.py зовёт load_dotenv() — не даём подтянуть облачный DATABASE_URL.
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False  # noqa: E731

import db  # noqa: E402

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
db.DB_PATH = os.path.abspath(_TMP.name)
os.environ["CLINIC_DB"] = db.DB_PATH

import fhir_store as fs  # noqa: E402
import protocol_cap as pcap  # noqa: E402
import protocol_dispatch as pdisp  # noqa: E402
from protocol_verdict import verdict_for_ui  # noqa: E402

fs.init_db()

ATC_RE = re.compile(r"\bJ\d{2}[A-Z]{2}\d{2}\b")
GAP_RE = re.compile(
    r"\b(not_first_line_abt|missing_cbc|no_abt|hospitalization_indicated|"
    r"icu_indicated|abt_no_effect|diagnosis_unsupported|course_too_short)\b"
)

PASS = FAIL = 0


def check(cond: bool, msg: str) -> None:
    global PASS, FAIL
    print(f"  {'OK   ' if cond else 'FAIL '} {msg}")
    if cond:
        PASS += 1
    else:
        FAIL += 1


def _body(html: str) -> str:
    return html.split("</style>", 1)[-1] if "</style>" in html else html


def _verdict(html: str) -> str:
    """Primary verdict banner (div.verdict-panel#now-action)."""
    m = re.search(r'<div class="verdict[^"]*verdict-panel[^"]*"[^>]*id="now-action"[^>]*>.*?</div>', html, re.S)
    return m.group(0) if m else ""


def _episode(html: str) -> str:
    """Первый episode (диагноз-контейнер)."""
    m = re.search(r'<div class="episode[^"]*"[^>]*>.*?</div>\s*</div>', html, re.S)
    return m.group(0) if m else ""


def seed() -> dict[str, str]:
    import importlib.util

    path = os.path.join(REPO, "tools", "seed_ten.py")
    spec = importlib.util.spec_from_file_location("seed_ten_gate", path)
    mod = importlib.util.module_from_spec(spec)
    # Не запускаем __main__ (он сам чистит argv); вызываем API.
    sys.modules["seed_ten_gate"] = mod
    spec.loader.exec_module(mod)
    mod._clear_clinical()
    dr = fs.add_practitioner("Терапевт", "Анна", "терапия")
    stories = mod.seed_ten(dr)
    mod._ensure_drugs()
    for pid, _name, _story in stories:
        pdisp.refresh_protocol_cache(pid)
    return {name: pid for pid, name, _ in stories}


def main() -> int:
    print("=" * 70)
    print("DOCTOR GATE — с нуля")
    print(f"DB: {db.DB_PATH}")
    print("=" * 70)

    print("\n[1] Сид пациентов (ВП + ЖДА)")
    by_name = seed()
    check(len(by_name) == 12, f"seeded {len(by_name)} patients")
    check(len(fs.get_drug_catalog()) >= 20, f"drugs={len(fs.get_drug_catalog())}")

    # app после привязки DB_PATH (dotenv уже заглушен)
    print("\n[2] Flask test_client")
    os.environ.pop("DATABASE_URL", None)
    db._reset_pg_conn()
    db.DB_PATH = os.path.abspath(_TMP.name)
    from app import app  # noqa: WPS433

    # На случай если app всё же подтянул env при импорте.
    os.environ.pop("DATABASE_URL", None)
    db._reset_pg_conn()
    db.DB_PATH = os.path.abspath(_TMP.name)
    assert db.backend() == "sqlite", db.backend()

    app.config["TESTING"] = True
    client = app.test_client()

    r = client.get("/")
    dash = r.data.decode("utf-8", "replace")
    check(r.status_code == 200, "GET / → 200")
    check("Сделать сейчас" in dash, "дашборд: колонка «Сделать сейчас»")
    check("Соколов" in dash, "Соколов в списке пациентов")
    for name in by_name:
        check(name in dash, f"дашборд: {name}")
    # Multi-protocol: ЖДА на дашборде через next_step (номер КП в списке не показываем).
    check("Феррова" in dash and "Железов" in dash, "дашборд: пациенты ЖДА в списке")
    check("желез" in dash.lower(), "дашборд: next_step по ЖДА (железо)")
    c_f = fs.get_cap_cache(by_name["Феррова"]) or {}
    check(c_f.get("protocol_id") == "ida_adult_23", f"Феррова: cache protocol_id=ida (got {c_f.get('protocol_id')})")
    check(bool(c_f.get("applicable")), "Феррова: cache applicable")
    check(not c_f.get("compliant"), "Феррова: cache non-compliant (нет железа)")
    check("желез" in (c_f.get("next_step") or "").lower(), f"Феррова: next_step про железо (got {c_f.get('next_step')!r})")
    c_j = fs.get_cap_cache(by_name["Железов"]) or {}
    check(c_j.get("protocol_id") == "ida_adult_23", "Железов: cache protocol_id=ida")
    check(bool(c_j.get("compliant")), "Железов: cache compliant")

    r = client.get("/demo", follow_redirects=False)
    check(r.status_code in (301, 302, 303, 307, 308), f"/demo → {r.status_code}")
    loc = r.headers.get("Location", "")
    check("/patient/" in loc, f"/demo Location={loc}")

    print("\n[3] Карточка: путь врача")
    for name, pid in sorted(by_name.items()):
        r = client.get(f"/patient/{pid}")
        html = r.data.decode("utf-8", "replace")
        check(r.status_code == 200, f"{name}: 200")
        body = _body(html)
        v = _verdict(html)
        ep = _episode(html)
        check(bool(v), f"{name}: есть verdict-panel")
        check(bool(ep), f"{name}: есть episode")
        text = v.split("<form")[0] if "<form" in v else v
        check(not ATC_RE.search(text), f"{name}: вердикт без ATC")
        check(not GAP_RE.search(text), f"{name}: вердикт без gap-кодов")
        pos_now = body.find('id="now-action"')
        pos_hist = body.find("history-fold")
        # Если истории нет — пропускаем проверку порядка
        if pos_hist >= 0:
            check(0 <= pos_now < pos_hist, f"{name}: now-action выше истории")
        else:
            check(pos_now >= 0, f"{name}: now-action выше истории (нет истории)")
        hist_m = re.search(r'<details class="history-fold"([^>]*)>', html)
        # Primary среди всех applicable протоколов (ВП / ЖДА), не только evaluate_cap.
        primary = pdisp.pick_primary_assessment(pdisp.patient_assessments(pid))
        if primary:
            ui = verdict_for_ui(primary["assessment"], primary["protocol_id"])
        else:
            ui = verdict_for_ui(pcap.evaluate_cap(pid))
        # Приём — блок с датой/поводом.
        check("Приём" in body or "Контрольный визит" in body, f"{name}: словарь приём/контрольный визит")
        check('id="conditions-list"' in html, f"{name}: есть conditions-list")
        if ui.get("ok"):
            check("verdict-ok" in v, f"{name}: verdict-ok")
        else:
            check("verdict-warn" in v or "verdict-critical" in v, f"{name}: verdict-warn/critical")
            check("К назначениям" not in v and "К госпитализации" not in v,
                  f"{name}: без лишнего CTA-прыжка")
            focus = ui.get("focus_stage")
            if focus == "med":
                check('id="med-code-now"' in html, f"{name}: форма терапии")
                med_verb = "Назначить" if ui.get("no_active_therapy") else "Заменить"
                check(med_verb in html, f"{name}: кнопка {med_verb.lower()}")
                sug = ui.get("suggest_atc")
                if sug:
                    check(f'value="{sug}"' in html and "selected" in html,
                          f"{name}: suggest_atc={sug} предвыбран")
                route = ui.get("suggest_route")
                if route:
                    check(f'name="route"' in html and f'value="{route}"' in html,
                          f"{name}: suggest_route={route}")
            if focus == "actions":
                check("Госпитализировать" in html or "ОРИТ" in html, f"{name}: действие госпитализации/ОРИТ")
            if focus == "cond":
                check("Поставить диагноз" in html or "МКБ" in html, f"{name}: форма диагноза")
            # CDS: не вываливать дамп виталов в видимый текст подсказки
            text = v.split("<details")[0] if "<details" in v else v
            check("ЧД 32" not in text and "×10" not in text, f"{name}: CDS без стены виталов")

    print("\n[4] Соколов: смена АБТ")
    pid_b = by_name["Соколов"]
    r = client.get(f"/patient/{pid_b}")
    html = r.data.decode("utf-8", "replace")
    check('id="med-code-now"' in html, "Соколов: med-code-now")
    check('name="replace_abt"' in html, "Соколов: replace_abt в форме")
    check("Заменить" in html, "Соколов: кнопка замены")
    check("Азитромицин" in html, "Соколов: текущий АБТ")
    eid_m = re.search(r'name="encounter_id"[^>]*value="(e-[a-f0-9]+)"', html)
    eid = eid_m.group(1) if eid_m else ""
    check(bool(eid), f"Соколов: encounter в форме ({eid})")
    # POST замены АБТ
    r = client.post(
        f"/patient/{pid_b}/medication",
        data={
            "encounter_id": eid,
            "code": "J01CA04",
            "display": "Амоксициллин",
            "dose": "500 мг",
            "frequency": "3 раза в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
            "replace_abt": "1",
            "confirm": "",
        },
        follow_redirects=True,
    )
    check(r.status_code == 200, f"Соколов: POST medication → {r.status_code}")
    body = r.data.decode("utf-8", "replace")
    check("Соответствует" in body or "verdict-ok" in body, "Соколов: после замены вердикт обновился")
    active = fs.get_medications(pid_b, status="active")
    codes = {m.get("code") for m in active}
    check("J01CA04" in codes, f"Соколов: активен амоксициллин ({codes})")
    check(not any(c.startswith("J01FA") for c in codes), f"Соколов: азитромицин снят ({codes})")
    cap = pcap.evaluate_cap(pid_b)
    warn = {g["code"] for g in cap.get("gaps", []) if g.get("severity") == "warning"}
    check("not_first_line_abt" not in warn, f"Соколов: после смены нет not_first_line ({warn})")

    print("\n[5] CDS order-sign: soft-stop (протокол) + hard-stop (аллергия)")
    pid_p = by_name["Пустова"]
    encs_p = fs.get_encounters(pid_p)
    eid_p = encs_p[0]["id"] if encs_p else ""
    check(bool(eid_p), f"Пустова: encounter ({eid_p})")
    before = {m["id"] for m in fs.get_medications(pid_p, status="active")}
    hdr = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    r = client.post(
        f"/patient/{pid_p}/medication",
        data={
            "encounter_id": eid_p,
            "code": "J01FA10",
            "display": "Азитромицин",
            "dose": "500 мг",
            "frequency": "1 раз в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
            "confirm": "",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(r.status_code == 200, f"Пустова: POST без confirm → {r.status_code}")
    check(data.get("need_confirm") is True, f"Пустова: need_confirm={data.get('need_confirm')}")
    check(data.get("level") == "soft", f"Пустова: level=soft (got {data.get('level')})")
    check(
        any((c.get("category") == "not_first_line_abt") for c in (data.get("cds") or [])),
        "Пустова: cds category not_first_line_abt",
    )
    # Soft-stop обязан назвать протокол: в message и в protocol_label
    soft_cds = [c for c in (data.get("cds") or []) if c.get("category") == "not_first_line_abt"]
    check(
        soft_cds and "ВП (КП №768)" in (soft_cds[0].get("message") or ""),
        "Пустова: soft message называет протокол ВП (КП №768)",
    )
    check(
        soft_cds and soft_cds[0].get("protocol_label") == "ВП (КП №768)",
        f"Пустова: protocol_label для soft-stop (got {soft_cds[0].get('protocol_label') if soft_cds else None})",
    )
    after = {m["id"] for m in fs.get_medications(pid_p, status="active")}
    check(after == before, "Пустова: без confirm АБТ не сохранена")
    # confirm+ack без причины → 400
    r = client.post(
        f"/patient/{pid_p}/medication",
        data={
            "encounter_id": eid_p,
            "code": "J01FA10",
            "display": "Азитромицин",
            "dose": "500 мг",
            "frequency": "1 раз в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
            "confirm": "1",
            "ack": "1",
            "override_reason": "",
        },
        headers=hdr,
    )
    check(r.status_code == 400, f"Пустова: soft confirm+ack без причины → 400 (got {r.status_code})")
    data = r.get_json(silent=True) or {}
    check(data.get("need_confirm") is True, f"Пустова: soft без причины остаётся need_confirm (got {data})")
    after = {m["id"] for m in fs.get_medications(pid_p, status="active")}
    check(after == before, "Пустова: без причины АБТ всё ещё не сохранена")
    # override с причиной
    r = client.post(
        f"/patient/{pid_p}/medication",
        data={
            "encounter_id": eid_p,
            "code": "J01FA10",
            "display": "Азитромицин",
            "dose": "500 мг",
            "frequency": "1 раз в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
            "confirm": "1",
            "ack": "1",
            "override_reason": "Клиническое обоснование",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is True, f"Пустова: soft override → ok=True (got {data})")
    active = fs.get_medications(pid_p, status="active")
    check(any(m["code"].startswith("J01FA") for m in active), "Пустова: после override активен азитромицин")
    med = next(m for m in active if m["code"].startswith("J01FA"))
    check(bool(med.get("cds_override")), "Пустова: cds_override=1 на назначении")
    logs = fs.get_cds_override_logs(pid_p)
    check(any(l.get("severity") == "soft-stop" for l in logs), "Пустова: soft-stop в cds_override_log")
    r = client.get(f"/patient/{pid_p}")
    html = r.data.decode("utf-8", "replace")
    check("осознанно" in html or "override" in html.lower(), "Пустова: в UI виден маркер осознанного назначения")
    verdicts = pdisp.patient_verdicts(pid_p)
    primary = pdisp.pick_primary_assessment(verdicts)
    if primary:
        ui = verdict_for_ui(primary["assessment"], primary["protocol_id"])
        headline = ui.get("headline") or ""
        checks = ui.get("checks") or []
        has_override = "осознанно" in headline.lower() or any(c.get("cds_override") for c in checks)
        check(has_override,
              f"Пустова: вердикт отражает осознанный override (headline={headline!r})")
    # Амокс/клав по протоколу без confirm — на пациенте с факторами риска (Клавуланова)
    pid_k = by_name["Клавуланова"]
    encs_k = fs.get_encounters(pid_k)
    eid_k = encs_k[0]["id"] if encs_k else ""
    r = client.post(
        f"/patient/{pid_k}/medication",
        data={
            "encounter_id": eid_k,
            "code": "J01CR02",
            "display": "Амоксициллин с клавулановой кислотой",
            "dose": "875/125 мг",
            "frequency": "2 раза в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("need_confirm") is not True, "Клавуланова: амокс/клав по протоколу без confirm")
    # Hard-stop (аллергия)
    pid_a = by_name["Аллергова"]
    encs_a = fs.get_encounters(pid_a)
    eid_a = encs_a[0]["id"] if encs_a else ""
    r = client.post(
        f"/patient/{pid_a}/medication",
        data={
            "encounter_id": eid_a,
            "code": "J01CA04",
            "display": "Амоксициллин",
            "dose": "500 мг",
            "frequency": "3 раза в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("level") == "hard", f"Аллергова: hard need_confirm (level={data.get('level')})")
    r = client.post(
        f"/patient/{pid_a}/medication",
        data={
            "encounter_id": eid_a,
            "code": "J01CA04",
            "display": "Амоксициллин",
            "dose": "500 мг",
            "frequency": "3 раза в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
            "confirm": "1",
            "override_reason": "",
        },
        headers=hdr,
    )
    check(r.status_code == 400, f"Аллергова: hard без причины → 400 (got {r.status_code})")
    r = client.post(
        f"/patient/{pid_a}/medication",
        data={
            "encounter_id": eid_a,
            "code": "J01CA04",
            "display": "Амоксициллин",
            "dose": "500 мг",
            "frequency": "3 раза в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
            "confirm": "1",
            "override_reason": "Жизненные показания",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is True, f"Аллергова: hard с причиной → ok=True (got {data})")
    logs = fs.get_cds_override_logs(pid_a)
    check(any(l.get("severity") == "hard-stop" and l.get("reason") for l in logs),
          "Аллергова: hard-stop + reason в cds_override_log")

    print("\n[6] Морозов: закрытие приёма при показании к госпитализации — soft-stop")
    pid_m = by_name["Морозов"]
    r = client.get(f"/patient/{pid_m}")
    html = r.data.decode("utf-8", "replace")
    check("Госпитализировать" in html or "ОРИТ" in html, "Морозов: действие госпитализации/ОРИТ")
    encs_m = [e for e in fs.get_encounters(pid_m) if e.get("status") != "finished" and e.get("class") == "ambulatory"]
    check(bool(encs_m), "Морозов: есть открытый амбулаторный приём")
    eid_m2 = encs_m[0]["id"]
    r = client.post(
        f"/patient/{pid_m}/encounter/{eid_m2}/finish",
        data={},
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("need_confirm") is True, "Морозов: finish без confirm → need_confirm")
    check(data.get("level") == "soft", f"Морозов: level=soft (got {data.get('level')})")
    check(
        any((c.get("category") == "hospitalization_indicated") for c in (data.get("cds") or [])),
        "Морозов: cds category hospitalization_indicated",
    )
    enc = fs.get_encounter(eid_m2)
    check(enc.get("status") != "finished", "Морозов: без confirm приём не закрыт")
    r = client.post(
        f"/patient/{pid_m}/encounter/{eid_m2}/finish",
        data={"confirm": "1", "ack": "1", "override_reason": ""},
        headers=hdr,
    )
    check(r.status_code == 400, f"Морозов: soft без причины → 400 (got {r.status_code})")
    data = r.get_json(silent=True) or {}
    check(data.get("need_confirm") is True, "Морозов: soft без причины остаётся need_confirm")

    print("\n[6.0] Морозов: госпитализация")
    r = client.post(f"/patient/{pid_m}/cap/admit", follow_redirects=False)
    check(r.status_code in (301, 302, 303, 307, 308), f"Морозов: admit → {r.status_code}")
    encs_m2 = [e for e in fs.get_encounters(pid_m) if e.get("class") == "inpatient"]
    check(bool(encs_m2), "Морозов: появился стационарный encounter")

    print("\n[6.1] Новый приём: явный повод — продолжение по диагнозу")
    conds = [c for c in fs.get_conditions(pid_m) if c.get("clinical_status") == "active"]
    check(bool(conds), "Морозов: есть активный диагноз для привязки повода")
    cid_m = conds[0]["id"]
    r = client.post(
        f"/patient/{pid_m}/encounter",
        data={"class": "followup", "reason_condition_ids": cid_m},
        follow_redirects=False,
    )
    check(r.status_code in (301, 302, 303, 307, 308), f"Морозов: открыть контрольный приём → {r.status_code}")
    encs_m3 = sorted(fs.get_encounters(pid_m), key=lambda e: e.get("start") or "", reverse=True)
    new_enc = next((e for e in encs_m3 if e.get("class") == "followup"), None)
    check(new_enc is not None, "Морозов: новый приём создан")
    if new_enc:
        reasons = fs.get_encounter_reasons(new_enc["id"])
        check(cid_m in reasons, "Морозов: encounter_reason связан сразу при открытии")
        r = client.get(f"/patient/{pid_m}?e={new_enc['id']}")
        html = r.data.decode("utf-8", "replace")
        check("Повод" in html or "повод" in html or conds[0].get("display", "") in html,
              "Морозов: «Повод приёма» виден в карточке контрольного визита")

    print("\n[6.2] Жалоба приёма: добавить/изменить после создания")
    r = client.post(f"/patient/{pid_m}/encounter", data={"class": "ambulatory"}, follow_redirects=False)
    check(r.status_code in (301, 302, 303, 307, 308), f"Морозов: открыть приём без жалобы → {r.status_code}")
    encs_m4 = sorted(fs.get_encounters(pid_m), key=lambda e: e.get("start") or "", reverse=True)
    empty_enc = next((e for e in encs_m4 if not e.get("complaint")), None)
    check(empty_enc is not None, "Морозов: создан приём без жалобы")
    if empty_enc:
        r = client.get(f"/patient/{pid_m}?e={empty_enc['id']}")
        html = r.data.decode("utf-8", "replace")
        check("Жалоба" in html, "Морозов: поле жалобы в блоке Жалоба")
        r = client.post(
            f"/patient/{pid_m}/encounter/{empty_enc['id']}/complaint",
            data={"complaint": "Новая жалоба"},
            follow_redirects=False,
        )
        check(r.status_code in (301, 302, 303, 307, 308), f"Морозов: сохранить жалобу → {r.status_code}")
        enc2 = fs.get_encounter(empty_enc["id"])
        check(enc2.get("complaint") == "Новая жалоба", "Морозов: жалоба записана")
        r = client.post(
            f"/patient/{pid_m}/encounter/{empty_enc['id']}/complaint",
            data={"complaint": "Обновлённая жалоба"},
            follow_redirects=False,
        )
        check(r.status_code in (301, 302, 303, 307, 308), f"Морозов: повторно изменить → {r.status_code}")
        enc3 = fs.get_encounter(empty_enc["id"])
        check(enc3.get("complaint") == "Обновлённая жалоба", "Морозов: жалоба перезаписана")

    print("\n[7] Отметить выздоровление — отдельно от закрытия приёма")
    r = client.post(f"/patient/{pid_m}/condition/{cid_m}/resolve", follow_redirects=False)
    check(r.status_code in (301, 302, 303, 307, 308), f"Морозов: resolve → {r.status_code}")
    cond2 = next(c for c in fs.get_conditions(pid_m) if c["id"] == cid_m)
    check(cond2.get("clinical_status") == "resolved", "Морозов: clinical_status=resolved")
    r = client.get(f"/patient/{pid_m}")
    html = r.data.decode("utf-8", "replace")
    check("История диагнозов" in html or "history-fold" in html, "Морозов: закрытый эпизод ушёл в историю")
    check("закрыт" in html, "Морозов: короткий бейдж «закрыт» виден в истории")
    check("Отметить выздоровление" not in html or "resolved" in html.lower(),
          "Морозов: полный смысл статуса — в title/кнопке")
    check(cond2.get("onset_date") in html or cond2.get("recorded_date") in html,
          "Морозов: в истории видна дата начала заболевания")

    print("\n" + "=" * 70)
    print(f"ИТОГ doctor_gate: {PASS} ok, {FAIL} fail")
    print("=" * 70)

    try:
        os.unlink(_TMP.name)
    except OSError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
