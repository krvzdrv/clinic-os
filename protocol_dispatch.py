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
