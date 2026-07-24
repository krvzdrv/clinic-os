"""
Слой 5 — CDS Hooks сервис (точка оказания помощи).

Имитация CDS Hooks: МИС вызывает сервис по событию (хук),
сервис прогоняет правила и возвращает карточки.
"""
from rules_engine import (
    uncontrolled_bp, bp_overdue, has_diabetes, dual_ace_therapy,
    ace_inhibitor_contraindicated
)
from fhir_store import get_last_bp, get_medications, get_patient

def cds_patient_view(pid):
    """Хук patient-view: врач открыл карту пациента. Возвращает карточки."""
    cards = []

    # --- Карточка 1: АД не контролируется (suggestion) ---
    if uncontrolled_bp(pid):
        bp = get_last_bp(pid)
        cards.append({
            "uuid": f"card-bp-{pid}",
            "summary": f"АД не контролируется ({bp['systolic']}/{bp['diastolic']} мм рт. ст.)",
            "detail": "По протоколу ведения артериальной гипертензии: "
                      "при АД ≥ 140/90 на фоне монотерапии — рассмотрите добавление "
                      "2-го препарата (амлодипин 5 мг или бисопролол 5 мг).",
            "indicator": "warning",
            "source": {"label": "Протокол АГ, ред. 2024"},
            "type": "suggestion",
            "suggestions": [{
                "label": "Добавить амлодипин 5 мг",
                "actions": [{"type": "create", "resource": "MedicationRequest"}],
            }],
        })

    # --- Карточка 2: Передержка наблюдения (info) ---
    if bp_overdue(pid, days=90):
        bp = get_last_bp(pid)
        if bp:
            from datetime import datetime, date
            bp_date = datetime.strptime(bp["date"], "%Y-%m-%d").date()
            days_ago = (date.today() - bp_date).days
            summary = f"Последнее измерение АД — {days_ago} дней назад"
        else:
            summary = "Нет ни одного измерения АД"
        cards.append({
            "uuid": f"card-bp-overdue-{pid}",
            "summary": summary,
            "detail": "Пациент выпал из наблюдения. Рекомендуется контроль АД "
                      "не реже 1 раза в 3 месяца. Запланируйте визит или дистанционный замер.",
            "indicator": "info",
            "source": {"label": "Протокол АГ, ред. 2024"},
            "type": "info",
        })

    # --- Карточка 3: Ко-морбидность с диабетом (info) ---
    if has_diabetes(pid):
        cards.append({
            "uuid": f"card-diabetes-{pid}",
            "summary": "У пациента сопутствующий сахарный диабет",
            "detail": "При сочетании АГ и СД целевое АД — < 130/80 мм рт. ст. "
                      "(а не < 140/90). Препараты первого выбора: ингибиторы АПФ "
                      "или сартаны (нефропротекция). Контроль HbA1c — 1 раз в 3 мес.",
            "indicator": "info",
            "source": {"label": "Протокол АГ+СД, ред. 2024"},
            "type": "info",
        })

    # --- Карточка 4: Дублирование ингибиторов АПФ (warning) ---
    if dual_ace_therapy(pid):
        cards.append({
            "uuid": f"card-dual-ace-{pid}",
            "summary": "Два ингибитора АПФ одновременно",
            "detail": "Назначены два препарата группы C09AA (ингибиторы АПФ). "
                      "Это дублирование — оставьте один препарат. "
                      "Одновременный приём не усиливает эффект, но повышает риск побочных эффектов.",
            "indicator": "warning",
            "source": {"label": "Фармакологический контроль"},
            "type": "suggestion",
            "suggestions": [{
                "label": "Отменить один из препаратов",
                "actions": [{"type": "update", "resource": "MedicationRequest"}],
            }],
        })

    return cards

def cds_order_sign(pid, medication_code):
    """Хук order-sign: врач назначает препарат. Проверяет противопоказания."""
    cards = []

    # Ингибиторы АПФ + женщина фертильного возраста → hard-stop
    if medication_code.startswith("C09AA") and ace_inhibitor_contraindicated(pid):
        cards.append({
            "uuid": f"card-contraindication-{pid}",
            "summary": "Ингибиторы АПФ противопоказаны при беременности",
            "detail": "Назначение ингибитора АПФ женщине фертильного возраста "
                      "требует подтверждения, что беременность исключена. "
                      "Категория D по FDA — доказанный риск для плода.",
            "indicator": "critical",
            "source": {"label": "Противопоказания, ред. 2024"},
            "type": "hard-stop",
            "overrideAction": "Подтвердить, что беременность исключена",
        })

    return cards
