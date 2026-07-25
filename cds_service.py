"""
Слой 5 — CDS Hooks (точка оказания помощи).

Хуки:
  patient-view — врач открыл карту. Карточки: показания к госпитализации/ОРИТ,
                 отсутствие обязательных исследований, и сводка соответствия
                 протоколу ВП (из protocol_cap — Слой 3b).
  order-sign   — врач назначает препарат. Проверка через drug_service
                 (Слой 2): hard-stop при противопоказании, suggestion при
                 взаимодействии/дублировании.

CDS использует правила (Слой 3), проверку лекарств (Слой 2) и регламент
(Слой 3b), но сам логику не выдумает — только превращает их вердикты в карточки.
"""
import fhir_store as fs
import rules_engine as re
import drug_service
import protocol_cap as pcap


def cds_patient_view(pid):
    cards = []

    # --- Сводка соответствия протоколу ВП (независимая проверка) ---
    assessment = pcap.evaluate_cap(pid)
    if assessment.get("applicable"):
        warnings = [g for g in assessment["gaps"] if g["severity"] == "warning"]
        if warnings:
            detail = "\n".join(f"• {g['message']} → {g['recommendation']}" for g in warnings)
            cards.append({
                "uuid": f"card-cap-{pid}",
                "summary": f"Отклонения от протокола ВП: {len(warnings)}",
                "detail": detail,
                "indicator": "warning",
                "source": {"label": "Регламент ВП (КП №768, независимая проверка)"},
                "type": "info",
            })

        # --- Показания к госпитализации ---
        if assessment.get("hospitalization"):
            cards.append({
                "uuid": f"card-cap-hosp-{pid}",
                "summary": "Показания к госпитализации: " + "; ".join(assessment["hospitalization"]),
                "detail": "Госпитализация (КП №768).",
                "indicator": "warning",
                "source": {"label": "Регламент ВП (КП №768)"},
                "type": "suggestion",
            })

        # --- Показания к ОРИТ ---
        if assessment.get("icu"):
            cards.append({
                "uuid": f"card-cap-icu-{pid}",
                "summary": "Показания к переводу в ОРИТ: " + "; ".join(assessment["icu"]),
                "detail": "Перевод в отделение реанимации (КП №768).",
                "indicator": "critical",
                "source": {"label": "Регламент ВП (КП №768)"},
                "type": "suggestion",
            })

        # --- Нет АБТ при диагностированной ВП ---
        if not _has_active_antibiotic(pid):
            exp = assessment.get("expected_regimen", {})
            name = exp.get("name") or (exp.get("primary", {}) or {}).get("name")
            cards.append({
                "uuid": f"card-cap-noabt-{pid}",
                "summary": "ВП диагностирована, АБТ не назначена",
                "detail": f"Назначить АБТ первой линии: {name}" if name else "Назначить АБТ.",
                "indicator": "warning",
                "source": {"label": "Регламент ВП (КП №768)"},
                "type": "suggestion",
            })

    # --- Ко-морбидность с диабетом (info) — влияет на тяжесть фона ---
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
    """Хук order-sign: проверка назначаемого препарата через drug_service."""
    verdict = drug_service.evaluate_medication(pid, medication_code)
    cards = []

    hard_stops = [i for i in verdict["issues"] if i["severity"] == "hard-stop"]
    warnings = [i for i in verdict["issues"] if i["severity"] == "warning"]

    if hard_stops:
        cards.append({
            "uuid": f"card-hardstop-{pid}-{medication_code}",
            "summary": hard_stops[0]["message"],
            "detail": "Назначение требует подтверждения или выбора альтернативы.",
            "indicator": "critical",
            "source": {"label": "Проверка лекарств (drug_service)"},
            "type": "hard-stop",
            "overrideAction": "Подтвердить осознанно",
        })

    if warnings:
        detail = "\n".join(f"• {i['message']}" for i in warnings)
        cards.append({
            "uuid": f"card-warn-{pid}-{medication_code}",
            "summary": f"Предостережения по препарату: {len(warnings)}",
            "detail": detail,
            "indicator": "warning",
            "source": {"label": "Проверка лекарств (drug_service)"},
            "type": "suggestion",
            "suggestions": [{"label": "Назначить с учётом предостережений",
                             "actions": [{"type": "create", "resource": "MedicationRequest"}]}],
        })

    return cards


def _has_active_antibiotic(pid):
    return any(m["code"].startswith("J01") for m in fs.get_medications(pid))
