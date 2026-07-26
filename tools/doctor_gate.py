#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Doctor gate — с нуля: чистая БД → seed_ten → Flask test_client → пути врача.

Если что-то не работает для врача (без вкладок/ленты) — падаем здесь.
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


def _now(html: str) -> str:
    m = re.search(r'<section id="now-action"[^>]*>.*?</section>', html, re.S)
    return m.group(0) if m else ""


def _verdict(html: str) -> str:
    # Вердикт и форма — один блок #now-action.verdict-panel.
    return _now(html)


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

    print("\n[3] Карточка: путь врача (без вкладок)")
    for name, pid in sorted(by_name.items()):
        r = client.get(f"/patient/{pid}")
        html = r.data.decode("utf-8", "replace")
        check(r.status_code == 200, f"{name}: 200")
        body = _body(html)
        v = _verdict(html)
        now = _now(html)
        check(bool(v), f"{name}: есть verdict-panel")
        check(bool(now), f"{name}: есть #now-action")
        text = v.split("<form")[0] if "<form" in v else v
        check(not ATC_RE.search(text), f"{name}: вердикт без ATC")
        check(not GAP_RE.search(text), f"{name}: вердикт без gap-кодов")
        pos_now = body.find('id="now-action"')
        pos_hist = body.find("history-fold")
        check(0 <= pos_now < pos_hist, f"{name}: now-action выше истории")
        hist_m = re.search(r'<details class="history-fold"([^>]*)>', html)
        # Primary среди всех applicable протоколов (ВП / ЖДА), не только evaluate_cap.
        primary = pdisp.pick_primary_assessment(pdisp.patient_assessments(pid))
        if primary:
            ui = verdict_for_ui(primary["assessment"], primary["protocol_id"])
        else:
            ui = verdict_for_ui(pcap.evaluate_cap(pid))
        # Приём — третьестепенный аккордеон (по умолчанию свёрнут).
        check(bool(hist_m), f"{name}: есть блок приёма")
        check("Приём" in body or "Контрольный визит" in body, f"{name}: словарь приём/контрольный визит")
        check('id="triage-panel"' in html, f"{name}: есть triage-panel")
        check('id="conditions-list"' in html, f"{name}: есть conditions-list")
        if ui.get("ok"):
            check("verdict-ok" in v, f"{name}: verdict-ok")
            # ok → панель скрыта (inline style display:none до JS; data пустой)
        else:
            check("verdict-warn" in v, f"{name}: verdict-warn")
            check('id="triage-data"' in html, f"{name}: triage-data при gap")
            check("К назначениям" not in v and "К госпитализации" not in v,
                  f"{name}: без лишнего CTA-прыжка")
            check("Здесь · без вкладок" not in body, f"{name}: нет дев-блока")
            focus = ui.get("focus_stage")
            if focus == "med":
                check('id="med-code-now"' in now, f"{name}: форма терапии в now-action")
                # «Назначить» — если терапия не назначена вовсе; «Заменить» — если неверная.
                med_verb = "Назначить" if ui.get("no_active_therapy") else "Заменить"
                check(med_verb in now, f"{name}: кнопка {med_verb.lower()}")
                sug = ui.get("suggest_atc")
                if sug:
                    check(
                        f'value="{sug}"' in now and "selected" in now,
                        f"{name}: suggest_atc={sug} предвыбран",
                    )
                route = ui.get("suggest_route")
                if route:
                    check(f'name="route" value="{route}"' in now,
                          f"{name}: suggest_route={route} в now-action")
            if focus == "reassess":
                if ui.get("suggest_atc"):
                    check('id="med-code-now"' in now, f"{name}: reassess — форма АБТ в now")
                    check("Заменить" in now, f"{name}: reassess — смена АБТ")
                    check("Госпитализировать" in now, f"{name}: reassess — госпитализация рядом")
                else:
                    check(
                        "Запланировать контроль" in now or "Контроль" in now,
                        f"{name}: reassess — план контроля 48–72 ч",
                    )
            if focus == "actions":
                check("Госпитализировать" in now, f"{name}: кнопка госпитализации в now")
                if ui.get("tier") == "critical" or (ui.get("cta_label") or "").find("ОРИТ") >= 0:
                    check("ОРИТ" in now, f"{name}: CTA/текст про ОРИТ")
            if focus == "cond":
                check("Поставить диагноз" in now or "МКБ" in now, f"{name}: форма диагноза в now")
            if focus == "anam":
                check("Анамнез" in now or "анамнез" in now.lower(), f"{name}: анамнез в now")
            # CDS: не вываливать дамп виталов в видимый текст подсказки
            text = now.split("<details")[0] if "<details" in now else now
            check("ЧД 32" not in text and "×10" not in text, f"{name}: CDS без стены виталов")

    print("\n[4] Соколов: смена АБТ через now-action (POST)")
    pid_b = by_name["Соколов"]
    r = client.get(f"/patient/{pid_b}")
    html = r.data.decode("utf-8", "replace")
    now = _now(html)
    check('id="med-code-now"' in now, "Соколов: med-code-now")
    check('name="replace_abt"' in now, "Соколов: replace_abt в форме")
    # Текущий АБТ — в status-strip; в action-card только CTA замены (без дубля «Сейчас:»).
    check("Заменить" in now, "Соколов: кнопка замены в now-action")
    check('status-strip' in html and "Азитромицин" in html, "Соколов: текущий АБТ в status-strip")
    eid_m = re.search(r'name="encounter_id"[^>]*value="(e-[a-f0-9]+)"', now)
    eid = eid_m.group(1) if eid_m else ""
    check(bool(eid), f"Соколов: encounter в форме ({eid})")
    # Одна кнопка: replace_abt снимает азитромицин и ставит амоксициллин.
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
    check("Соответствует" in body or "verdict-ok" in body, "Соколов: после замены вердикт обновился на странице")
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
        any(
            (c.get("category") == "not_first_line_abt")
            for c in (data.get("cds") or [])
        ),
        "Пустова: cds category not_first_line_abt",
    )
    # Soft-stop обязан назвать протокол: в message и в protocol_label
    # (титул окна «Отклонение от протокола · ВП (КП №768)»).
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
    # Soft: confirm + ack, но без причины — как и hard-stop, чекбокса
    # недостаточно, нужно письменное обоснование (иначе override
    # бесполезен для аудита протокола).
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
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(r.status_code == 400, f"Пустова: soft confirm+ack без причины → 400 (got {r.status_code})")
    check(data.get("need_confirm") is True and data.get("level") == "soft",
          f"Пустова: soft без причины остаётся need_confirm (got {data})")
    after2 = {m["id"] for m in fs.get_medications(pid_p, status="active")}
    check(after2 == before, "Пустова: без причины АБТ всё ещё не сохранена")
    # Soft: confirm + ack + причина — назначение проходит.
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
            "override_reason": "непереносимость пенициллинов в анамнезе (устно)",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is True, f"Пустова: soft override → ok={data.get('ok')}")
    az = [
        m for m in fs.get_medications(pid_p, status="active")
        if m.get("code") == "J01FA10"
    ]
    check(bool(az), "Пустова: после override активен азитромицин")
    check(bool(az and az[0].get("cds_override")), "Пустова: cds_override=1 на назначении")
    logs = fs.get_cds_override_logs(pid_p)
    check(any(x.get("severity") == "soft-stop" for x in logs), "Пустова: soft-stop в cds_override_log")
    r = client.get(f"/patient/{pid_p}")
    html_p = r.data.decode("utf-8", "replace")
    check("осознанно" in html_p, "Пустова: в UI виден маркер осознанного назначения")
    check(
        "осознанно вне протокола" in html_p or "подтвердил назначение" in html_p,
        "Пустова: вердикт отражает осознанный override",
    )
    # После макролида в анамнезе КП ждёт амокс/клав — без диалога.
    for m in list(fs.get_medications(pid_p, status="active")):
        if (m.get("code") or "").startswith("J01"):
            fs.stop_medication(m["id"])
    r = client.post(
        f"/patient/{pid_p}/medication",
        data={
            "encounter_id": eid_p,
            "code": "J01CR02",
            "display": "Амоксициллин с клавулановой кислотой",
            "dose": "875/125 мг",
            "frequency": "2 раза в день",
            "route": "oral",
            "med_date": "2026-07-25",
            "period_end": "2026-08-01",
            "confirm": "",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is True and not data.get("need_confirm"),
          "Пустова: амокс/клав по протоколу без confirm")

    # Hard-stop: Аллергова + β-лактам без причины → отказ; с причиной → ok + log.
    pid_a = by_name["Аллергова"]
    encs_a = fs.get_encounters(pid_a)
    eid_a = encs_a[0]["id"] if encs_a else ""
    for m in list(fs.get_medications(pid_a, status="active")):
        if (m.get("code") or "").startswith("J01"):
            fs.stop_medication(m["id"])
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
            "confirm": "",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("need_confirm") is True and data.get("level") == "hard",
          f"Аллергова: hard need_confirm (level={data.get('level')})")
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
            "ack": "1",
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
            "ack": "1",
            "override_reason": "Десенсибилизация в стационаре по решению аллерголога",
        },
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(data.get("ok") is True, f"Аллергова: hard с причиной → ok={data.get('ok')}")
    logs_a = fs.get_cds_override_logs(pid_a)
    check(
        any(x.get("severity") == "hard-stop" and x.get("reason") for x in logs_a),
        "Аллергова: hard-stop + reason в cds_override_log",
    )

    print("\n[6] Морозов: закрытие приёма при показании к госпитализации — soft-stop")
    pid_v = by_name["Морозов"]
    r = client.get(f"/patient/{pid_v}")
    now = _now(r.data.decode("utf-8", "replace"))
    check("Госпитализировать" in now, "Морозов: кнопка в now-action")
    open_amb = next(
        (
            e for e in fs.get_encounters(pid_v)
            if e.get("status") != "finished"
            and (e.get("class") or "ambulatory") in ("ambulatory", "followup")
        ),
        None,
    )
    check(bool(open_amb), "Морозов: есть открытый амбулаторный приём")
    eid_v = open_amb["id"]
    before_status = open_amb.get("status")
    r = client.post(
        f"/patient/{pid_v}/encounter/{eid_v}/finish",
        data={"confirm": ""},
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(r.status_code == 200, f"Морозов: finish без confirm → {r.status_code}")
    check(data.get("need_confirm") is True, f"Морозов: need_confirm={data.get('need_confirm')}")
    check(data.get("level") == "soft", f"Морозов: level=soft (got {data.get('level')})")
    check(
        any(
            (c.get("category") == "hospitalization_indicated")
            for c in (data.get("cds") or [])
        ),
        "Морозов: cds category hospitalization_indicated",
    )
    enc_still = fs.get_encounter(eid_v)
    check(
        enc_still and enc_still.get("status") == before_status,
        "Морозов: без confirm приём не закрыт",
    )
    r = client.post(
        f"/patient/{pid_v}/encounter/{eid_v}/finish",
        data={"confirm": "1", "ack": "1"},
        headers=hdr,
    )
    data = r.get_json(silent=True) or {}
    check(r.status_code == 400, f"Морозов: soft без причины → 400 (got {r.status_code})")
    check(
        data.get("need_confirm") is True and data.get("level") == "soft",
        f"Морозов: soft без причины остаётся need_confirm (got {data})",
    )
    # Не закрываем через override — дальше проверяем штатный путь «Госпитализировать».
    print("\n[6.0] Морозов: госпитализация из now-action")
    r = client.post(f"/patient/{pid_v}/cap/admit", follow_redirects=True)
    check(r.status_code == 200, f"Морозов: admit → {r.status_code}")
    encs = fs.get_encounters(pid_v)
    check(
        any(e.get("class") == "inpatient" for e in encs),
        "Морозов: появился стационарный encounter",
    )

    print("\n[6.1] Новый приём: явный повод — продолжение по диагнозу, без повторной постановки (STATUS_SEMANTICS §0)")
    cond_v = fs.get_condition(pid_v)
    check(bool(cond_v), "Морозов: есть активный диагноз для привязки повода")
    enc_ids_before = {e["id"] for e in fs.get_encounters(pid_v)}
    r = client.post(
        f"/patient/{pid_v}/encounter",
        data={"class": "followup", "start": "2026-07-27", "complaint": "Контроль", "reason_condition_ids": cond_v["id"]},
        follow_redirects=True,
    )
    check(r.status_code == 200, f"Морозов: открыть контрольный приём по диагнозу → {r.status_code}")
    new_enc = next((e for e in fs.get_encounters(pid_v) if e["id"] not in enc_ids_before), None)
    check(bool(new_enc), "Морозов: новый приём создан")
    if new_enc:
        reasons = fs.get_encounter_reasons(new_enc["id"])
        check(cond_v["id"] in reasons,
              "Морозов: encounter_reason связан сразу при открытии — диагноз не переставлен повторно")
        html_new = client.get(f"/patient/{pid_v}?e={new_enc['id']}").data.decode("utf-8", "replace")
        check("Повод:" in html_new, "Морозов: «Повод приёма» виден в карточке контрольного визита")

    print("\n[6.2] Жалоба приёма: добавить/изменить и после создания приёма")
    r = client.post(
        f"/patient/{pid_v}/encounter",
        data={"class": "ambulatory", "start": "2026-07-27"},  # без complaint
        follow_redirects=True,
    )
    check(r.status_code == 200, f"Морозов: открыть приём без жалобы → {r.status_code}")
    enc_no_complaint = next(
        (e for e in fs.get_encounters(pid_v)
         if e["id"] not in enc_ids_before and e["id"] != (new_enc or {}).get("id") and not e.get("complaint")),
        None,
    )
    check(bool(enc_no_complaint), "Морозов: создан приём без жалобы")
    if enc_no_complaint:
        html_empty = client.get(f"/patient/{pid_v}?e={enc_no_complaint['id']}").data.decode("utf-8", "replace")
        check("Жалоба не указана" in html_empty, "Морозов: «Жалоба не указана» видна без жалобы")
        r = client.post(
            f"/patient/{pid_v}/encounter/{enc_no_complaint['id']}/complaint",
            data={"complaint": "Кашель, температура 2 дня"},
            follow_redirects=True,
        )
        check(r.status_code == 200, f"Морозов: сохранить жалобу после создания приёма → {r.status_code}")
        enc_after = fs.get_encounter(enc_no_complaint["id"])
        check(enc_after.get("complaint") == "Кашель, температура 2 дня",
              "Морозов: жалоба записана в encounter.complaint")
        html_filled = client.get(f"/patient/{pid_v}?e={enc_no_complaint['id']}").data.decode("utf-8", "replace")
        check("Кашель, температура 2 дня" in html_filled, "Морозов: жалоба видна в карточке приёма")
        # Повторное изменение — редактирование уже заполненной жалобы, не только первый ввод.
        r = client.post(
            f"/patient/{pid_v}/encounter/{enc_no_complaint['id']}/complaint",
            data={"complaint": "Кашель прошёл, жалоб нет"},
            follow_redirects=True,
        )
        check(r.status_code == 200, "Морозов: повторно изменить уже заполненную жалобу → 200")
        enc_edited = fs.get_encounter(enc_no_complaint["id"])
        check(enc_edited.get("complaint") == "Кашель прошёл, жалоб нет",
              "Морозов: жалоба перезаписана, а не продублирована")

    print("\n[7] Отметить выздоровление — отдельно от закрытия приёма (STATUS_SEMANTICS §0)")
    r = client.post(f"/patient/{pid_v}/condition/{cond_v['id']}/resolve", follow_redirects=True)
    check(r.status_code == 200, f"Морозов: resolve → {r.status_code}")
    conds_after = fs.get_conditions(pid_v)
    resolved = next((c for c in conds_after if c["id"] == cond_v["id"]), None)
    check(bool(resolved) and resolved.get("clinical_status") == "resolved",
          "Морозов: clinical_status=resolved после выздоровления")
    html_v = client.get(f"/patient/{pid_v}").data.decode("utf-8", "replace")
    check("История диагнозов" in html_v, "Морозов: закрытый эпизод ушёл в историю")
    check(">закрыт<" in html_v or "badge grey\">закрыт" in html_v,
          "Морозов: короткий бейдж «закрыт» виден в истории")
    check("Выздоровление" in html_v, "Морозов: полный смысл статуса — в title/кнопке")
    if resolved and resolved.get("onset_date"):
        check(resolved["onset_date"] in html_v,
              "Морозов: в истории видна дата начала заболевания")

    print("\n" + "=" * 70)
    print(f"ИТОГ doctor_gate: {PASS} ok, {FAIL} fail")
    print("=" * 70)

    try:
        os.unlink(_TMP.name)
    except OSError:
        pass
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
