"""
ClinicalVerdict для UI — обёртка над protocol_cap.evaluate_cap.

Сырой assessment сохраняет gaps[].code для scenarios/CDS;
verdict_for_ui() отдаёт только поля из docs/agents/verdict-contract.md.
"""
from __future__ import annotations

import re

import protocol_rules
from terminology import adult_dose

DEFAULT_PROTOCOL_ID = "cap_adult_768"

_SETTING_LABELS = {
    "outpatient": "Амбулаторно",
    "inpatient": "Стационар",
}

_SEVERITY_LABELS = {
    "mild": "Нетяжёлая",
    "severe": "Тяжёлая",
}

_ROUTE_LABELS = {
    "oral": "внутрь",
    "iv": "внутривенно",
    "im": "внутримышечно",
}

# ATC-коды, «группа J01…», хвосты вида «(КП №768)» в action/next_step не нужны врачу.
_ATC_CODE_RE = re.compile(r"\bJ\d{2}[A-Z]{2}\d{2}\b")
_GROUP_RE = re.compile(r"\s*\(группа\s+J\d{2}[A-Z]{2}\)", re.IGNORECASE)
# Хвост ссылки на протокол, в т.ч. со вложенными скобками: «(КП … (…))»
_KP_TAIL_RE = re.compile(r"\s*\(КП\s.*\)\s*\.?\s*$", re.DOTALL)


def _clean_ui_text(text: str | None) -> str:
    if not text:
        return ""
    s = _ATC_CODE_RE.sub("", text)
    s = _GROUP_RE.sub("", s)
    # Сначала обрезаем с позиции «(КП» — надёжнее вложенных скобок
    idx = s.find("(КП")
    if idx >= 0:
        s = s[:idx].rstrip(" .;")
    s = _KP_TAIL_RE.sub("", s.strip())
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([,.])", r"\1", s)
    return s.strip().rstrip(".")


def _route_label(atc_code: str | None, setting: str, explicit_route: str | None = None) -> str:
    if explicit_route:
        return _ROUTE_LABELS.get(explicit_route, explicit_route)
    if atc_code:
        dose = adult_dose(atc_code)
        if dose:
            return _ROUTE_LABELS.get(dose[0], dose[0])
    return "внутрь" if setting == "outpatient" else "внутривенно"


def _expected_therapy(expected_regimen: dict | None, setting: str) -> dict:
    if not expected_regimen:
        return {"title": "", "detail": ""}

    if setting == "outpatient":
        name = expected_regimen.get("name") or "антибиотик первой линии"
        route = _route_label(expected_regimen.get("atc_code"), setting)
        title = f"{name} {route}".strip()
        detail = _clean_ui_text(expected_regimen.get("rationale") or "")
        return {"title": title, "detail": detail}

    primary = expected_regimen.get("primary") or {}
    name = primary.get("name") or "антибиотик"
    route = _route_label(primary.get("atc_code"), setting, expected_regimen.get("route"))
    title = f"{name} {route}".strip()
    detail = _clean_ui_text(
        primary.get("reason") or expected_regimen.get("rationale") or ""
    )
    return {"title": title, "detail": detail}


def _therapy_next_step(expected_regimen: dict | None, setting: str) -> str:
    therapy = _expected_therapy(expected_regimen, setting)
    if not therapy["title"]:
        return "Назначить антибактериальную терапию по протоколу."
    return f"Назначить {therapy['title']} на 7–14 дней."


def _gap_to_check(gap: dict) -> dict:
    level = "problem" if gap.get("severity") == "warning" else "info"
    title = _clean_ui_text(gap.get("message") or "")
    action = _clean_ui_text(gap.get("recommendation") or "")
    if action and not action.endswith("."):
        action += "."
    return {"level": level, "title": title, "action": action}


def _pick_next_step(checks: list[dict], expected_regimen: dict | None, setting: str) -> str | None:
    for check in checks:
        if check["level"] == "problem" and check.get("action"):
            return check["action"].rstrip(".")
    problems = [c for c in checks if c["level"] == "problem"]
    if problems:
        return problems[0].get("title")
    if expected_regimen:
        return _therapy_next_step(expected_regimen, setting)
    return None


# Куда вести врача в карточке (якорь этапа). Коды gaps остаются внутри, в UI не светятся.
_FOCUS_BY_GAP = {
    "not_first_line_abt": "med",
    "not_inpatient_first_line": "med",
    "no_abt": "med",
    "course_too_short": "med",
    "dose_too_low": "med",
    "dose_too_high": "med",
    "parenteral_in_outpatient": "med",
    "oral_in_inpatient": "med",
    "bronchodilator_not_indicated": "med",
    "steroid_not_indicated": "med",
    "missing_cbc": "diag",
    "missing_crp": "diag",
    "cxr_indicated": "diag",
    "no_repeat_cxr": "diag",
    "missing_spo2": "exam",
    "hospitalization_indicated": "exam",
    "icu_indicated": "exam",
    "diagnosis_unsupported": "cond",
}


def _focus_stage(gaps: list[dict]) -> str | None:
    for g in gaps or []:
        if g.get("severity") != "warning":
            continue
        stage = _FOCUS_BY_GAP.get(g.get("code") or "")
        if stage:
            return stage
    for g in gaps or []:
        stage = _FOCUS_BY_GAP.get(g.get("code") or "")
        if stage:
            return stage
    return None


def verdict_for_ui(assessment: dict, protocol_id: str = DEFAULT_PROTOCOL_ID) -> dict:
    """Преобразует сырой verdict evaluate_cap в ClinicalVerdict для шаблона."""
    if not assessment.get("applicable"):
        return {
            "applicable": False,
            "protocol_title": None,
            "headline": "Протокол ВП не активен",
            "next_step": "Укажите диагноз внебольничной пневмонии из справочника МКБ.",
            "checks": [],
            "ok": True,
            "focus_stage": "cond",
            "cta_label": "К диагнозу",
        }

    proto = protocol_rules.get_protocol(protocol_id) or {}
    setting = assessment.get("setting") or "outpatient"
    severity = assessment.get("severity") or "mild"
    expected = assessment.get("expected_regimen")
    gaps = assessment.get("gaps") or []
    checks = [_gap_to_check(g) for g in gaps]
    ok = bool(assessment.get("compliant"))
    expected_therapy = _expected_therapy(expected, setting)
    focus = _focus_stage(gaps) or ("med" if not ok else None)

    cta_labels = {
        "med": "К назначениям",
        "diag": "К обследованиям",
        "exam": "К осмотру",
        "cond": "К диагнозу",
        "anam": "К анамнезу",
    }

    headline = (
        "Соответствует протоколу"
        if ok
        else "Есть отклонения от протокола"
    )

    return {
        "applicable": True,
        "protocol_id": protocol_id,
        "protocol_title": proto.get("title") or assessment.get("protocol"),
        "setting_label": _SETTING_LABELS.get(setting, setting),
        "severity_label": _SEVERITY_LABELS.get(severity, severity),
        "ok": ok,
        "headline": headline,
        "next_step": _pick_next_step(checks, expected, setting),
        "expected_therapy": expected_therapy,
        "checks": checks,
        "focus_stage": focus,
        "cta_label": cta_labels.get(focus) if focus and not ok else None,
    }
