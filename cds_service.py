"""
Слой 5 — CDS Hooks (точка оказания помощи).

Хуки:
  patient-view — врач открыл карту. Карточки: АД не контролируется,
                 передержка, ко-морбидность с СД, и сводка соответствия
                 протоколу (из protocol_engine — Слой 3b).
  order-sign   — врач назначает препарат. Проверка через drug_service
                 (Слой 2): hard-stop при противопоказании, suggestion при
                 взаимодействии/дублировании.

CDS использует правила (Слой 3), проверку лекарств (Слой 2) и регламент
(Слой 3b), но сам логику не выдумает — только превращает их вердикты в карточки.
"""
import fhir_store as fs
import rules_engine as re
import drug_service
import protocol_engine


def cds_patient_view(pid):
    cards = []

    # --- АД не контролируется (suggestion) ---
    if re.uncontrolled_bp(pid):
        bp = fs.get_last_bp(pid)
        cards.append({
            "uuid": f"card-bp-{pid}",
            "summary": f"АД не контролируется ({bp['systolic']:.0f}/{bp['diastolic']:.0f} мм рт. ст.)",
            "detail": "По протоколу АГ: при АД ≥ 140/90 на фоне монотерапии — рассмотрите "
                      "добавление 2-го препарата (амлодипин 5 мг или тиазид).",
            "indicator": "warning",
            "source": {"label": "Протокол АГ, ред. 2024"},
            "type": "suggestion",
            "suggestions": [{"label": "Добавить амлодипин 5 мг",
                             "actions": [{"type": "create", "resource": "MedicationRequest"}]}],
        })

    # --- Передержка наблюдения (info) ---
    if re.bp_overdue(pid, days=90):
        bp = fs.get_last_bp(pid)
        summary = (f"Последнее измерение АД — {_days_ago(bp)} дней назад"
                   if bp else "Нет ни одного измерения АД")
        cards.append({
            "uuid": f"card-bp-overdue-{pid}",
            "summary": summary,
            "detail": "Пациент выпал из наблюдения. Контроль АД не реже 1 раза в 3 месяца.",
            "indicator": "info",
            "source": {"label": "Протокол АГ, ред. 2024"},
            "type": "info",
        })

    # --- Ко-морбидность с диабетом (info) ---
    if re.has_diabetes(pid):
        cards.append({
            "uuid": f"card-diabetes-{pid}",
            "summary": "Сопутствующий сахарный диабет — целевое АД < 130/80",
            "detail": "Препараты первого выбора: ингибитор АПФ или сартан (нефропротекция). "
                      "Контроль HbA1c 1 раз в 3 мес.",
            "indicator": "info",
            "source": {"label": "Протокол АГ+СД, ред. 2024"},
            "type": "info",
        })

    # --- Сводка соответствия протоколу (из независимого слоя регламента) ---
    assessment = protocol_engine.evaluate_htn(pid)
    if assessment.get("applicable") and assessment["gaps"]:
        warnings = [g for g in assessment["gaps"] if g["severity"] == "warning"]
        if warnings:
            detail = "\n".join(f"• {g['message']} → {g['recommendation']}" for g in warnings)
            cards.append({
                "uuid": f"card-protocol-{pid}",
                "summary": f"Отклонения от протокола: {len(warnings)}",
                "detail": detail,
                "indicator": "warning",
                "source": {"label": "Регламент АГ (независимая проверка)"},
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


def _days_ago(bp):
    from datetime import datetime, date
    if not bp:
        return 0
    bp_date = datetime.strptime(bp["date"], "%Y-%m-%d").date()
    return (date.today() - bp_date).days
