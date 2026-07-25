"""
Загрузка и исполнение правил выбора АБТ из docs/protocols/cap_abt_rules.yaml.

Это SSOT для «какую АБТ выбрать». Каталог препаратов (drug_catalog) даёт только
название/дозу/маршрут по ATC-коду; ветвления протокола живут в YAML.
"""
from __future__ import annotations

import os
from functools import lru_cache

import yaml

import rules_engine as re
from terminology import atc_drug_display, atc_group, adult_dose

_RULES_PATH = os.path.join(
    os.path.dirname(__file__), "docs", "protocols", "cap_abt_rules.yaml"
)
_REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "docs", "protocols", "protocol_registry.yaml"
)

DEFAULT_PROTOCOL_ID = "cap_adult_768"


@lru_cache(maxsize=1)
def load_rules() -> dict:
    with open(_RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_protocol_registry() -> dict:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_protocol(protocol_id: str = DEFAULT_PROTOCOL_ID) -> dict | None:
    return (load_protocol_registry().get("protocols") or {}).get(protocol_id)


@lru_cache(maxsize=8)
def protocol_icd_codes(protocol_id: str = DEFAULT_PROTOCOL_ID) -> frozenset[str]:
    proto = get_protocol(protocol_id)
    if not proto:
        raise KeyError(f"Протокол не найден в protocol_registry.yaml: {protocol_id}")
    return frozenset(proto.get("icd_codes") or [])


def protocol_applicable(pid, protocol_id: str = DEFAULT_PROTOCOL_ID) -> bool:
    """Активный диагноз МКБ из реестра протокола (не свободный текст)."""
    import fhir_store as fs

    codes = protocol_icd_codes(protocol_id)
    return any(
        c.get("code") in codes and c.get("clinical_status") == "active"
        for c in fs.get_conditions(pid)
    )


def _facts(pid, severity=None) -> dict:
    allergy = re.betalactam_allergy_type(pid)  # 'ige' / 'non-ige' / None
    return {
        "allergy": allergy or "none",
        "risk_factors": bool(re.antibiotics_in_last_3mo(pid)
                             or re.has_clinical_flag(pid, "hospitalized_3mo")
                             or re.has_chronic_lung_disease(pid)
                             or re.has_diabetes(pid)
                             or re.has_clinical_flag(pid, "immunosuppression")),
        "severity": severity or "mild",
        "aspiration": re.has_aspiration_suspicion(pid),
        "mrsa": re.has_mrsa_suspicion(pid),
        "influenza": re.has_influenza_suspicion(pid),
        "atypical": re.is_atypical(pid),
        "complication": re.has_complication(pid),
        "severe_background": re.has_severe_background(pid),
    }


def _match(when: dict | None, facts: dict) -> bool:
    if not when:
        return True
    for key, expected in when.items():
        if expected in (None, "any"):
            continue
        actual = facts.get(key)
        if key in ("risk_factors", "aspiration", "mrsa", "influenza",
                   "atypical", "complication", "severe_background"):
            want = bool(expected) if not isinstance(expected, bool) else expected
            if bool(actual) != want:
                return False
        else:
            if actual != expected:
                return False
    return True


def _med(code: str, reason: str) -> dict:
    d = adult_dose(code)
    return {
        "atc_code": code,
        "name": atc_drug_display(code),
        "dose": d[1] if d else "",
        "reason": reason,
    }


def select_outpatient(pid) -> dict:
    """Ожидаемая амбулаторная АБТ по YAML-правилам."""
    rules = load_rules()
    facts = _facts(pid)
    rows = sorted(rules.get("outpatient") or [], key=lambda r: r.get("priority", 100))
    for row in rows:
        if _match(row.get("when"), facts):
            code = row["atc_code"]
            d = adult_dose(code)
            grp, _ = atc_group(code)
            return {
                "atc_group": grp,
                "atc_code": code,
                "name": atc_drug_display(code),
                "dose": d[1] if d else "",
                "rationale": row.get("rationale") or "",
                "ref": rules.get("protocol") or "КП №768",
            }
    raise RuntimeError("Нет подходящего outpatient-правила в cap_abt_rules.yaml")


def select_inpatient(pid, severity=None) -> dict:
    """Ожидаемый стационарный режим АБТ по YAML-правилам."""
    rules = load_rules()
    facts = _facts(pid, severity=severity)
    block = rules.get("inpatient") or {}
    primary_rows = sorted(block.get("primary") or [], key=lambda r: r.get("priority", 100))

    primary = None
    addons = []
    for row in primary_rows:
        if _match(row.get("when"), facts):
            primary = _med(row["atc_code"], row.get("rationale") or "")
            for a in row.get("addons") or []:
                addons.append(_med(a["atc_code"], a.get("rationale") or ""))
            break
    if primary is None:
        raise RuntimeError("Нет подходящего inpatient.primary-правила в cap_abt_rules.yaml")

    # Глобальные addons
    have_groups = {atc_group(primary["atc_code"])[0]}
    have_groups.update(atc_group(a["atc_code"])[0] for a in addons)

    for row in block.get("addons") or []:
        if not _match(row.get("when"), facts):
            continue
        skip_grp = row.get("skip_if_group")
        if skip_grp and skip_grp in have_groups:
            continue
        code = row["atc_code"]
        grp, _ = atc_group(code)
        if grp and grp in have_groups:
            continue
        addons.append(_med(code, row.get("rationale") or ""))
        if grp:
            have_groups.add(grp)

    sev_label = "Тяжёлая ВП" if facts["severity"] == "severe" else "ВП нетяжёлая в стационаре"
    return {
        "primary": primary,
        "addons": addons,
        "route": "iv",
        "rationale": f"Стационар ({sev_label}): старт АБТ внутривенно.",
        "ref": rules.get("protocol") or "КП №768",
    }
