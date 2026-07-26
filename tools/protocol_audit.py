#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сверка Орлов/Б/В с КП №768 + ClinicalVerdict (без техкодов в UI)."""
from __future__ import annotations

import os
import re
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.pop("DATABASE_URL", None)
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["CLINIC_DB"] = path

import db

db.DB_PATH = path

import fhir_store as fs
import protocol_cap as pcap
import protocol_verdict as pv
from _seed_data import seed_all

ATC_RE = re.compile(r"\bJ\d{2}[A-Z]{2}\d{2}\b")
GAP_RE = re.compile(
    r"\b(not_first_line_abt|missing_cbc|missing_crp|no_abt|"
    r"hospitalization_indicated|icu_indicated|cxr_indicated|"
    r"not_inpatient_first_line|course_too_short)\b"
)
FAIL = 0


def check(cond, msg):
    global FAIL
    if cond:
        print(f"  OK    {msg}")
    else:
        print(f"  FAIL  {msg}")
        FAIL += 1


def ui_blob(v: dict) -> str:
    parts = [
        v.get("headline") or "",
        v.get("next_step") or "",
        (v.get("expected_therapy") or {}).get("title") or "",
        (v.get("expected_therapy") or {}).get("detail") or "",
        v.get("cta_label") or "",
    ]
    for c in v.get("checks") or []:
        parts.append(c.get("title") or "")
        parts.append(c.get("action") or "")
    return "\n".join(parts)


def main() -> int:
    fs.init_db()
    seed_all()
    by = {p["family"]: p for p in fs.get_all_patients()}
    print("=" * 70)
    print("PROTOCOL AUDIT — Орлов/Б/В vs КП №768")
    print("=" * 70)

    # --- Орлов ---
    print("\n[Орлов] эталон: нетяжёлая амбулаторно, амоксициллин")
    a = pcap.evaluate_cap(by["Орлов"]["id"])
    va = pv.verdict_for_ui(a)
    check(a.get("applicable") is True, "applicable")
    check(a.get("severity") == "mild", f"severity=mild (got {a.get('severity')})")
    check(a.get("setting") == "outpatient", f"setting=outpatient (got {a.get('setting')})")
    check(a.get("compliant") is True, "compliant=True")
    check(va.get("ok") is True, "verdict.ok=True")
    # Терапию в отдельном блоке не дублируем — для compliant достаточно next_step.
    check(va.get("show_therapy") is False, "compliant: show_therapy=False")
    exp_a = a.get("expected_regimen") or {}
    check(
        "амоксициллин" in (exp_a.get("name") or "").lower()
        or exp_a.get("atc_code") == "J01CA04",
        f"движок: эталон амоксициллин: {exp_a}",
    )
    check(not any(g.get("severity") == "warning" for g in a.get("gaps") or []),
          f"no warning gaps (got {[g.get('code') for g in a.get('gaps') or []]})")
    ns = (va.get("next_step") or "").lower()
    check("назначить амоксициллин" not in ns, f"compliant next_step не «назначить АБТ»: {va.get('next_step')}")
    check(
        "продолжить" in ns or "r-граф" in ns or "рентген" in ns or "контроль" in ns,
        f"compliant next_step про продолжение/контроль: {va.get('next_step')}",
    )
    check(va.get("cta_label") in (None, ""), "compliant без CTA fix")
    check(not ATC_RE.search(ui_blob(va)), "UI без ATC")
    check(not GAP_RE.search(ui_blob(va)), "UI без gap-кодов")

    # --- Соколов ---
    print("\n[Соколов] азитромицин вместо амоксициллина (нет факторов риска)")
    b = pcap.evaluate_cap(by["Соколов"]["id"])
    vb = pv.verdict_for_ui(b)
    check(b.get("applicable") is True, "applicable")
    check(b.get("severity") == "mild", f"severity=mild (got {b.get('severity')})")
    check(b.get("setting") == "outpatient", f"setting=outpatient (got {b.get('setting')})")
    check(b.get("compliant") is False, "compliant=False")
    check(vb.get("ok") is False, "verdict.ok=False")
    codes = [g.get("code") for g in b.get("gaps") or []]
    check("not_first_line_abt" in codes, f"gap not_first_line_abt in {codes}")
    # главная проблема — АБТ, не missing labs
    warn = [g for g in b.get("gaps") or [] if g.get("severity") == "warning"]
    check(any(g.get("code") == "not_first_line_abt" for g in warn), "warning про неверную АБТ")
    check("амоксициллин" in (vb.get("next_step") or "").lower()
          or "амоксициллин" in (vb.get("reason") or "").lower()
          or "амоксициллин" in ui_blob(vb).lower(),
          f"next_step/UI про амоксициллин: {vb.get('next_step')}")
    # Текущий неверный АБТ — в карточке (active_abt), не в тексте ClinicalVerdict.
    meds_b = [m.get("display", "").lower() for m in fs.get_medications(by["Соколов"]["id"], status="active")]
    check(any("азитромицин" in m for m in meds_b), f"активен азитромицин: {meds_b}")
    check("клавулан" not in ui_blob(vb).lower(), "не амокс/клав (нет факторов риска)")
    check(vb.get("focus_stage") == "med", f"focus_stage=med (got {vb.get('focus_stage')})")
    check(vb.get("cta_label") == "Заменить АБТ", f"cta кнопки замены: {vb.get('cta_label')}")
    check(vb.get("suggest_atc") == "J01CA04", f"suggest_atc=J01CA04 (got {vb.get('suggest_atc')})")
    check(not ATC_RE.search(ui_blob(vb)), "UI без ATC")
    check(not GAP_RE.search(ui_blob(vb)), "UI без gap-кодов")
    # YAML: outpatient default → J01CA04
    exp = b.get("expected_regimen") or {}
    check(exp.get("atc_code") == "J01CA04" or (exp.get("name") or "").lower().startswith("амоксициллин"),
          f"expected regimen amoxicillin: {exp}")

    # --- Морозов ---
    print("\n[Морозов] тяжёлая амбулаторно, нет АБТ → госпитализация + ОРИТ критерии")
    c = pcap.evaluate_cap(by["Морозов"]["id"])
    vc = pv.verdict_for_ui(c)
    check(c.get("applicable") is True, "applicable")
    check(c.get("severity") == "severe", f"severity=severe (got {c.get('severity')})")
    check(c.get("compliant") is False, "compliant=False")
    codes_c = [g.get("code") for g in c.get("gaps") or []]
    check("hospitalization_indicated" in codes_c, f"hospitalization in {codes_c}")
    check("no_abt" in codes_c, f"no_abt in {codes_c}")
    check(c.get("hospitalization") is True or "hospitalization_indicated" in codes_c,
          "hospitalization flagged")
    blob_c = ui_blob(vc)
    check(any(x in blob_c.lower() for x in ("госпитал", "орит", "антибиот", "цефтриак")),
          f"UI clinical language: {vc.get('next_step')}")
    check(vc.get("focus_stage") == "actions", f"focus_stage=actions (got {vc.get('focus_stage')})")
    check(vc.get("cta_label") == "Госпитализировать в ОРИТ", f"cta ОРИТ: {vc.get('cta_label')}")
    check(vc.get("tier") == "critical", f"tier=critical (got {vc.get('tier')})")
    check(not ATC_RE.search(blob_c), "UI без ATC")
    check(not GAP_RE.search(blob_c), "UI без gap-кодов")
    # тяжёлая → цефтриаксон III (+ макролид addon)
    exp_c = c.get("expected_regimen") or {}
    primary = exp_c.get("primary") or exp_c
    check(
        (primary.get("atc_code") == "J01DD04")
        or "цефтриаксон" in (primary.get("name") or "").lower()
        or "цефалоспорин" in (vc.get("expected_therapy") or {}).get("title", "").lower(),
        f"expected severe ABT cephalosporin III: {exp_c} / {vc.get('expected_therapy')}",
    )

    # registry
    print("\n[registry]")
    import protocol_rules as pr
    proto = pr.get_protocol("cap_adult_768")
    check(bool(proto), "protocol_registry has cap_adult_768")
    check("768" in (proto.get("title") or "") or "768" in (proto.get("id") or "cap_adult_768"),
          f"title mentions 768: {proto.get('title')}")

    print("\n" + "=" * 70)
    print(f"ИТОГ protocol_audit: {'PASS' if FAIL == 0 else f'{FAIL} FAIL'}")
    print("=" * 70)
    try:
        os.unlink(path)
    except OSError:
        pass
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
