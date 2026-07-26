#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Quality gate перед передачей демо человеку.
Проверяет: протокол, ClinicalVerdict (без техкодов), сиды Орлов/Б/В, UI-контракт.

Запуск (изолированная SQLite, прод не трогает):
  python3 tools/quality_gate.py
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

os.environ.pop("DATABASE_URL", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import db

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
db.DB_PATH = _TMP.name

import fhir_store as fs
import protocol_cap as pcap
import protocol_verdict as pv
from _seed_data import seed_all

fs.init_db()

ATC_RE = re.compile(r"\bJ\d{2}[A-Z]{2}\d{2}\b")
GAP_CODES = {
    "not_first_line_abt", "no_abt", "hospitalization_indicated", "icu_indicated",
    "missing_cbc", "missing_crp", "missing_spo2", "parenteral_in_outpatient",
    "oral_in_inpatient", "course_too_short", "diagnosis_unsupported",
}


def fail(msg):
    print(f"  FAIL  {msg}")
    return False


def ok(msg):
    print(f"  OK    {msg}")
    return True


def check_no_tech(label, text):
    if text is None:
        return True
    s = str(text)
    bad = []
    if ATC_RE.search(s):
        bad.append("ATC")
    for c in GAP_CODES:
        if c in s:
            bad.append(c)
    if bad:
        return fail(f"{label}: техданные в UI-тексте {bad}: {s[:120]!r}")
    return True


def main():
    passed = failed = 0

    def step(cond, msg):
        nonlocal passed, failed
        if cond:
            ok(msg)
            passed += 1
        else:
            fail(msg)
            failed += 1

    print("=" * 70)
    print("QUALITY GATE — clinic-os sprint 1")
    print(f"DB: {db.DB_PATH}")
    print("=" * 70)

    # 1. Seed demo patients
    print("\n[1] Сид Орлов/Б/В")
    seed_all()
    patients = fs.get_all_patients()
    by_fam = {p["family"]: p for p in patients}
    for fam in ("Орлов", "Соколов", "Морозов"):
        step(fam in by_fam, f"пациент {fam} создан")

    # 2. Protocol + verdict expectations
    print("\n[2] Протокол и ClinicalVerdict")
    expectations = {
        "Орлов": {"applicable": True, "ok": True, "need_substr": []},
        "Соколов": {
            "applicable": True, "ok": False, "need_substr": ["амоксициллин"],
            "next_step_substr": ["амоксициллин", "антибиот"],
        },
        "Морозов": {"applicable": True, "ok": False, "need_substr": []},
    }
    for fam, exp in expectations.items():
        p = by_fam.get(fam)
        if not p:
            step(False, f"{fam}: нет пациента")
            continue
        pid = p["id"]
        cap = pcap.evaluate_cap(pid)
        v = pv.verdict_for_ui(cap)
        step(cap.get("applicable") == exp["applicable"], f"{fam}: applicable={cap.get('applicable')}")
        step(bool(v.get("ok")) == exp["ok"], f"{fam}: verdict.ok={v.get('ok')} (ждали {exp['ok']})")
        step(bool(v.get("headline")), f"{fam}: есть headline")
        step(bool(v.get("next_step")), f"{fam}: есть next_step")
        blob = " ".join([
            str(v.get("headline") or ""),
            str(v.get("next_step") or ""),
            str((v.get("expected_therapy") or {}).get("title") or ""),
            str((v.get("expected_therapy") or {}).get("detail") or ""),
        ] + [f"{c.get('title')} {c.get('action')}" for c in (v.get("checks") or [])])
        tech_ok = check_no_tech(f"{fam} verdict", blob)
        step(tech_ok, f"{fam}: UI-тексты без ATC/gap-кодов")
        for sub in exp["need_substr"]:
            step(sub.lower() in blob.lower(), f"{fam}: в подсказке есть «{sub}»")
        needles = exp.get("next_step_substr") or []
        if needles:
            ns = (v.get("next_step") or "").lower()
            step(any(s in ns for s in needles),
                 f"{fam}: next_step про АБТ (got {v.get('next_step')!r})")
        # raw cap still has codes for engine — fine
        if fam == "Соколов":
            codes = {g["code"] for g in cap.get("gaps", []) if g.get("severity") == "warning"}
            step("not_first_line_abt" in codes, f"{fam}: движок видит not_first_line_abt")
        if fam == "Морозов":
            codes = {g["code"] for g in cap.get("gaps", []) if g.get("severity") == "warning"}
            step(
                "hospitalization_indicated" in codes or "no_abt" in codes,
                f"{fam}: движок: hospitalization/no_abt (есть {sorted(codes)})",
            )

    # 3. Persistence: add observation and re-read
    print("\n[3] Сохранение данных (observation)")
    p = by_fam["Орлов"]
    pid = p["id"]
    encs = fs.get_encounters(pid)
    eid = encs[0]["id"] if encs else None
    before = len(fs.get_observations(pid))
    fs.add_observation(pid, "8867-4", "ЧСС", value_numeric=88, value_unit="bpm",
                       obs_date="2026-07-25", encounter_id=eid)
    after = fs.get_observations(pid)
    step(len(after) == before + 1, f"observation сохранена ({before}→{len(after)})")
    found = [o for o in after if o.get("code") == "8867-4" and float(o.get("value_numeric") or 0) == 88.0]
    step(bool(found), "ЧСС=88 найдена после сохранения")

    # 4. Registry present
    print("\n[4] Реестр протокола")
    import protocol_rules as pr
    reg = pr.load_protocol_registry()
    step("cap_adult_768" in (reg.get("protocols") or reg), "protocol_registry загружается")
    codes = pr.protocol_icd_codes("cap_adult_768")
    step("J18.9" in codes, "J18.9 в inclusion-наборе")

    # 5. Template structure smoke (static file checks)
    print("\n[5] Структура UI (статическая)")
    html_path = os.path.join(REPO, "templates", "patient.html")
    html = open(html_path, encoding="utf-8").read()
    step("verdict-panel" in html, "CSS/класс verdict-panel")
    step('id="now-action"' in html, "один экран #now-action")
    step("history-fold" in html, "история свёрнута в history-fold")
    step("suggest_atc" in html, "предвыбор препарата (suggest_atc)")
    step("Осмотр" in html and "Диагноз" in html and "Лечение" in html,
         "группы Осмотр / Диагноз / Лечение")
    step("К назначениям" not in html.split("verdict-work")[0] if "verdict-work" in html else True,
         "в вердикте нет CTA «К назначениям»")
    # should not display gap codes as visible labels in verdict section
    step("g.code" not in html.split("verdict-panel")[1][:2000] if "verdict-panel" in html else True,
         "в вердикте нет вывода g.code")

    # 6. Напоминание: полный путь врача — doctor_gate (с нуля)
    print("\n[6] Напоминание")
    step(os.path.isfile(os.path.join(REPO, "tools", "doctor_gate.py")),
         "tools/doctor_gate.py существует (полный прогон с нуля)")

    print("\n" + "=" * 70)
    print(f"ИТОГ quality_gate: {passed} ok, {failed} fail")
    print("=" * 70)

    try:
        os.unlink(_TMP.name)
    except OSError:
        pass
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
