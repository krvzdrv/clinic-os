"""
Загрузка и исполнение правил выбора терапии железом из
docs/protocols/ida_therapy_rules.yaml (КП МЗ РБ №23 от 01.04.2022).

Тот же паттерн, что и protocol_rules.py (АБТ для ВП): YAML — SSOT выбора,
drug_catalog — только справочник название/доза/маршрут.
"""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

import rules_engine as re
from terminology import atc_drug_display, atc_group, adult_dose

_RULES_PATH = os.path.join(
    os.path.dirname(__file__), "docs", "protocols", "ida_therapy_rules.yaml"
)

PROTOCOL_ID = "ida_adult_23"


@lru_cache(maxsize=1)
def load_rules() -> dict:
    with open(_RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _facts(pid) -> dict:
    return {
        "malabsorption": re.has_clinical_flag(pid, "malabsorption"),
        "gi_disease": re.has_clinical_flag(pid, "gi_disease"),
        "intolerance_oral_iron": re.has_clinical_flag(pid, "intolerance_oral_iron"),
    }


def _match(when: dict | None, facts: dict) -> bool:
    if not when:
        return True
    for key, expected in when.items():
        if expected in (None, "any"):
            continue
        actual = facts.get(key)
        want = bool(expected) if not isinstance(expected, bool) else expected
        if bool(actual) != want:
            return False
    return True


def select_therapy(pid) -> dict:
    """Ожидаемая терапия железом по YAML-правилам (первое совпадение)."""
    rules = load_rules()
    facts = _facts(pid)
    rows = sorted(rules.get("therapy") or [], key=lambda r: r.get("priority", 100))
    for row in rows:
        if _match(row.get("when"), facts):
            code = row["atc_code"]
            d = adult_dose(code)
            grp, _ = atc_group(code)
            return {
                "atc_group": grp,
                "atc_code": code,
                "name": atc_drug_display(code),
                "route": row.get("route") or (d[0] if d else "oral"),
                "dose": d[1] if d else "по инструкции, индивидуально",
                "rationale": row.get("rationale") or "",
                "ref": rules.get("protocol") or "КП №23",
            }
    raise RuntimeError("Нет подходящего правила в ida_therapy_rules.yaml")
