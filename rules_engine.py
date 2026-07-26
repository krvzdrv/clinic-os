"""
Слой 3 — Движок правил (CQL-like).

Имитирует исполнение CQL-правил. В реальной системе здесь был бы cql-engine.
Single source of truth: одни и те же функции используются и для подсказок
врачу (через cds_service), и для метрик качества.

Ходит в БД только через fhir_store (репозиторий) и db (для прямых запросов
по терминологии). Никогда не открывает соединение с БД «в обход» db.py.

Все правила относятся к протоколу внебольничной пневмонии (КП МЗ РБ №768, взрослые).
"""
from datetime import datetime, date, timedelta

import db
from fhir_store import (
    get_condition, get_all_patients, get_medications, get_last_observation,
    get_conditions, get_observations,
)
from terminology import (PNEUMONIA_CODES, IDA_CODES, TEMP_CODE, SPO2_CODE, RR_CODE, HR_CODE,
                         WBC_CODE, CRP_CODE, PCT_CODE, SBP_CODE, DBP_CODE,
                         CREAT_CODE, UREA_CODE, HB_CODE,
                         FERRITIN_CODE, IRON_CODE, MCV_CODE, MCH_CODE, MCHC_CODE, TSAT_CODE)


# --- Ко-морбидность: диабет (влияет на выбор АБТ и тяжесть фона) ---
def has_diabetes(pid):
    """exists [Condition] C where C.code in {'E10','E11','E12','E13','E14'}"""
    return any(c.get("code") in ("E10", "E11", "E12", "E13", "E14")
               and c.get("clinical_status") == "active"
               for c in get_conditions(pid))


# --- Ко-морбидность: хроническая болезнь почек ---
def has_ckd(pid):
    """exists [Condition] C where C.code starts with 'N18'"""
    return any((c.get("code") or "").startswith("N18")
               and c.get("clinical_status") == "active"
               for c in get_conditions(pid))


# --- Метрика качества (контекст Population): соответствие протоколу ВП ---
def quality_measure_cap():
    """
    Доля пациентов с активной ВП, у которых протокол ВП выполняется
    (нет warning-уровневых отклонений). Берёт готовые оценки из cap_cache
    (заполняется при открытии карты пациента), чтобы не делать N+1 CAP-расчётов.
    """
    import fhir_store as fs
    total = compliant = with_warnings = 0
    for c in fs.get_all_cap_caches():
        if c.get("applicable"):
            total += 1
            if c.get("compliant"):
                compliant += 1
            else:
                with_warnings += 1
    rate = (compliant / total * 100) if total > 0 else 0
    return {
        "total": total,
        "compliant": compliant,
        "with_warnings": with_warnings,
        "rate": round(rate, 1),
    }


# ====================================================================
#  Правила для протокола внебольничной пневмонии (КП МЗ РБ №768, взрослые)
# ====================================================================

def has_pneumonia(pid):
    """exists [Condition] C where C.code in protocol_registry icd_codes and C.clinical_status='active'"""
    import protocol_rules as pr

    codes = pr.protocol_icd_codes(pr.DEFAULT_PROTOCOL_ID)
    return any(
        c.get("code") in codes and c.get("clinical_status") == "active"
        for c in get_conditions(pid)
    )


# ====================================================================
#  Правила для протокола железодефицитной анемии (КП МЗ РБ №23, взрослые)
# ====================================================================

def has_ida(pid):
    """exists [Condition] C where C.code in protocol_registry('ida_adult_23').icd_codes
    and C.clinical_status='active'"""
    import protocol_rules as pr

    codes = pr.protocol_icd_codes("ida_adult_23")
    return any(
        c.get("code") in codes and c.get("clinical_status") == "active"
        for c in get_conditions(pid)
    )


def age_years(pid):
    from fhir_store import get_age
    return get_age(pid)


def encounter_setting(pid):
    """'inpatient' если есть стационарный приём (любой статус), иначе 'outpatient'.

    Общая для всех протоколов оценка условий лечения — не зависит от диагноза.
    """
    from fhir_store import get_encounters
    for e in get_encounters(pid):
        if e.get("class") in ("inpatient", "program", "day"):
            return "inpatient"
    return "outpatient"


def _latest_value(pid, code):
    o = get_last_observation(pid, code)
    return o["value_numeric"] if o else None


def latest_temp(pid):   return _latest_value(pid, TEMP_CODE)
def latest_spo2(pid):   return _latest_value(pid, SPO2_CODE)
def latest_rr(pid):     return _latest_value(pid, RR_CODE)
def latest_hr(pid):     return _latest_value(pid, HR_CODE)
def latest_wbc(pid):    return _latest_value(pid, WBC_CODE)
def latest_crp(pid):    return _latest_value(pid, CRP_CODE)
def latest_pct(pid):    return _latest_value(pid, PCT_CODE)
def latest_sbp(pid):    return _latest_value(pid, SBP_CODE)
def latest_dbp(pid):    return _latest_value(pid, DBP_CODE)
def latest_creat(pid):  return _latest_value(pid, CREAT_CODE)
def latest_urea(pid):   return _latest_value(pid, UREA_CODE)
def latest_hb(pid):     return _latest_value(pid, HB_CODE)
def latest_ferritin(pid): return _latest_value(pid, FERRITIN_CODE)
def latest_iron(pid):     return _latest_value(pid, IRON_CODE)
def latest_mcv(pid):      return _latest_value(pid, MCV_CODE)
def latest_mch(pid):      return _latest_value(pid, MCH_CODE)
def latest_mchc(pid):     return _latest_value(pid, MCHC_CODE)
def latest_tsat(pid):     return _latest_value(pid, TSAT_CODE)


def _worst_value(pid, code, direction):
    """Самое «острое» значение наблюдения: max для temp/RR/HR/WBC/CRP/creat/urea,
    min для SpO2/SBP/DBP/Hb. Используется для оценки тяжести эпизода ВП
    (тяжесть определяется при поступлении/пике, а не по выписочным значениям)."""
    vals = [o["value_numeric"] for o in get_observations(pid, code)
            if o.get("value_numeric") is not None]
    if not vals:
        return None
    return max(vals) if direction == "max" else min(vals)


def worst_temp(pid):   return _worst_value(pid, TEMP_CODE, "max")
def worst_spo2(pid):   return _worst_value(pid, SPO2_CODE, "min")
def worst_rr(pid):     return _worst_value(pid, RR_CODE, "max")
def worst_hr(pid):     return _worst_value(pid, HR_CODE, "max")
def worst_sbp(pid):    return _worst_value(pid, SBP_CODE, "min")
def worst_dbp(pid):    return _worst_value(pid, DBP_CODE, "min")
def worst_wbc(pid):    return _worst_value(pid, WBC_CODE, "max")
def worst_creat(pid):  return _worst_value(pid, CREAT_CODE, "max")
def worst_urea(pid):   return _worst_value(pid, UREA_CODE, "max")
def worst_hb(pid):     return _worst_value(pid, HB_CODE, "min")
def worst_ferritin(pid): return _worst_value(pid, FERRITIN_CODE, "min")


def crp_history(pid):
    """История СРБ по дате (возр.) — для оценки динамики (снижение = эффект АБТ)."""
    rows = get_observations(pid, CRP_CODE)
    hist = [(r["date"], r["value_numeric"]) for r in rows if r["value_numeric"] is not None]
    hist.sort(key=lambda x: x[0])
    return hist


def crp_decreased(pid):
    """СРБ снизился между двумя измерениями — критерий эффективности АБТ (п.15)."""
    h = crp_history(pid)
    if len(h) < 2:
        return None
    return h[-1][1] < h[0][1]


def has_fever(pid, threshold=38.0):
    """Лихорадка ≥38.0 °C (критерий тяжести и эффективности АБТ)."""
    t = latest_temp(pid)
    return t is not None and t >= threshold


def has_hypothermia(pid):
    """Гипотермия <35.5 °C — критерий госпитализации (КП №768). Оценивается по острому (минимальному) значению."""
    t = worst_temp(pid)
    return t is not None and t < 35.5


def has_high_fever(pid):
    """Лихорадка ≥39.9 °C — критерий госпитализации (КП №768). Оценивается по острому (максимальному) значению."""
    t = worst_temp(pid)
    return t is not None and t >= 39.9


# ---- Взрослые фиксированные пороги (КП №768, взрослое население) ----
# У взрослых нет возрастных групп — пороги единые.

TACHYPNEA_THRESHOLD = 30   # ЧД ≥30/мин — госпитализация / малый критерий тяжёлого течения
TACHYCARDIA_THRESHOLD = 125  # ЧСС ≥125/мин — госпитализация
HOSP_SBP_THRESHOLD = 90    # САД <90 — госпитализация / малый критерий
HOSP_DBP_THRESHOLD = 60    # ДАД ≤60 — госпитализация
HOSP_SPO2_THRESHOLD = 92   # SpO2 <92% — госпитализация
SEVERE_SPO2_THRESHOLD = 90  # SpO2 <90% — малый критерий тяжёлого течения
HOSP_WBC_LOW = 4.0         # лейкоциты <4.0 — госпитализация
HOSP_WBC_HIGH = 20.0       # лейкоциты >20.0 — госпитализация
HOSP_CREAT_THRESHOLD = 176.7  # креатинин >176.7 мкмоль/л — госпитализация
HOSP_UREA_THRESHOLD = 7.0     # мочевина >7.0 ммоль/л — госпитализация
HOSP_HB_THRESHOLD = 90.0      # гемоглобин <90 г/л — госпитализация


def tachypnea_threshold(pid):
    """Порог ЧД для тахипноэ (взрослые, фиксированный — КП №768)."""
    return TACHYPNEA_THRESHOLD


def is_tachypneic(pid):
    rr = worst_rr(pid)
    if rr is None:
        return False
    return rr >= TACHYPNEA_THRESHOLD


def tachycardia_threshold(pid):
    """Порог ЧСС для тахикардии (взрослые, фиксированный — КП №768)."""
    return TACHYCARDIA_THRESHOLD


def is_tachycardic(pid):
    hr = worst_hr(pid)
    if hr is None:
        return False
    return hr >= TACHYCARDIA_THRESHOLD


def has_hypotension(pid):
    """САД <90 мм рт.ст. — малый критерий тяжёлого течения / госпитализация (КП №768). Острое (минимальное) значение."""
    sbp = worst_sbp(pid)
    return sbp is not None and sbp < HOSP_SBP_THRESHOLD


def has_low_dbp(pid):
    """ДАД ≤60 мм рт.ст. — госпитализация (КП №768). Острое (минимальное) значение."""
    dbp = worst_dbp(pid)
    return dbp is not None and dbp <= HOSP_DBP_THRESHOLD


# ---- «Малые» и «большие» критерии тяжёлого течения пневмонии (КП №768) ----

def small_severe_criteria(pid):
    """Список «малых» критериев тяжёлого течения (КП №768).

    Врач констатирует тяжёлое течение при ≥2 «малых» или ≥1 «большом» критерии.
    """
    crit = []
    rr = worst_rr(pid)
    if rr is not None and rr >= TACHYPNEA_THRESHOLD:
        crit.append(f"ЧД {rr} ≥30/мин")
    if has_clinical_flag(pid, "consciousness_disorder"):
        crit.append("нарушение сознания")
    s = worst_spo2(pid)
    if s is not None and s < SEVERE_SPO2_THRESHOLD:
        crit.append(f"SaO2 {s}% (<90%)")
    pao2 = _worst_value(pid, "2703-7", "min")
    if pao2 is not None and pao2 < 60:
        crit.append(f"PaO2 {pao2} мм рт.ст. (<60)")
    if has_hypotension(pid):
        crit.append(f"САД {worst_sbp(pid)} <90 мм рт.ст.")
    if has_clinical_flag(pid, "bilateral_infiltration"):
        crit.append("двустороннее/многоочаговое поражение лёгких")
    if has_clinical_flag(pid, "cavity"):
        crit.append("полости распада")
    if has_clinical_flag(pid, "pleural_effusion"):
        crit.append("плевральный выпот")
    return crit


def large_severe_criteria(pid):
    """Список «больших» критериев тяжёлого течения (КП №768) — показание к ОРИТ."""
    crit = []
    if has_clinical_flag(pid, "shock"):
        crit.append("септический шок / необходимость вазопрессоров ≥4 ч")
    # ИВЛ и быстрое прогрессирование / ОПН — по флагам/данным; упрощённо через shock + urea/creat
    u = worst_urea(pid)
    if u is not None and u > HOSP_UREA_THRESHOLD:
        crit.append(f"мочевина {u} ммоль/л (>7,0) — ОПН")
    cr = worst_creat(pid)
    if cr is not None and cr > HOSP_CREAT_THRESHOLD:
        crit.append(f"креатинин {cr} мкмоль/л (>176,7) — ОПН")
    return crit


def has_chronic_lung_disease(pid):
    """Хронические болезни лёгких — фактор риска резистентности (п.18)."""
    return any((c.get("code") or "").startswith("J4")
               and c.get("clinical_status") == "active"
               for c in get_conditions(pid))


# --- Флаги анамнеза/осмотра/контекста (КП №768) ---

def has_clinical_flag(pid, key, value="true"):
    """Обёртка над fhir_store.has_flag (здесь — чтобы держать все предикаты в одном слое)."""
    from fhir_store import has_flag
    return has_flag(pid, key, value)


def general_condition(pid):
    """Оценка общего состояния врачом при осмотре (clinical_flag категории general_condition).

    Возвращает ключ ('satisfactory'/'moderate'/'mod_severe'/'severe'/'very_severe') или None.
    Берём последнюю запись — общее состояние может меняться в динамике.
    """
    from fhir_store import get_flags
    flags = [f for f in get_flags(pid, "general_condition")]
    if not flags:
        return None
    # recorded_date DESC — берём первую (самую свежую)
    return flags[0].get("key")


def diagnosis_support(pid):
    """Подтверждён ли диагноз данными. Возвращает (has_anamnesis, has_exam).

    Диагноз должен подтверждаться жалобами/анамнезом ИЛИ объективными данными осмотра:
      - анамнез: clinical_flag категории anamnesis/social_risk/context;
      - осмотр: observation с витальным кодом (EXAM_LOINC) ИЛИ clinical_flag категории exam.
    """
    from fhir_store import get_flags, get_observations
    from terminology import EXAM_LOINC
    anam = any(f.get("category") in ("anamnesis", "social_risk", "context")
               for f in get_flags(pid))
    exam = (any(o.get("code") in EXAM_LOINC for o in get_observations(pid))
            or any(f.get("category") == "exam" for f in get_flags(pid)))
    return anam, exam


def has_bronchial_obstruction(pid):
    return has_clinical_flag(pid, "bronchial_obstruction")


def has_local_signs(pid):
    """Локальные/асимметричные аускультативные/перкуторные знаки — показание к R-графии (п.12)."""
    return has_clinical_flag(pid, "local_signs")


def has_aspiration_suspicion(pid):
    return has_clinical_flag(pid, "aspiration_suspicion")


def has_influenza_suspicion(pid):
    return has_clinical_flag(pid, "influenza_suspicion")


def has_mrsa_suspicion(pid):
    return has_clinical_flag(pid, "mrsa_suspicion")


def has_complication(pid):
    """Любое осложнение (плеврит, деструкция) — утяжеляет течение (п.6.5)."""
    return has_clinical_flag(pid, "pleurisy") or has_clinical_flag(pid, "lung_destruction")


def has_emergency_sign(pid):
    """Экстренные/неотложные признаки (п.26.6): судороги, шок, тяжёлая ДН, нарушение сознания."""
    return any(has_clinical_flag(pid, k) for k in
               ("seizures", "shock", "consciousness_disorder"))


def has_severe_background(pid):
    """Тяжёлый фон (п.26.7): иммунодефицит, онкология, глюкокортикоиды, ХБП лёгких, СД, пороки сердца и др."""
    if has_clinical_flag(pid, "immunosuppression") or has_clinical_flag(pid, "glucocorticoids"):
        return True
    if has_chronic_lung_disease(pid) or has_diabetes(pid):
        return True
    # врождённые/приобретённые пороки сердца, онкология — по МКБ-кодам (упрощённо)
    for c in get_conditions(pid):
        code = c.get("code") or ""
        if c.get("clinical_status") == "active" and (
                code.startswith("Q2") or code.startswith("C") or code.startswith("D7")):
            return True
    return False


def betalactam_allergy_type(pid):
    """Тип аллергии на β-лактамы: 'ige' / 'non-ige' / None (п.19 vs п.21)."""
    from fhir_store import betalactam_allergy_type
    return betalactam_allergy_type(pid)


def is_atypical(pid):
    """Атипичная этиология (Mycoplasma, Chlamydia, Legionella) — показание к макролиду (п.33)."""
    return any(c.get("code") in ("J15.7", "J16.0", "A48.1")
               and c.get("clinical_status") == "active"
               for c in get_conditions(pid))


def antibiotics_in_last_3mo(pid):
    """
    Была ли антибактериальная терапия в предшествующие 3 месяца —
    фактор риска лекарственно-устойчивых возбудителей (п.16/17).
    Считаем АБТ (J01*) ДО текущего эпизода ВП: с датой раньше onset текущей
    активной пневмонии и в окне 90 дней до него. Текущий назначенный антибиотик
    НЕ учитывается (это лечение данного эпизода, а не «предшествующая» АБТ).
    """
    cond = None
    for c in get_conditions(pid):
        if c.get("code") in PNEUMONIA_CODES and c.get("clinical_status") == "active":
            cond = c
            break
    if not cond or not cond.get("onset_date"):
        return False
    onset = _parse_iso(cond["onset_date"])
    cutoff = (onset - timedelta(days=90)).isoformat()
    from fhir_store import get_all_medications
    for m in get_all_medications(pid):
        code = m.get("code") or ""
        d = m.get("date")
        if code.startswith("J01") and d and d < cond["onset_date"] and d >= cutoff:
            return True
    return has_clinical_flag(pid, "abt_3mo")


def _parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%d").date()
