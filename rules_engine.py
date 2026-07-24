"""
Слой 3 — Движок правил (CQL-like).

Имитирует исполнение CQL-правил. В реальной системе здесь был бы cql-engine.
Single source of truth: одни и те же функции используются и для подсказок
врачу (через cds_service), и для метрик качества.

Ходит в БД только через fhir_store (репозиторий) и db (для прямых запросов
по терминологии). Никогда не открывает соединение с БД «в обход» db.py.

Правила:
1. has_hypertension      — есть ли активная гипертония
2. last_bp               — последнее измерение АД
3. uncontrolled_bp       — АД не контролируется (> 140/90)
4. bp_overdue            — передержка наблюдения (нет измерения > 90 дней)
5. has_diabetes          — ко-морбидность: есть ли диабет (E10-E14)
6. dual_ace_therapy      — дублирование: два ингибитора АПФ одновременно
"""
from datetime import datetime, date, timedelta

import db
from fhir_store import (
    get_condition, get_last_bp, get_all_patients, get_medications, get_last_observation,
)
from terminology import PNEUMONIA_CODES, TEMP_CODE, SPO2_CODE, RR_CODE, HR_CODE, WBC_CODE, CRP_CODE


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
    r = db.fetchone(
        "SELECT 1 FROM condition_ WHERE patient_id = %s "
        "AND code IN ('E10','E11','E12','E13','E14') AND clinical_status = 'active'",
        (pid,),
    )
    return r is not None


# --- Правило 5b: "Ко-морбидность: хроническая болезнь почек" ---
def has_ckd(pid):
    """exists [Condition] C where C.code starts with 'N18'"""
    r = db.fetchone(
        "SELECT 1 FROM condition_ WHERE patient_id = %s "
        "AND code LIKE 'N18%%' AND clinical_status = 'active'",
        (pid,),
    )
    return r is not None


# --- Правило 5c: "Последнее значение лабораторного показателя" ---
def latest_lab(pid, loinc_code):
    """Last(Observation where code=loinc_code) — для СКФ, K+, HbA1c и т.д."""
    return get_last_observation(pid, loinc_code)


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


# ====================================================================
#  Правила для протокола внебольничной пневмонии (КП МЗ РБ №204, дети)
# ====================================================================

def has_pneumonia(pid):
    """exists [Condition] C where C.code in PNEUMONIA_CODES and C.clinical_status='active'"""
    r = db.fetchone(
        "SELECT 1 FROM condition_ WHERE patient_id = %s "
        "AND code IN ('" + "','".join(PNEUMONIA_CODES) + "') AND clinical_status = 'active'",
        (pid,),
    )
    return r is not None


def age_years(pid):
    from fhir_store import get_age
    return get_age(pid)


def age_months(pid):
    """Возраст в полных месяцах — нужен для порогов тахипноэ у детей до 2 лет."""
    from fhir_store import get_patient
    p = get_patient(pid)
    if not p or not p.get("birth_date"):
        return 0
    born = datetime.strptime(p["birth_date"], "%Y-%m-%d").date()
    today = date.today()
    return (today.year - born.year) * 12 + (today.month - born.month) - (today.day < born.day)


def _latest_value(pid, code):
    o = get_last_observation(pid, code)
    return o["value_numeric"] if o else None


def latest_temp(pid):   return _latest_value(pid, TEMP_CODE)
def latest_spo2(pid):   return _latest_value(pid, SPO2_CODE)
def latest_rr(pid):     return _latest_value(pid, RR_CODE)
def latest_hr(pid):     return _latest_value(pid, HR_CODE)
def latest_wbc(pid):    return _latest_value(pid, WBC_CODE)
def latest_crp(pid):    return _latest_value(pid, CRP_CODE)


def has_fever(pid, threshold=38.0):
    """Лихорадка ≥38.0 °C (критерий тяжести и эффективности АБТ)."""
    t = latest_temp(pid)
    return t is not None and t >= threshold


def dn_degree(pid):
    """
    Степень дыхательной недостаточности по SpO2 (приложение к КП №204):
      I   — SpO2 90–94
      II  — SpO2 75–89
      III — SpO2 <75
      None — нет данных или ≥95
    """
    s = latest_spo2(pid)
    if s is None:
        return None
    if s >= 95:
        return 0
    if s >= 90:
        return 1
    if s >= 75:
        return 2
    return 3


def tachypnea_threshold(pid):
    """Порог ЧД для тахипноэ по возрасту (п.26.3 КП №204)."""
    m = age_months(pid)
    y = age_years(pid)
    if m < 2:
        return 60
    if m < 12:          # 2–11 месяцев
        return 50
    if y <= 5:          # 1–5 лет
        return 40
    return 30           # старше 5 лет


def is_tachypneic(pid):
    rr = latest_rr(pid)
    if rr is None:
        return False
    return rr > tachypnea_threshold(pid)


def tachycardia_threshold(pid):
    """Порог ЧСС для тахикардии по возрасту (п.26.4 КП №204)."""
    y = age_years(pid)
    if y < 1:
        return 140
    if y <= 5:
        return 130
    return 120


def is_tachycardic(pid):
    hr = latest_hr(pid)
    if hr is None:
        return False
    return hr > tachycardia_threshold(pid)


def has_chronic_lung_disease(pid):
    """Хронические болезни лёгких — фактор риска резистентности (п.18)."""
    r = db.fetchone(
        "SELECT 1 FROM condition_ WHERE patient_id = %s "
        "AND code LIKE 'J4%%' AND clinical_status = 'active'",
        (pid,),
    )
    return r is not None


def antibiotics_in_last_3mo(pid):
    """
    Была ли антибактериальная терапия в предшествующие 3 месяца —
    фактор риска лекарственно-устойчивых возбудителей (п.16/17).
    Считаем АБТ (J01*) ДО текущего эпизода ВП: с датой раньше onset текущей
    активной пневмонии и в окне 90 дней до него. Текущий назначенный антибиотик
    НЕ учитывается (это лечение данного эпизода, а не «предшествующая» АБТ).
    """
    cond = db.fetchone(
        "SELECT onset_date FROM condition_ WHERE patient_id = %s "
        "AND code IN ('" + "','".join(PNEUMONIA_CODES) + "') "
        "AND clinical_status = 'active' ORDER BY onset_date DESC LIMIT 1",
        (pid,),
    )
    if not cond or not cond.get("onset_date"):
        return False
    onset = _parse_iso(cond["onset_date"])
    cutoff = (onset - timedelta(days=90)).isoformat()
    r = db.fetchone(
        "SELECT 1 FROM medication_request "
        "WHERE patient_id = %s AND code LIKE 'J01%%' "
        "AND date < %s AND date >= %s",
        (pid, cond["onset_date"], cutoff),
    )
    return r is not None


def _parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%d").date()
