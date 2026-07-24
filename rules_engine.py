"""
Слой 3 — Движок правил (CQL-like).

Имитирует исполнение CQL-правил. В реальной системе здесь был бы cql-engine.
Single source of truth: одни и те же функции используются
и для подсказок врачу (через cds_service), и для метрик качества.

Правила:
1. has_hypertension      — есть ли активная гипертония
2. last_bp               — последнее измерение АД
3. uncontrolled_bp       — АД не контролируется (> 140/90)
4. bp_overdue            — передержка наблюдения (нет измерения > 90 дней)
5. has_diabetes          — ко-морбидность: есть ли диабет (E10-E14)
6. dual_ace_therapy      — дублирование: два ингибитора АПФ одновременно
"""
from fhir_store import (
    get_condition, get_last_bp, get_all_patients, get_medications, get_patient
)
from datetime import datetime, date

# --- Правило 1: "Есть ли гипертония" ---
def has_hypertension(pid):
    """exists [Condition] C where C.code = 'I10' and C.clinicalStatus = 'active'"""
    c = get_condition(pid)
    if not c:
        return False
    return c["clinical_status"] == "active" and c["code"] == "I10"

# --- Правило 2: "Последнее АД" ---
def last_bp(pid):
    """Last([Observation] O where O.code = 'BP' sort by date desc)"""
    return get_last_bp(pid)

# --- Правило 3: "АД не контролируется" ---
def uncontrolled_bp(pid):
    """HasHypertension and LastBP is not null and (systolic > 140 or diastolic > 90)"""
    if not has_hypertension(pid):
        return False
    bp = last_bp(pid)
    if not bp:
        return False
    return bp["systolic"] > 140 or bp["diastolic"] > 90

# --- Правило 4: "Передержка наблюдения" ---
def bp_overdue(pid, days=90):
    """Нет измерения АД более 90 дней — пациент выпал из наблюдения."""
    bp = last_bp(pid)
    if not bp:
        return True
    bp_date = datetime.strptime(bp["date"], "%Y-%m-%d").date()
    return (date.today() - bp_date).days > days

# --- Правило 5: "Ко-морбидность: диабет" ---
def has_diabetes(pid):
    """exists [Condition] C where C.code in {'E10','E11','E12','E13','E14'}"""
    conn = None
    try:
        from fhir_store import get_db
        conn = get_db()
        r = conn.execute(
            "SELECT 1 FROM condition_ WHERE patient_id=? AND code IN ('E10','E11','E12','E13','E14') AND clinical_status='active'",
            (pid,)
        ).fetchone()
        return r is not None
    finally:
        if conn:
            conn.close()

# --- Правило 6: "Дублирование ингибиторов АПФ" ---
def dual_ace_therapy(pid):
    """Два активных назначения с кодом C09AA* — ошибка дублирования."""
    meds = get_medications(pid)
    ace = [m for m in meds if m["code"].startswith("C09AA")]
    return len(ace) > 1

# --- Метрика качества (контекст Population) ---
def quality_measure_controlled():
    """
    Доля пациентов с гипертонией, у которых АД < 140/90.
    Тот же код uncontrolled_bp, но для всей популяции.
    """
    patients = get_all_patients()
    total = controlled = overdue = 0
    for p in patients:
        if has_hypertension(p["id"]):
            total += 1
            if not uncontrolled_bp(p["id"]):
                controlled += 1
            if bp_overdue(p["id"]):
                overdue += 1
    rate = (controlled / total * 100) if total > 0 else 0
    return {
        "total": total,
        "controlled": controlled,
        "uncontrolled": total - controlled,
        "overdue": overdue,
        "rate": round(rate, 1),
    }

# --- Проверка противопоказания ---
def ace_inhibitor_contraindicated(pid):
    """Ингибиторы АПФ противопоказаны при беременности (женщина фертильного возраста)."""
    from fhir_store import is_fertile_female
    return is_fertile_female(pid)
