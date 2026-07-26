"""
Диспетчер клинических протоколов.

Единая точка, через которую app.py / cds_service.py получают список протоколов,
применимых пациенту, и их ClinicalVerdict — вместо жёсткого вызова
protocol_cap.evaluate_cap(pid) один раз на пациента.

Добавление нового протокола = регистрация evaluator в PROTOCOL_EVALUATORS
(+ запись в docs/protocols/protocol_registry.yaml). Существующие evaluate_*
модули (protocol_cap, protocol_anemia) не меняются.
"""
import fhir_store as fs
import protocol_cap
import protocol_anemia
import protocol_rules
import protocol_verdict

PROTOCOL_EVALUATORS = {
    "cap_adult_768": protocol_cap.evaluate_cap,
    "ida_adult_23": protocol_anemia.evaluate_ida,
}

# Реалтайм-проверка выбранного препарата в момент назначения (order-sign),
# симметрично PROTOCOL_EVALUATORS. Каждый evaluate_*_choice сам решает,
# применим ли он (по ATC-префиксу своего класса препарата и активному
# диагнозу) — добавление протокола с новым классом терапии не требует
# правок в app.py/cds_service.py, только регистрации здесь.
DRUG_CHOICE_EVALUATORS = {
    "cap_adult_768": protocol_cap.evaluate_abt_choice,
    "ida_adult_23": protocol_anemia.evaluate_iron_choice,
}

# Короткое имя для UI (soft-stop, заголовки) — не полное title из реестра.
SHORT_PROTOCOL_LABELS = {
    "cap_adult_768": "ВП (КП №768)",
    "ida_adult_23": "ЖДА (КП №23)",
}

# Класс терапии протокола (ATC-префикс) — для строки «Лечение» в карточке диагноза.
THERAPY_ATC_PREFIX = {
    "cap_adult_768": "J01",
    "ida_adult_23": "B03A",
}


def short_protocol_label(protocol_id):
    """«ВП (КП №768)» / «ЖДА (КП №23)» — для титула окна CDS и headline."""
    if not protocol_id:
        return ""
    if protocol_id in SHORT_PROTOCOL_LABELS:
        return SHORT_PROTOCOL_LABELS[protocol_id]
    proto = protocol_rules.get_protocol(protocol_id) or {}
    return proto.get("title") or protocol_id


def evaluate_drug_choice(pid, atc_code):
    """Issues (drug_service-совместимые) от всех зарегистрированных протоколов
    для выбранного препарата — используется в order-sign (см. app.py,
    cds_service.cds_order_sign)."""
    issues = []
    for evaluator in DRUG_CHOICE_EVALUATORS.values():
        issues.extend(evaluator(pid, atc_code) or [])
    return issues


def _primary_condition_id(pid, protocol_id):
    """Первый активный Condition с кодом из этого протокола — к нему привязывается
    вложенная CDS-карточка (см. templates/patient.html verdict_by_condition)."""
    codes = protocol_rules.protocol_icd_codes(protocol_id)
    for c in fs.get_conditions(pid):
        if c.get("code") in codes and c.get("clinical_status") == "active":
            return c.get("id")
    return None


def patient_assessments(pid):
    """Список {protocol_id, condition_id, assessment} по всем applicable протоколам пациента.

    У пациента может быть несколько активных протоколов одновременно
    (напр. ВП + железодефицитная анемия) — каждый оценивается независимо.
    """
    out = []
    for protocol_id in PROTOCOL_EVALUATORS:
        if not protocol_rules.protocol_applicable(pid, protocol_id):
            continue
        assessment = PROTOCOL_EVALUATORS[protocol_id](pid)
        if not assessment.get("applicable"):
            continue
        out.append({
            "protocol_id": protocol_id,
            "condition_id": _primary_condition_id(pid, protocol_id),
            "assessment": assessment,
        })
    return out


def patient_verdicts(pid):
    """Список {protocol_id, condition_id, assessment, verdict} — verdict уже готов для UI."""
    result = []
    for item in patient_assessments(pid):
        verdict = protocol_verdict.verdict_for_ui(item["assessment"], item["protocol_id"])
        result.append({**item, "verdict": verdict})
    return result
