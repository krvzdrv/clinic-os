"""
Слой 4 — Путь пациента: план лечения, цель, контроль, цикл коррекции.

Реализует цикл «назначил → контроль → сравнил с целью → достиг/не достиг →
коррекция → снова». Это CarePlan + Goal из FHIR, плюс state machine пациента.

Использует репозиторий (fhir_store), правила (rules_engine) и регламент
(protocol_cap) — но сам логики лечения не выдумает, только оркестрирует
переходы состояния и сравнение результата с целью.

Все функции относятся к протоколу внебольничной пневмонии (КП МЗ РБ №768, взрослые).
"""
import fhir_store as fs
import rules_engine as re
from terminology import TEMP_CODE, SPO2_CODE, RR_CODE, HR_CODE


def get_followups(pid):
    return [e for e in fs.get_encounters(pid) if e["status"] == "planned"]


# ====================================================================
#  Цикл лечения внебольничной пневмонии (КП МЗ РБ №768, взрослые)
# ====================================================================

# Цель выздоровления: афебрильность (t° <38) + SpO2 ≥95% + нет тахипноэ.
# Храним как goal с target_metric = 'cap_recovery' и target_value = 1 (достигнуто/нет).

def create_cap_plan(pid, condition_id=None):
    """Создаёт план лечения ВП и цель «клиническое выздоровление»."""
    if not condition_id:
        cond = fs.get_condition(pid)
        condition_id = cond["id"] if cond else None
    cp_id = fs.add_care_plan(pid, condition_id=condition_id)
    fs.add_goal(
        pid, cp_id,
        description="Клиническое выздоровление: t° <38 °C, SpO2 ≥95%, нет тахипноэ.",
        target_metric="cap_recovery", target_value=1, target_unit="flag",
    )
    fs.set_pathway(pid, "treatment", "Терапия ВП")
    return cp_id


def evaluate_cap_goal(pid):
    """
    Сравнивает текущее состояние с целью выздоровления (КП №768):
      - температура < 38 °C;
      - SpO2 ≥ 95% при дыхании комнатным воздухом;
      - нет тахипноэ (ЧД в пределах нормы).
    Обновляет статус цели и путь пациента. Возвращает {status, ...}.
    """
    goals = fs.get_goals(pid)
    cap_goals = [g for g in goals if g["target_metric"] == "cap_recovery"]
    if not cap_goals:
        return {"status": "in-progress", "reason": "no_cap_goal"}

    goal = cap_goals[0]
    temp = re.latest_temp(pid)
    spo2 = re.latest_spo2(pid)
    rr = re.latest_rr(pid)

    missing = (temp is None) or (spo2 is None) or (rr is None)
    if missing:
        return {"status": "in-progress", "reason": "missing_vitals",
                "temp": temp, "spo2": spo2, "rr": rr}

    afebrile = temp < 38.0
    oxygenated = spo2 >= 95
    not_tachypneic = rr <= re.tachypnea_threshold(pid)
    achieved = afebrile and oxygenated and not_tachypneic

    if achieved:
        fs.set_goal_status(goal["id"], "achieved")
        fs.set_pathway(pid, "controlled", "Выздоровление / контроль")
        return {"status": "achieved", "temp": temp, "spo2": spo2, "rr": rr,
                "goal_id": goal["id"]}
    fs.set_goal_status(goal["id"], "not-achieved")
    fs.set_pathway(pid, "adjustment", "Коррекция терапии ВП")
    return {"status": "not-achieved", "temp": temp, "spo2": spo2, "rr": rr,
            "goal_id": goal["id"],
            "reason": _cap_not_achieved_reason(afebrile, oxygenated, not_tachypneic)}


def _cap_not_achieved_reason(afebrile, oxygenated, not_tachypneic):
    parts = []
    if not afebrile:
        parts.append("сохраняется лихорадка")
    if not oxygenated:
        parts.append("SpO2 < 95%")
    if not not_tachypneic:
        parts.append("тахипноэ")
    return "; ".join(parts) or "не достигнута"


def schedule_cap_followup(pid, days=3, practitioner_id=None):
    """Контрольный визит для оценки эффективности АБТ через 48-72 ч (п.15)."""
    from datetime import date, timedelta
    when = (date.today() + timedelta(days=days)).isoformat()
    return fs.add_encounter(pid, practitioner_id=practitioner_id,
                            status="planned", cls="followup", start=when,
                            complaint="Контроль эффективности АБТ через 48-72 ч (КП N204)")


def admit_inpatient(pid, practitioner_id=None):
    """Открывает стационарный приём (п.26) и ставит путь пациента в 'inpatient'."""
    eid = fs.add_encounter(pid, practitioner_id=practitioner_id,
                           status="in-progress", cls="inpatient",
                           start=None, complaint="Госпитализация по ВП (КП N204, п.26)")
    fs.set_pathway(pid, "inpatient", "Стационарное лечение ВП")
    return eid


def discharge_inpatient(pid, practitioner_id=None):
    """
    Выписка из стационара (п.49): закрывает активный стационарный приём,
    переводит цель в achieved (если критерии выполнены), планирует повторную
    R-графию ОГК через 4-6 нед. Возвращает {discharged, reason}.
    """
    import protocol_cap as pcap
    dc = pcap.discharge_criteria(pid)
    if not dc["met"]:
        return {"discharged": False, "reason": "Критерии выписки не выполнены: " + "; ".join(dc["missing"])}

    # Закрываем стационарные приёмы
    for e in fs.get_encounters(pid):
        if e.get("class") == "inpatient" and e.get("status") == "in-progress":
            fs.finish_encounter(e["id"])

    # Цель выздоровления - achieved
    for g in fs.get_goals(pid):
        if g.get("target_metric") == "cap_recovery" and g.get("status") != "achieved":
            fs.set_goal_status(g["id"], "achieved")

    # Повторная R-графия через 4-6 нед (п.12.3, п.49)
    schedule_repeat_cxr(pid, practitioner_id=practitioner_id)

    fs.set_pathway(pid, "controlled", "Выписка / амбулаторный контроль")
    return {"discharged": True, "reason": "Выписан. Запланирована контрольная R-графия через 4-6 нед."}


def schedule_repeat_cxr(pid, days=35, practitioner_id=None):
    """Плановый контрольный визит с повторной R-графией ОГК через 4-6 нед (п.12.3)."""
    from datetime import date, timedelta
    when = (date.today() + timedelta(days=days)).isoformat()
    eid = fs.add_encounter(pid, practitioner_id=practitioner_id,
                           status="planned", cls="followup", start=when,
                           complaint="Контрольная R-графия ОГК через 4-6 нед (КП N204, п.12.3)")
    fs.add_service_request(pid, code="CXR_REPEAT",
                           display="Повторная рентгенография ОГК (через 4-6 нед)",
                           practitioner_id=practitioner_id, occurrence_date=when)
    return eid
