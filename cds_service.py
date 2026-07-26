"""
Слой 5 — CDS Hooks (точка оказания помощи).

Хуки:
  patient-view — врач открыл карту. Карточки строятся по каждому применимому
                 пациенту протоколу (protocol_dispatch — ВП и/или ЖДА): показания
                 к госпитализации/ОРИТ/трансфузии, отсутствие терапии, сводка
                 соответствия протоколу.
  order-sign   — врач назначает препарат. drug_service (аллергии/взаимодействия)
                 + protocol_cap.evaluate_abt_choice (АБТ не по КП №768 → hard-stop
                 с осознанным подтверждением). Пока проверяет только АБТ/ВП.

Политика сигналов / override / continuous пересчёта:
  docs/processes/CDS_SIGNALING.md (якорь cds_policy в process_registry.yaml).

CDS использует правила (Слой 3), проверку лекарств (Слой 2) и регламент
(Слой 3b), но сам логику не выдумает — только превращает их вердикты в карточки.

Единый источник текста «что не так»: protocol_verdict.verdict_for_ui(assessment).
Карточки CDS не склеивают gap['message']/['recommendation'] заново — иначе
формулировки на дашборде/в карточке пациента и в CDS могут разойтись
(см. docs/processes/CDS_SIGNALING.md).
"""
import fhir_store as fs
import rules_engine as re
import drug_service
import protocol_verdict as pverdict
import protocol_dispatch as pdisp

# Метка источника карточки и класс препарата терапии — по протоколу.
# Добавление протокола = одна строка здесь + регистрация evaluator в protocol_dispatch.
_PROTOCOL_LABELS = {
    "cap_adult_768": "Регламент ВП (КП №768)",
    "ida_adult_23": "Регламент ЖДА (КП №23)",
}
_THERAPY_ATC_PREFIX = {
    "cap_adult_768": "J01",
    "ida_adult_23": "B03A",
}
_THERAPY_WORD = {
    "cap_adult_768": "АБТ",
    "ida_adult_23": "терапию железом",
}


def _protocol_label(protocol_id):
    return _PROTOCOL_LABELS.get(protocol_id, "Регламент протокола")


def cds_patient_view(pid):
    cards = []

    # --- По каждому применимому протоколу: сводка + госпитализация/критическое + терапия ---
    for item in pdisp.patient_assessments(pid):
        protocol_id = item["protocol_id"]
        assessment = item["assessment"]
        label = _protocol_label(protocol_id)
        verdict = pverdict.verdict_for_ui(assessment, protocol_id)

        if not verdict.get("ok"):
            problems = list(verdict.get("checks_primary") or [])
            problems += [c for c in (verdict.get("checks_more") or []) if c.get("level") == "problem"]
            if problems:
                detail = "\n".join(
                    f"• {c['title']}" + (f" → {c['action']}" if c.get("action") else "")
                    for c in problems
                )
                cards.append({
                    "uuid": f"card-{protocol_id}-{pid}",
                    "summary": verdict.get("headline") or f"Отклонения от протокола: {len(problems)}",
                    "detail": detail,
                    "indicator": "critical" if verdict.get("tier") == "critical" else "warning",
                    "source": {"label": f"{label}, единый вердикт"},
                    "type": "info",
                })

        # --- Показания к госпитализации ---
        if assessment.get("hospitalization"):
            cards.append({
                "uuid": f"card-{protocol_id}-hosp-{pid}",
                "summary": "Показания к госпитализации: " + "; ".join(assessment["hospitalization"]),
                "detail": f"Госпитализация ({label}).",
                "indicator": "warning",
                "source": {"label": label},
                "type": "suggestion",
            })

        # --- Показания к ОРИТ (ВП) / трансфузии (ЖДА) — самое острое действие ---
        critical_list = assessment.get("icu") or assessment.get("transfusion")
        if critical_list:
            is_icu = bool(assessment.get("icu"))
            summary_word = "переводу в ОРИТ" if is_icu else "трансфузии эритроцитарной массы"
            detail_text = "Перевод в отделение реанимации" if is_icu else "Трансфузия эритроцитарной массы"
            cards.append({
                "uuid": f"card-{protocol_id}-critical-{pid}",
                "summary": f"Показания к {summary_word}: " + "; ".join(critical_list),
                "detail": f"{detail_text} ({label}).",
                "indicator": "critical",
                "source": {"label": label},
                "type": "suggestion",
            })

        # --- Нет терапии при подтверждённом диагнозе ---
        if not _has_active_therapy(pid, protocol_id):
            exp = assessment.get("expected_regimen") or {}
            name = exp.get("name") or (exp.get("primary", {}) or {}).get("name")
            therapy_word = _THERAPY_WORD.get(protocol_id, "терапию")
            cards.append({
                "uuid": f"card-{protocol_id}-notx-{pid}",
                "summary": f"Диагноз подтверждён, {therapy_word} не назначена",
                "detail": (f"Назначить {therapy_word} первой линии: {name}" if name
                          else f"Назначить {therapy_word}."),
                "indicator": "warning",
                "source": {"label": label},
                "type": "suggestion",
            })

    # --- Ко-морбидность с диабетом (info) — влияет на тяжесть фона ВП ---
    if re.has_diabetes(pid):
        cards.append({
            "uuid": f"card-diabetes-{pid}",
            "summary": "Сопутствующий сахарный диабет — фактор тяжёлого течения",
            "detail": "Учитывается при решении о госпитализации и выборе режима АБТ.",
            "indicator": "info",
            "source": {"label": "Регламент ВП (КП №768)"},
            "type": "info",
        })

    return cards


def cds_order_sign(pid, medication_code):
    """Хук order-sign: drug_service + соответствие препарата протоколу (любому
    применимому — ВП/ЖДА/…, см. protocol_dispatch.evaluate_drug_choice)."""
    verdict = drug_service.evaluate_medication(pid, medication_code)
    issues = list(verdict.get("issues") or [])
    issues.extend(pdisp.evaluate_drug_choice(pid, medication_code))

    cards = []
    hard_stops = [i for i in issues if i["severity"] == "hard-stop"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    if hard_stops:
        cards.append({
            "uuid": f"card-hardstop-{pid}-{medication_code}",
            "summary": hard_stops[0]["message"],
            "detail": "Hard-stop: обязательная текстовая причина назначения.",
            "indicator": "critical",
            "source": {"label": "Проверка лекарств (drug_service)"},
            "type": "hard-stop",
            "overrideAction": "Назначить несмотря на риск",
        })

    if warnings:
        proto_id = next((i.get("protocol_id") for i in warnings if i.get("protocol_id")), None)
        detail = "\n".join(f"• {i['message']}" for i in warnings)
        cards.append({
            "uuid": f"card-warn-{pid}-{medication_code}",
            "summary": (
                warnings[0]["message"] if len(warnings) == 1
                else f"Отклонение от протокола / предостережения: {len(warnings)}"
            ),
            "detail": detail,
            "indicator": "warning",
            "source": {
                "label": (
                    _protocol_label(proto_id) if proto_id
                    else "Проверка лекарств (drug_service)"
                )
            },
            "type": "soft-stop",
            "overrideAction": "Назначить всё равно",
            "suggestions": [{"label": "Назначить с подтверждением отклонения",
                             "actions": [{"type": "create", "resource": "MedicationRequest"}]}],
        })

    return cards


def _has_active_therapy(pid, protocol_id):
    prefix = _THERAPY_ATC_PREFIX.get(protocol_id)
    if not prefix:
        return True  # протокол без смоделированного класса препарата — не сигналим
    return any((m.get("code") or "").upper().startswith(prefix) for m in fs.get_medications(pid))
