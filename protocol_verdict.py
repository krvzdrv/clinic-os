"""
ClinicalVerdict для UI — обёртка над protocol_cap.evaluate_cap.

Паттерн подсказки (CDS / SaaS):
  1) один сигнал (headline)
  2) одна короткая причина (reason) — без стены виталов
  3) одно действие (cta / форма)
  4) остальное — progressive disclosure («Ещё»)

Сырой assessment сохраняет gaps[].code для scenarios/CDS.
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

_ATC_CODE_RE = re.compile(r"\bJ\d{2}[A-Z]{2}\d{2}\b")
_GROUP_RE = re.compile(r"\s*\(группа\s+J\d{2}[A-Z]{2}\)", re.IGNORECASE)
_KP_TAIL_RE = re.compile(r"\s*\(КП\s.*\)\s*\.?\s*$", re.DOTALL)

# Короткие заголовки проверок — без дампа SpO2/ЧД/АД (они уже в осмотре).
_SHORT_CHECK = {
    "icu_indicated": ("Критерии ОРИТ", "Госпитализировать в ОРИТ"),
    "hospitalization_indicated": ("Показания к госпитализации", "Госпитализировать"),
    "abt_no_effect": ("АБТ без эффекта", "Сменить терапию или госпитализировать"),
    "diagnosis_unsupported": ("Диагноз не подтверждён", "Дополнить осмотр и анамнез"),
    "not_first_line_abt": ("АБТ не по протоколу", None),
    "not_inpatient_first_line": ("Схема АБТ не по протоколу", None),
    "no_abt": ("АБТ не назначена", "Назначить АБТ по протоколу"),
    "oral_in_inpatient": ("В стационаре нужен старт в/в", None),
    "parenteral_in_outpatient": ("Амбулаторно нужна пероральная АБТ", None),
    "bronchodilator_not_indicated": ("Бронхолитик без показаний", "Отменить бронхолитик"),
    "steroid_not_indicated": ("Стероид без показаний", "Отменить стероид"),
    "course_too_short": ("Курс АБТ короче рекомендуемого", "Продлить курс"),
    "dose_too_low": ("Доза АБТ ниже рекомендуемой", "Скорректировать дозу"),
    "dose_too_high": ("Доза АБТ выше рекомендуемой", "Скорректировать дозу"),
    "missing_spo2": ("Нет SpO₂", "Измерить SpO₂"),
    "missing_cbc": ("Нет общего анализа крови", "Назначить ОАК"),
    "missing_crp": ("Нет СРБ", "Назначить СРБ"),
    "cxr_indicated": ("Нужна R-графия ОГК", "Назначить R-графию"),
    "no_repeat_cxr": ("Нет контрольной R-графии", "Запланировать контроль через 4–6 нед"),
    "missing_inpt_studies": ("Нет ОАМ / ЭКГ", "Назначить ОАМ и ЭКГ"),
    "missing_cultures": ("Нет посевов", "Взять посевы до АБТ"),
    "no_reassessment": ("Нет оценки эффекта АБТ", "Оценить через 48–72 ч"),
    "crp_not_decreasing": ("СРБ не снижается", "Пересмотреть терапию"),
    # --- Протокол ЖДА (КП №23, взрослые) — свои коды, без пересечения с ВП ---
    "transfusion_indicated": ("Показания к трансфузии", "Рассмотреть трансфузию эритроцитарной массы"),
    "no_iron_therapy": ("Терапия железом не назначена", "Назначить железо по протоколу"),
    "not_first_line_iron": ("Препарат железа не по протоколу", None),
    "route_mismatch_iron": ("Маршрут введения железа не по протоколу", None),
    "missing_ferritin": ("Нет ферритина", "Назначить ферритин"),
    "missing_iron_serum": ("Нет железа сыворотки", "Назначить железо сыворотки"),
    "missing_biochem": ("Нет биохимии крови", "Назначить биохимический анализ крови"),
    "missing_urine": ("Нет общего анализа мочи", "Назначить ОАМ"),
    "no_hb_reassessment": ("Нет контрольного ОАК", "Оценить через 3–4 нед"),
    "hb_not_normalized": ("Гемоглобин не нормализован", "Продолжить терапию железом"),
    "ferritin_not_replenished": ("Ферритин не восполнен", "Продолжить терапию железом"),
    "no_repeat_cbc_plan": ("Нет плана повторного ОАК", "Запланировать контроль ОАК 1×/мес"),
}

# Коды gap'ов, относящиеся к «терапии не назначена / не первой линии» — для CTA/reason.
_THERAPY_GAP_CODES = ("not_first_line_abt", "not_inpatient_first_line", "no_abt",
                      "not_first_line_iron", "no_iron_therapy")
_NOT_FIRST_LINE_CODES = ("not_first_line_abt", "not_inpatient_first_line", "not_first_line_iron")
_NO_THERAPY_CODES = ("no_abt", "no_iron_therapy")
_CRITICAL_CODES = ("icu_indicated", "transfusion_indicated")


def _clean_ui_text(text: str | None) -> str:
    if not text:
        return ""
    s = _ATC_CODE_RE.sub("", text)
    s = _GROUP_RE.sub("", s)
    idx = s.find("(КП")
    if idx >= 0:
        s = s[:idx].rstrip(" .;")
    s = _KP_TAIL_RE.sub("", s.strip())
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([,.])", r"\1", s)
    return s.strip().rstrip(".")


def _cap_first(text: str) -> str:
    """Первая буква сегмента — заглавная (единый стиль строк UI)."""
    s = text or ""
    for i, ch in enumerate(s):
        if ch.isalpha():
            return s[:i] + ch.upper() + s[i + 1 :]
    return s


def _ui_sentence(text: str | None) -> str:
    """Предложение / CTA для UI: с заглавной; сегменты через «·» — каждый с заглавной."""
    s = (text or "").strip()
    if not s:
        return ""
    parts = re.split(r"(\s*[·;]\s*)", s)
    return "".join(
        p if re.fullmatch(r"\s*[·;]\s*", p or "") else _cap_first(p) for p in parts
    )


def _truncate(text: str, n: int = 90) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1].rstrip(" ;,.") + "…"


def _route_label(atc_code: str | None, setting: str, explicit_route: str | None = None) -> str:
    if explicit_route:
        return _ROUTE_LABELS.get(explicit_route, explicit_route)
    if atc_code:
        dose = adult_dose(atc_code)
        if dose:
            return _ROUTE_LABELS.get(dose[0], dose[0])
    return "внутрь" if setting == "outpatient" else "внутривенно"


def _expected_therapy(expected_regimen: dict | None, setting: str) -> dict:
    """Форма ожидаемой терапии — по структуре dict, не по protocol_id/setting:
    вложенный {'primary': {...}} (стационарный режим ВП) vs плоский {'atc_code','name',...}
    (амбулаторная ВП или терапия железом ЖДА — там нет ветвления по 'setting')."""
    if not expected_regimen:
        return {"title": "", "detail": ""}

    if "primary" in expected_regimen:
        primary = expected_regimen.get("primary") or {}
        name = primary.get("name") or "препарат первой линии"
        route = _route_label(primary.get("atc_code"), setting, expected_regimen.get("route"))
        title = f"{name} {route}".strip()
        detail = _clean_ui_text(
            primary.get("reason") or expected_regimen.get("rationale") or ""
        )
        return {"title": title, "detail": detail}

    name = expected_regimen.get("name") or "препарат первой линии"
    route = _route_label(
        expected_regimen.get("atc_code"), setting, expected_regimen.get("route")
    )
    title = f"{name} {route}".strip()
    detail = _clean_ui_text(expected_regimen.get("rationale") or "")
    return {"title": title, "detail": detail}


def _therapy_next_step(expected_regimen: dict | None, setting: str,
                       protocol_id: str = DEFAULT_PROTOCOL_ID) -> str:
    therapy = _expected_therapy(expected_regimen, setting)
    is_cap = protocol_id == DEFAULT_PROTOCOL_ID
    if not therapy["title"]:
        label = "антибактериальную терапию" if is_cap else "терапию железом"
        return f"Назначить {label} по протоколу"
    if is_cap:
        return f"Назначить {therapy['title']} на 7–14 дней"
    return f"Назначить {therapy['title']}"


def _compact_abt_action(recommendation: str | None, fallback: str | None = None) -> str:
    """Короткий CTA без «Цефтриаксон — цефтриаксон 1–2 г…»."""
    raw = _clean_ui_text(recommendation or "")
    if not raw:
        return fallback or ""
    # «Старт АБТ в/в: Name — dose…» / «Назначить АБТ первой линии: Name — dose…»
    m = re.match(
        r"(?:Старт АБТ\s*(?P<route>в/в|внутрь)?\s*:\s*)?"
        r"(?:Назначить АБТ первой линии:\s*)?"
        r"(?:Назначить\s+)?"
        r"(?P<name>[^—\-:]+?)"
        r"(?:\s*[—\-:]\s*(?P<rest>.+))?$",
        raw,
        re.I,
    )
    if m:
        name = (m.group("name") or "").strip()
        route = (m.group("route") or "").strip()
        rest = (m.group("rest") or "").strip()
        # если name = «АБТ первой линии» — возьмём препарат из rest
        if name.lower().startswith("абт") and rest:
            name = re.split(r"[—\-]", rest, 1)[0].strip()
            rest = ""
        # убрать повтор имени в дозе
        if rest and name and rest.lower().startswith(name.lower()):
            rest = rest[len(name) :].strip(" —-")
        if name and route:
            return f"Назначить {name} {route}"
        if name and re.match(r"^(внутрь|в/в|в/м)\b", name, re.I):
            return f"Назначить препарат {name}"
        if name:
            return f"Назначить {name}"
    if raw.lower().startswith("назначить"):
        return re.split(r"[—\-]", raw, 1)[0].strip()
    return fallback or _truncate(raw, 70)


def _gap_to_check(gap: dict) -> dict:
    """Короткий check для UI — без стены виталов из message."""
    level = "problem" if gap.get("severity") == "warning" else "info"
    code = gap.get("code") or ""
    short = _SHORT_CHECK.get(code)
    if short:
        title, action = short
        if not action:
            action = _clean_ui_text(gap.get("recommendation") or "")
    else:
        title = _truncate(_clean_ui_text(gap.get("message") or ""), 80)
        action = _clean_ui_text(gap.get("recommendation") or "")
    if code in _NO_THERAPY_CODES:
        action = _compact_abt_action(gap.get("recommendation"), action)
    elif code in _NOT_FIRST_LINE_CODES:
        is_iron = code == "not_first_line_iron"
        if gap.get("cds_override"):
            title = ("Препарат железа назначен осознанно вне протокола" if is_iron
                     else "АБТ назначена осознанно вне протокола")
        rec = gap.get("recommendation") or ""
        m = re.search(r"→\s*(.+?)(?:\s*\(|$)", rec)
        if m:
            action = f"Заменить на {m.group(1).strip()}"
        else:
            action = "Заменить препарат железа по протоколу" if is_iron else "Заменить АБТ по протоколу"
    title = _ui_sentence(title)
    action = _ui_sentence(action) if action else action
    # CTA без точки в конце — так читается как действие, не как абзац
    if action:
        action = action.rstrip(".")
    out = {"level": level, "title": title, "action": action, "code": code}
    if gap.get("cds_override"):
        out["cds_override"] = True
    return out


def _pick_next_step(
    checks: list[dict],
    expected_regimen: dict | None,
    setting: str,
    *,
    ok: bool,
    primary_gap: dict | None = None,
    gaps: list[dict] | None = None,
    protocol_id: str = DEFAULT_PROTOCOL_ID,
) -> str | None:
    if primary_gap and gaps and len(gaps) == len(checks):
        for g, c in zip(gaps, checks):
            if g.get("code") == primary_gap.get("code") and c.get("action"):
                return c["action"].rstrip(".")
    for check in checks:
        if check["level"] == "problem" and check.get("action"):
            return check["action"].rstrip(".")
    problems = [c for c in checks if c["level"] == "problem"]
    if problems:
        return problems[0].get("title")
    if ok:
        for check in checks:
            if check["level"] == "info" and check.get("action"):
                return check["action"].rstrip(".")
        return "Продолжить ведение по протоколу"
    if expected_regimen:
        return _therapy_next_step(expected_regimen, setting, protocol_id)
    return None


# UI focus aliases → шаги процесса: см. docs/processes/UI_PROCESS_MAP.md
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
    "no_stepdown": "med",
    "missing_addon": "med",
    # reassess_48_72h: смена АБТ или госпитализация / запланировать контроль
    "abt_no_effect": "reassess",
    "crp_not_decreasing": "reassess",
    "no_reassessment": "reassess",
    "missing_cbc": "diag",
    "missing_crp": "diag",
    "cxr_indicated": "diag",
    "missing_inpt_studies": "diag",
    "missing_cultures": "diag",
    "cxr_local_signs": "diag",
    "no_repeat_cxr": "repeat_cxr",
    "missing_spo2": "exam",
    "missing_temp": "exam",
    "hospitalization_indicated": "actions",
    "icu_indicated": "actions",
    "inpatient_preferable": "actions",
    "diagnosis_unsupported": "cond",
    # --- ЖДА (КП №23) — свои коды ---
    "transfusion_indicated": "actions",
    "no_iron_therapy": "med",
    "not_first_line_iron": "med",
    "route_mismatch_iron": "med",
    "missing_ferritin": "diag",
    "missing_iron_serum": "diag",
    "missing_biochem": "diag",
    "missing_urine": "diag",
    "no_hb_reassessment": "reassess",
    "hb_not_normalized": "reassess",
    "ferritin_not_replenished": "reassess",
}

_CLINICAL_PRIORITY = (
    ("icu_indicated", "Показан перевод в ОРИТ"),
    ("transfusion_indicated", "Показания к трансфузии эритроцитарной массы"),
    ("hospitalization_indicated", "Показана госпитализация"),
    ("abt_no_effect", "АБТ без эффекта — смена терапии или госпитализация"),
    ("diagnosis_unsupported", "Диагноз не подтверждён осмотром и анамнезом"),
    ("not_first_line_abt", "Антибиотик не соответствует протоколу"),
    ("not_inpatient_first_line", "Схема АБТ не соответствует протоколу"),
    ("not_first_line_iron", "Препарат железа не соответствует протоколу"),
    ("no_abt", "Не назначена антибактериальная терапия"),
    ("no_iron_therapy", "Не назначена терапия железом"),
    ("oral_in_inpatient", "В стационаре нужен старт АБТ внутривенно"),
    ("parenteral_in_outpatient", "Амбулаторно нужна пероральная АБТ"),
    ("route_mismatch_iron", "Маршрут введения железа не соответствует протоколу"),
    ("bronchodilator_not_indicated", "Бронхолитик без показаний"),
    ("course_too_short", "Курс АБТ короче рекомендуемого"),
    ("missing_spo2", "Нет SpO₂ — нельзя оценить тяжесть"),
    ("missing_cbc", "Нет общего анализа крови"),
    ("missing_crp", "Нет С-реактивного белка"),
    ("missing_ferritin", "Не определён ферритин"),
    ("missing_iron_serum", "Не определено железо сыворотки"),
)

_PRIMARY_PROBLEMS = 1  # в «Ещё» не дублируем главный сигнал


def _primary_warning(gaps: list[dict]) -> dict | None:
    warns = [g for g in gaps or [] if g.get("severity") == "warning"]
    if not warns:
        return None
    by_code = {g.get("code"): g for g in warns}
    for code, _title in _CLINICAL_PRIORITY:
        if code in by_code:
            return by_code[code]
    return warns[0]


def _focus_stage(gaps: list[dict], primary: dict | None = None) -> str | None:
    if primary:
        stage = _FOCUS_BY_GAP.get(primary.get("code") or "")
        if stage:
            return stage
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


def _clinical_headline(gaps: list[dict], ok: bool, primary: dict | None,
                       protocol_id: str = DEFAULT_PROTOCOL_ID) -> str:
    # Короткое имя протокола в заголовке — у пациента может быть несколько
    # (ВП + ЖДА), «отклонение от протокола» без уточнения путает.
    short = {
        "cap_adult_768": "ВП (КП №768)",
        "ida_adult_23": "ЖДА (КП №23)",
    }.get(protocol_id or "", "")
    proto_suffix = f" · {short}" if short else ""
    if ok:
        return f"Соответствует протоколу{proto_suffix}" if short else "Соответствует протоколу"
    if primary:
        code = primary.get("code")
        if primary.get("cds_override") and code in _NOT_FIRST_LINE_CODES:
            base = ("Препарат железа назначен осознанно вне протокола" if code == "not_first_line_iron"
                    else "АБТ назначена осознанно вне протокола")
            return f"{base}{proto_suffix}" if short else base
        for c, title in _CLINICAL_PRIORITY:
            if c == code:
                return f"{title}{proto_suffix}" if short else title
    return f"Есть отклонения от протокола{proto_suffix}" if short else "Есть отклонения от протокола"


def _short_reason(assessment: dict, primary_gap: dict | None, expected: dict | None, setting: str,
                  protocol_id: str = DEFAULT_PROTOCOL_ID) -> str | None:
    """Одна короткая строка «почему» под заголовком.

    Без дампа критериев/виталов — детали уходят в «Ещё».
    Для ОРИТ/госпитализации/трансфузии reason не нужен: хватает headline + кнопка.
    """
    if not primary_gap:
        return None
    code = primary_gap.get("code") or ""
    if code in _CRITICAL_CODES or code == "hospitalization_indicated":
        return None
    if code in _THERAPY_GAP_CODES:
        if primary_gap.get("cds_override"):
            return "Врач подтвердил назначение при предупреждении CDS"
        return _ui_sentence(_truncate(_therapy_next_step(expected, setting, protocol_id), 100))
    if code == "abt_no_effect":
        return "Нет ответа на текущую АБТ за 48–72 ч"
    if code == "diagnosis_unsupported":
        return "Нет осмотра и анамнеза"
    rec = _clean_ui_text(primary_gap.get("recommendation") or "")
    return _ui_sentence(_truncate(rec, 100)) if rec else None


def _icu_detail_lines(assessment: dict) -> list[str]:
    """Критерии ОРИТ/трансфузии — только для свёрнутого «Ещё», не в шапку карточки."""
    lines = (assessment.get("icu") or assessment.get("transfusion") or [])[:4]
    return [_ui_sentence(x) for x in lines if x]


def _cta_label(
    focus: str | None,
    assessment: dict,
    ok: bool,
    primary_code: str = "",
    protocol_id: str = DEFAULT_PROTOCOL_ID,
) -> str | None:
    if not focus:
        return None
    if ok:
        # Соответствует протоколу — CTA-кнопка не нужна, план контроля уже в next_step.
        return None
    if focus == "repeat_cxr":
        return "Запланировать контроль через 4–6 нед"
    if focus == "actions":
        if assessment.get("icu"):
            return "Госпитализировать в ОРИТ"
        if assessment.get("transfusion"):
            return "Рассмотреть трансфузию"
        return "Госпитализировать"
    if focus == "reassess":
        if primary_code == "no_reassessment":
            return "Запланировать контроль через 3 дня"
        if primary_code == "no_hb_reassessment":
            return "Запланировать контрольный ОАК"
        return "Сменить АБТ"
    if focus == "med":
        is_iron = protocol_id != DEFAULT_PROTOCOL_ID
        if primary_code in _NO_THERAPY_CODES:
            return "Назначить препарат железа" if is_iron else "Назначить АБТ"
        return "Заменить препарат железа" if is_iron else "Заменить АБТ"
    if focus == "cond":
        return "Поставить диагноз"
    if focus == "exam":
        return "Сохранить осмотр"
    if focus == "diag":
        return "Назначить исследование"
    if focus == "anam":
        return "Добавить анамнез"
    return None


def _split_checks(
    checks: list[dict], gaps: list[dict], primary_gap: dict | None
) -> tuple[list[dict], list[dict]]:
    """primary — только главный problem; more — всё остальное (короткие строки)."""
    problems = [c for c in checks if c.get("level") == "problem"]
    infos = [c for c in checks if c.get("level") != "problem"]
    if primary_gap and problems:
        pcode = primary_gap.get("code")
        gap_checks = list(zip(gaps, checks)) if len(gaps) == len(checks) else []
        primary_check = None
        rest_problems = []
        if gap_checks:
            for g, c in gap_checks:
                if c.get("level") != "problem":
                    continue
                if g.get("code") == pcode and primary_check is None:
                    primary_check = c
                else:
                    rest_problems.append(c)
        else:
            primary_check = problems[0]
            rest_problems = problems[1:]
        primary = [primary_check] if primary_check else []
        primary = primary[:_PRIMARY_PROBLEMS]
        more = rest_problems + infos
        return primary, more
    primary = problems[:_PRIMARY_PROBLEMS]
    more = problems[_PRIMARY_PROBLEMS:] + infos
    return primary, more


def verdict_for_ui(assessment: dict, protocol_id: str = DEFAULT_PROTOCOL_ID) -> dict:
    """Преобразует сырой verdict evaluate_cap/evaluate_ida в ClinicalVerdict для шаблона.

    Форма ответа одинакова для любого протокола — шаблон не различает, чей это
    вердикт; конкретный текст берётся из общих таблиц по gap.code (см. выше)."""
    if not assessment.get("applicable"):
        proto = protocol_rules.get_protocol(protocol_id) or {}
        title = proto.get("title")
        headline = f"Протокол «{title}» не активен" if title else "Нет активного протокола"
        return {
            "applicable": False,
            "protocol_title": None,
            "headline": headline,
            "reason": "Нужен диагноз из справочника МКБ, включённый в этот протокол",
            "next_step": "Укажите диагноз из справочника МКБ, входящий в протокол",
            "checks": [],
            "checks_primary": [],
            "checks_more": [],
            "ok": True,
            "focus_stage": "cond",
            "cta_label": "Поставить диагноз",
            "show_therapy": False,
            "suggest_atc": None,
            "suggest_repeat_cxr": False,
            "tier": "info",
        }

    proto = protocol_rules.get_protocol(protocol_id) or {}
    setting = assessment.get("setting") or "outpatient"
    severity = assessment.get("severity") or "mild"
    expected = assessment.get("expected_regimen")
    gaps = assessment.get("gaps") or []
    checks = [_gap_to_check(g) for g in gaps]
    ok = bool(assessment.get("compliant"))
    has_repeat_cxr = any(g.get("code") == "no_repeat_cxr" for g in gaps)
    primary_gap = None if ok else _primary_warning(gaps)
    focus = _focus_stage(gaps, primary_gap) or ("med" if not ok else None)
    # Соответствует протоколу, но ещё нужно запланировать контрольную R-графию.
    if ok and has_repeat_cxr:
        focus = "repeat_cxr"
    primary, more = _split_checks(checks, gaps, primary_gap)

    suggest_atc = None
    if expected:
        if "primary" in expected:
            suggest_atc = (expected.get("primary") or {}).get("atc_code")
        else:
            suggest_atc = expected.get("atc_code")

    primary_code = (primary_gap or {}).get("code") or ""
    reason = None if ok else _short_reason(assessment, primary_gap, expected, setting, protocol_id)
    next_step = _pick_next_step(
        checks, expected, setting, ok=ok, primary_gap=primary_gap, gaps=gaps,
        protocol_id=protocol_id,
    )
    cta = _cta_label(focus, assessment, ok, primary_code=primary_code, protocol_id=protocol_id)

    # Критерии ОРИТ/трансфузии — только в «Ещё», не второй строкой под заголовком.
    if not ok and primary_code in _CRITICAL_CODES:
        for line in _icu_detail_lines(assessment):
            more.append(
                {"level": "info", "title": line, "action": None, "code": "icu_criterion"}
            )

    reason = _ui_sentence(reason) if reason else None
    next_step = _ui_sentence(next_step) if next_step else None
    headline = _ui_sentence(_clinical_headline(gaps, ok, primary_gap, protocol_id))
    if reason and reason.lower() == headline.lower():
        reason = None

    # Дашборд / next_step: действие, не дамп критериев.
    if ok:
        step_for_ui = next_step
    elif primary_code in _CRITICAL_CODES or primary_code == "hospitalization_indicated":
        step_for_ui = cta or next_step
    else:
        step_for_ui = reason or cta or next_step

    tier = "ok" if ok else ("critical" if primary_code in _CRITICAL_CODES else "warn")

    # Предвыбор препарата: лечение и reassess (кроме «ещё не оценили эффект»).
    suggest_med = (
        not ok
        and focus in ("med", "reassess")
        and primary_code not in ("no_reassessment", "no_hb_reassessment")
    )
    # Маршрут — явно из ожидаемого режима (ЖДА: перорально/в/в по факторам, не по setting),
    # иначе — по условиям лечения ВП (амбулаторно = внутрь, стационар = в/в).
    suggest_route = (expected.get("route") if expected else None) or (
        "iv" if setting == "inpatient" else "oral"
    )

    return {
        "applicable": True,
        "protocol_id": protocol_id,
        "protocol_title": proto.get("title") or assessment.get("protocol"),
        "setting_label": _SETTING_LABELS.get(setting, setting),
        "severity_label": _SEVERITY_LABELS.get(severity, severity),
        "ok": ok,
        "tier": tier,
        "headline": headline,
        "reason": reason if not ok else None,
        "next_step": step_for_ui,
        "expected_therapy": {"title": "", "detail": ""},
        "show_therapy": False,
        "suggest_atc": suggest_atc if suggest_med else None,
        "suggest_route": suggest_route if suggest_med else None,
        "suggest_repeat_cxr": bool(has_repeat_cxr),
        # Терапия ещё не назначена вовсе (не «неверный препарат») — кнопка «Назначить», не «Заменить».
        "no_active_therapy": primary_code in _NO_THERAPY_CODES,
        "checks": checks,
        "checks_primary": primary,
        "checks_more": more,
        "focus_stage": focus if (not ok or focus == "repeat_cxr") else None,
        "cta_label": cta,
    }
