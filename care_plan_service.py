"""
Слой 4 — Путь пациента: план лечения, цель, контроль, цикл коррекции.

Реализует цикл «назначил → контроль → сравнил с целью → достиг/не достиг →
коррекция → снова». Это CarePlan + Goal из FHIR, плюс state machine пациента.

Использует репозиторий (fhir_store), правила (rules_engine) и регламент
(protocol_engine) — но сам логики лечения не выдумает, только оркестрирует
переходы состояния и сравнение результата с целью.
"""
import fhir_store as fs
import rules_engine as re
import protocol_engine
from terminology import BP_SYS, TEMP_CODE, SPO2_CODE, RR_CODE, HR_CODE


def create_plan(pid, condition_id=None):
    """Создаёт план лечения и цель по АД (с учётом ко-морбидностей)."""
    if not condition_id:
        cond = fs.get_condition(pid)
        condition_id = cond["id"] if cond else None
    cp_id = fs.add_care_plan(pid, condition_id=condition_id)
    t_sys, t_dia = protocol_engine.target_bp(pid)
    fs.add_goal(pid, cp_id,
                description=f"Контроль АД: ≤ {t_sys}/{t_dia} мм рт. ст.",
                target_metric=BP_SYS, target_value=t_sys, target_unit="mmHg")
    fs.set_pathway(pid, "treatment", "Терапия")
    return cp_id


def evaluate_goal(pid):
    """
    Сравнивает последнее АД с целью. Обновляет статус цели и путь пациента.
    Возвращает {status, bp, target, goal_id}.
    """
    goals = fs.get_goals(pid)  # все цели; берём последнюю (любого статуса)
    bp = fs.get_last_bp(pid)
    if not goals or not bp or bp["systolic"] is None:
        return {"status": "in-progress", "bp": bp, "target": None}

    goal = goals[0]
    t_sys = goal["target_value"]
    t_dia = protocol_engine.target_bp(pid)[1]
    achieved = bp["systolic"] <= t_sys and (bp["diastolic"] is None or bp["diastolic"] <= t_dia)

    if achieved:
        fs.set_goal_status(goal["id"], "achieved")
        fs.set_pathway(pid, "controlled", "Контролируется")
        return {"status": "achieved", "bp": bp, "target": t_sys, "goal_id": goal["id"]}
    else:
        # Не достиг — путь уходит в коррекцию (цикл начнётся заново при следующем назначении)
        fs.set_goal_status(goal["id"], "not-achieved")
        fs.set_pathway(pid, "adjustment", "Коррекция терапии")
        return {"status": "not-achieved", "bp": bp, "target": t_sys, "goal_id": goal["id"]}


def schedule_followup(pid, days=14, practitioner_id=None, reason="Контроль АД"):
    """Создаёт плановый контрольный визит через N дней."""
    from datetime import date, timedelta
    when = (date.today() + timedelta(days=days)).isoformat()
    return fs.add_encounter(pid, practitioner_id=practitioner_id,
                             status="planned", cls="followup", start=when, complaint=reason)


def get_followups(pid):
    return [e for e in fs.get_encounters(pid) if e["status"] == "planned"]


def start_adjustment(pid):
    """Отмечает, что начат цикл коррекции терапии (цель сбрасывается, ставится новая)."""
    # Закрываем старые in-progress цели
    for g in fs.get_goals(pid, status="in-progress"):
        fs.set_goal_status(g["id"], "not-achieved")
    fs.set_pathway(pid, "adjustment", "Коррекция терапии")


# ====================================================================
#  Цикл лечения внебольничной пневмонии (КП МЗ РБ №204)
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
    Сравнивает текущее состояние с целью выздоровления (п.49 КП №204):
      - температура < 38 °C;
      - SpO2 ≥ 95% при дыхании комнатным воздухом;
      - нет тахипноэ (ЧД в пределах возрастной нормы).
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
    """Контрольный визит для оценки эффективности АБТ через 48–72 ч (п.15)."""
    from datetime import date, timedelta
    when = (date.today() + timedelta(days=days)).isoformat()
    return fs.add_encounter(pid, practitioner_id=practitioner_id,
                            status="planned", cls="followup", start=when,
                            complaint="Контроль эффективности АБТ через 48–72 ч (КП №204)")
