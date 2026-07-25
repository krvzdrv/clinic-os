#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Прогон сценариев протокола ВП (КП МЗ РБ №768, взрослые) end-to-end на изолированной SQLite-БД.

Каждый сценарий:
  1) строит пациента (анамнез/осмотр/наблюдения/назначения) через fhir_store;
  2) вызывает protocol_cap.evaluate_cap(pid);
  3) сверяет verdict (setting/severity/compliant/коды gap'ов) с ожиданиями.

Изоляция: DATABASE_URL убирается из окружения, DB_PATH указывает на временный файл,
таблицы создаются с нуля. Прод-БД (Supabase) не трогается.

Запуск:
  python3 tools/scenarios.py
  python3 tools/scenarios.py -v   # подробный вывод по каждому gap
"""
import os
import sys
import tempfile
import argparse
from datetime import date, timedelta

# Изоляция от прод-БД: работаем только на локальном SQLite.
os.environ.pop("DATABASE_URL", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import db  # noqa: E402

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
db.DB_PATH = _TMP_DB.name

import fhir_store as fs  # noqa: E402
import protocol_cap as pcap  # noqa: E402
from terminology import (  # noqa: E402
    TEMP_CODE, SPO2_CODE, RR_CODE, HR_CODE, WBC_CODE, CRP_CODE,
)

fs.init_db()

PN_CODE = "J18.9"
PN_DISP = "Пневмония неуточненная"

AMOX = "J01CA04"
AMOX_CLAV = "J01CR02"
AZITHRO = "J01FA10"
CEFTRIAXONE = "J01DD04"
VANCOMYCIN = "J01XA01"
LINEZOLID = "J01XX08"
MEROPENEM = "J01DH02"
METRONIDAZOLE = "J01XD01"
SALBUTAMOL = "R03AC02"


def _years_ago(n):
    t = date.today()
    try:
        return date(t.year - n, t.month, t.day).isoformat()
    except ValueError:
        return date(t.year - n, t.month, 28).isoformat()


def _days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


def _days_ahead(n):
    return (date.today() + timedelta(days=n)).isoformat()


def _warning_codes(verdict):
    return [g["code"] for g in verdict.get("gaps", []) if g["severity"] == "warning"]


def make_patient(age_years, gender="male", family="Тестов"):
    return fs.add_patient(family, f"Пациент{age_years}", "Тестович", gender, _years_ago(age_years))


def add_pneumonia(pid, onset_days_ago=0, code=PN_CODE):
    return fs.add_condition(pid, code, PN_DISP, onset_date=_days_ago(onset_days_ago))


def add_encounter(pid, cls="ambulatory", status="in-progress", start_days_ago=0):
    return fs.add_encounter(pid, status=status, cls=cls, start=_days_ago(start_days_ago))


def obs(pid, code, value, unit, days_ago=0, display=None):
    fs.add_observation(pid, code, display or code, value_numeric=value,
                       value_unit=unit, obs_date=_days_ago(days_ago))


def med(pid, code, route="oral", start_days_ago=0, duration_days=None, status="active"):
    end = _days_ahead(duration_days) if duration_days is not None else None
    fs.add_medication(pid, code, code, route=route,
                      period_start=_days_ago(start_days_ago),
                      period_end=end, med_date=_days_ago(start_days_ago),
                      status=status)


def req(pid, code, days_ago=0, status="active"):
    fs.add_service_request(pid, code, code, occurrence_date=_days_ago(days_ago), status=status)


def flag(pid, key, value="true"):
    fs.add_flag(pid, key, value)


def allergy(pid, code="penicillin", display="Пенициллин", reaction_type="unknown"):
    fs.add_allergy(pid, code, display, reaction_type=reaction_type)


# ====================================================================
#  Сценарии (взрослые, КП №768)
# ====================================================================

def s_positive_outpatient_mild():
    """P1: амбулаторно, нетяжёлая, без факторов риска, амоксициллин per os."""
    pid = make_patient(40)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    obs(pid, TEMP_CODE, 38.5, "C", display="Температура")
    obs(pid, SPO2_CODE, 96, "%", display="SpO2")
    obs(pid, RR_CODE, 22, "/min", display="ЧД")
    obs(pid, HR_CODE, 90, "bpm", display="ЧСС")
    obs(pid, WBC_CODE, 9.0, "10^9/L", display="Лейкоциты")
    obs(pid, CRP_CODE, 12, "mg/L", display="СРБ")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "setting": "outpatient", "severity": "mild",
            "compliant": True, "warn_not_in": ["no_abt", "not_first_line_abt",
            "parenteral_in_outpatient", "missing_cbc", "missing_crp", "missing_spo2",
            "hospitalization_indicated", "course_too_short"]}


def s_positive_outpatient_risk_clavulanate():
    """P2: фактор риска (АБТ 3 мес) → амокс/клавуланат."""
    pid = make_patient(45)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    flag(pid, "abt_3mo")
    obs(pid, SPO2_CODE, 95, "%")
    obs(pid, RR_CODE, 22, "/min")
    obs(pid, TEMP_CODE, 38.2, "C")
    obs(pid, WBC_CODE, 9.0, "10^9/L")
    obs(pid, CRP_CODE, 10, "mg/L")
    med(pid, AMOX_CLAV, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "setting": "outpatient", "severity": "mild",
            "compliant": True,
            "warn_not_in": ["not_first_line_abt", "hospitalization_indicated"]}


def s_positive_outpatient_ige_allergy_macrolide():
    """P3: IgE-аллергия на β-лактамы → макролид."""
    pid = make_patient(35)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    allergy(pid, reaction_type="ige")
    obs(pid, SPO2_CODE, 96, "%")
    obs(pid, TEMP_CODE, 38.0, "C")
    obs(pid, WBC_CODE, 8.0, "10^9/L")
    obs(pid, CRP_CODE, 8, "mg/L")
    med(pid, AZITHRO, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "compliant": True,
            "warn_not_in": ["not_first_line_abt"]}


def s_positive_inpatient_mild():
    """P4: стационар, нетяжёлая ВП, цефтриаксон в/в — без ОРИТ."""
    pid = make_patient(50)
    add_pneumonia(pid, onset_days_ago=5)
    add_encounter(pid, cls="inpatient", status="in-progress", start_days_ago=5)
    obs(pid, TEMP_CODE, 37.0, "C", days_ago=0)
    obs(pid, SPO2_CODE, 96, "%", days_ago=0)
    obs(pid, RR_CODE, 22, "/min", days_ago=0)
    obs(pid, WBC_CODE, 9.0, "10^9/L", days_ago=5)
    obs(pid, CRP_CODE, 60, "mg/L", days_ago=5)
    obs(pid, CRP_CODE, 20, "mg/L", days_ago=0)
    req(pid, "CXR", days_ago=5)
    req(pid, "URINE", days_ago=5)
    req(pid, "ECG", days_ago=5)
    req(pid, "BLOOD_CULT", days_ago=5)
    med(pid, CEFTRIAXONE, route="iv", start_days_ago=5, duration_days=7)
    med(pid, AMOX_CLAV, route="oral", start_days_ago=2, duration_days=5)
    req(pid, "CXR_REPEAT", days_ago=0)
    return {"applicable": True, "setting": "inpatient", "severity": "mild",
            "compliant": True,
            "warn_not_in": ["no_abt", "oral_in_inpatient", "icu_indicated",
                           "hospitalization_indicated", "abt_no_effect",
                           "crp_not_decreasing", "no_reassessment"]}


def s_negative_no_abt():
    """N1: ВП есть, АБТ нет."""
    pid = make_patient(40)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    obs(pid, SPO2_CODE, 96, "%")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["no_abt", "missing_cbc", "missing_crp"]}


def s_negative_wrong_first_line():
    """N2: без факторов/аллергии, азитромицин вместо амоксициллина."""
    pid = make_patient(40)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    obs(pid, SPO2_CODE, 96, "%")
    obs(pid, WBC_CODE, 8.0, "10^9/L")
    obs(pid, CRP_CODE, 8, "mg/L")
    med(pid, AZITHRO, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["not_first_line_abt"]}


def s_negative_severe_needs_hospitalization():
    """N3: тяжёлая ВП (SpO2+ЧД) амбулаторно — госпитализация."""
    pid = make_patient(55)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    obs(pid, SPO2_CODE, 87, "%")
    obs(pid, RR_CODE, 32, "/min")
    obs(pid, WBC_CODE, 8.0, "10^9/L")
    obs(pid, CRP_CODE, 8, "mg/L")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "severity": "severe", "any_warning": True,
            "warn_in": ["hospitalization_indicated"]}


def s_negative_severe_oral_in_inpatient():
    """N4: тяжёлая ВП, стационар, АБТ per os + ОРИТ."""
    pid = make_patient(48)
    add_pneumonia(pid, onset_days_ago=1)
    add_encounter(pid, cls="inpatient", status="in-progress", start_days_ago=1)
    obs(pid, SPO2_CODE, 85, "%", days_ago=1)
    obs(pid, RR_CODE, 34, "/min", days_ago=1)
    obs(pid, TEMP_CODE, 39.0, "C", days_ago=1)
    obs(pid, WBC_CODE, 12.0, "10^9/L", days_ago=1)
    obs(pid, CRP_CODE, 80, "mg/L", days_ago=1)
    req(pid, "CXR", days_ago=1)
    med(pid, CEFTRIAXONE, route="oral", start_days_ago=1, duration_days=12)
    return {"applicable": True, "setting": "inpatient", "severity": "severe",
            "any_warning": True,
            "warn_in": ["oral_in_inpatient", "icu_indicated"]}


def s_negative_bronchodilator_not_indicated():
    """N5: бронходилататор без бронхообструкции."""
    pid = make_patient(40)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    obs(pid, SPO2_CODE, 96, "%")
    obs(pid, WBC_CODE, 8.0, "10^9/L")
    obs(pid, CRP_CODE, 8, "mg/L")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=10)
    med(pid, SALBUTAMOL, route="oral", start_days_ago=0, duration_days=5)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["bronchodilator_not_indicated"]}


def s_negative_course_too_short():
    """N6: курс АБТ 3 дня → course_too_short (порог движка: <5 дн.)."""
    pid = make_patient(40)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    obs(pid, SPO2_CODE, 96, "%")
    obs(pid, WBC_CODE, 8.0, "10^9/L")
    obs(pid, CRP_CODE, 8, "mg/L")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=3)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["course_too_short"]}


def s_edge_no_pneumonia():
    """E1: ОРВИ без пневмонии → протокол не применим."""
    pid = make_patient(40)
    fs.add_condition(pid, "J00", "Острый назофарингит (ОРВИ)")
    add_encounter(pid, cls="ambulatory")
    obs(pid, TEMP_CODE, 37.5, "C")
    return {"applicable": False, "compliant": True}


def s_edge_ige_allergy_wrong_drug():
    """E2: IgE-аллергия, назначен амоксициллин."""
    pid = make_patient(38)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    allergy(pid, reaction_type="ige")
    obs(pid, SPO2_CODE, 96, "%")
    obs(pid, WBC_CODE, 8.0, "10^9/L")
    obs(pid, CRP_CODE, 8, "mg/L")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["not_first_line_abt"]}


def s_edge_risk_wrong_amoxicillin():
    """E3: фактор риска, назначен амоксициллин (нужен клавуланат)."""
    pid = make_patient(42)
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    flag(pid, "abt_3mo")
    obs(pid, SPO2_CODE, 95, "%")
    obs(pid, WBC_CODE, 8.0, "10^9/L")
    obs(pid, CRP_CODE, 8, "mg/L")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["not_first_line_abt"]}


def s_edge_abt_no_effect_72h():
    """E4: нет эффекта АБТ — лихорадка после 72ч."""
    pid = make_patient(40)
    add_pneumonia(pid, onset_days_ago=4)
    add_encounter(pid, cls="ambulatory", start_days_ago=4)
    obs(pid, TEMP_CODE, 39.0, "C", days_ago=4)
    obs(pid, TEMP_CODE, 38.5, "C", days_ago=0)
    obs(pid, SPO2_CODE, 96, "%", days_ago=4)
    obs(pid, WBC_CODE, 8.0, "10^9/L", days_ago=4)
    obs(pid, CRP_CODE, 30, "mg/L", days_ago=4)
    obs(pid, CRP_CODE, 25, "mg/L", days_ago=0)
    med(pid, AMOX, route="oral", start_days_ago=4, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["abt_no_effect", "hospitalization_indicated"]}


# --- 5 обращений: верно vs отклонение ---

def c1_right():
    pid = make_patient(40, family="Иванов")
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    obs(pid, TEMP_CODE, 38.6, "C", display="Температура")
    obs(pid, SPO2_CODE, 96, "%", display="SpO2")
    obs(pid, RR_CODE, 22, "/min", display="ЧД")
    obs(pid, HR_CODE, 90, "bpm", display="ЧСС")
    obs(pid, WBC_CODE, 9.5, "10^9/L", display="Лейкоциты")
    obs(pid, CRP_CODE, 14, "mg/L", display="СРБ")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "setting": "outpatient", "severity": "mild",
            "compliant": True,
            "warn_not_in": ["no_abt", "not_first_line_abt", "parenteral_in_outpatient",
                            "hospitalization_indicated", "course_too_short"]}


def c1_wrong():
    pid = make_patient(40, family="Иванов")
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    obs(pid, TEMP_CODE, 38.6, "C", display="Температура")
    obs(pid, SPO2_CODE, 96, "%", display="SpO2")
    obs(pid, RR_CODE, 22, "/min", display="ЧД")
    obs(pid, WBC_CODE, 9.5, "10^9/L", display="Лейкоциты")
    obs(pid, CRP_CODE, 14, "mg/L", display="СРБ")
    med(pid, CEFTRIAXONE, route="iv", start_days_ago=0, duration_days=3)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["parenteral_in_outpatient", "course_too_short"]}


def c2_right():
    pid = make_patient(45, family="Петров")
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    flag(pid, "abt_3mo")
    obs(pid, TEMP_CODE, 38.3, "C", display="Температура")
    obs(pid, SPO2_CODE, 95, "%", display="SpO2")
    obs(pid, RR_CODE, 22, "/min", display="ЧД")
    obs(pid, WBC_CODE, 9.0, "10^9/L", display="Лейкоциты")
    obs(pid, CRP_CODE, 11, "mg/L", display="СРБ")
    med(pid, AMOX_CLAV, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "setting": "outpatient", "compliant": True,
            "warn_not_in": ["not_first_line_abt", "hospitalization_indicated"]}


def c2_wrong():
    pid = make_patient(45, family="Петров")
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    flag(pid, "abt_3mo")
    obs(pid, TEMP_CODE, 38.3, "C", display="Температура")
    obs(pid, SPO2_CODE, 95, "%", display="SpO2")
    obs(pid, RR_CODE, 22, "/min", display="ЧД")
    obs(pid, WBC_CODE, 9.0, "10^9/L", display="Лейкоциты")
    obs(pid, CRP_CODE, 11, "mg/L", display="СРБ")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["not_first_line_abt"]}


def c3_right():
    pid = make_patient(32, family="Сидорова", gender="female")
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    allergy(pid, code="penicillin", display="Пенициллин", reaction_type="ige")
    obs(pid, TEMP_CODE, 38.1, "C", display="Температура")
    obs(pid, SPO2_CODE, 96, "%", display="SpO2")
    obs(pid, RR_CODE, 20, "/min", display="ЧД")
    obs(pid, WBC_CODE, 8.5, "10^9/L", display="Лейкоциты")
    obs(pid, CRP_CODE, 9, "mg/L", display="СРБ")
    med(pid, AZITHRO, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "setting": "outpatient", "compliant": True,
            "warn_not_in": ["not_first_line_abt"]}


def c3_wrong():
    pid = make_patient(32, family="Сидорова", gender="female")
    add_pneumonia(pid)
    add_encounter(pid, cls="ambulatory")
    allergy(pid, code="penicillin", display="Пенициллин", reaction_type="ige")
    obs(pid, TEMP_CODE, 38.1, "C", display="Температура")
    obs(pid, SPO2_CODE, 96, "%", display="SpO2")
    obs(pid, RR_CODE, 20, "/min", display="ЧД")
    obs(pid, WBC_CODE, 8.5, "10^9/L", display="Лейкоциты")
    obs(pid, CRP_CODE, 9, "mg/L", display="СРБ")
    med(pid, AMOX, route="oral", start_days_ago=0, duration_days=10)
    req(pid, "CXR_REPEAT")
    return {"applicable": True, "any_warning": True,
            "warn_in": ["not_first_line_abt"]}


def _c4_base(pid):
    """Тяжёлая ВП: SpO2 + ЧД (≥2 малых) → severity=severe, icu_indicated."""
    add_pneumonia(pid, onset_days_ago=5)
    add_encounter(pid, cls="inpatient", status="in-progress", start_days_ago=5)
    flag(pid, "pleural_effusion")
    obs(pid, TEMP_CODE, 39.2, "C", days_ago=5, display="Температура")
    obs(pid, SPO2_CODE, 88, "%", days_ago=5, display="SpO2")
    obs(pid, RR_CODE, 32, "/min", days_ago=5, display="ЧД")
    obs(pid, WBC_CODE, 15.0, "10^9/L", days_ago=5, display="Лейкоциты")
    obs(pid, CRP_CODE, 80, "mg/L", days_ago=5, display="СРБ")
    # «свежие» виталки оставляем тяжёлыми — иначе severity уйдёт в mild
    obs(pid, TEMP_CODE, 37.2, "C", days_ago=0, display="Температура")
    obs(pid, SPO2_CODE, 88, "%", days_ago=0, display="SpO2")
    obs(pid, RR_CODE, 32, "/min", days_ago=0, display="ЧД")
    req(pid, "CXR", days_ago=5)
    req(pid, "URINE", days_ago=5)
    req(pid, "ECG", days_ago=5)
    req(pid, "BLOOD_CULT", days_ago=5)


def c4_right():
    """Тяжёлая: цефтриаксон + макролид в/в. icu_indicated ожидаем (тяжёлая = ОРИТ)."""
    pid = make_patient(55, family="Кузнецов")
    _c4_base(pid)
    obs(pid, CRP_CODE, 25, "mg/L", days_ago=0, display="СРБ")
    med(pid, CEFTRIAXONE, route="iv", start_days_ago=5, duration_days=7)
    med(pid, AZITHRO, route="oral", start_days_ago=5, duration_days=7)
    req(pid, "CXR_REPEAT", days_ago=0)
    return {"applicable": True, "setting": "inpatient", "severity": "severe",
            "any_warning": True,
            "warn_in": ["icu_indicated"],
            "warn_not_in": ["no_abt", "oral_in_inpatient", "not_inpatient_first_line",
                            "abt_no_effect", "course_too_short"]}


def c4_wrong():
    pid = make_patient(55, family="Кузнецов")
    _c4_base(pid)
    # цефтриаксон per os + короткий курс (dur=3)
    med(pid, CEFTRIAXONE, route="oral", start_days_ago=0, duration_days=3)
    req(pid, "CXR_REPEAT", days_ago=0)
    return {"applicable": True, "setting": "inpatient", "severity": "severe",
            "any_warning": True,
            "warn_in": ["oral_in_inpatient", "course_too_short", "icu_indicated"]}


def _c5_base(pid):
    add_pneumonia(pid, onset_days_ago=5)
    add_encounter(pid, cls="inpatient", status="in-progress", start_days_ago=5)
    flag(pid, "pleural_effusion")
    flag(pid, "pleurisy")  # complication для YAML (аспирация+осложнение → карбапенем)
    flag(pid, "aspiration_suspicion")
    flag(pid, "mrsa_suspicion")
    obs(pid, TEMP_CODE, 39.0, "C", days_ago=5, display="Температура")
    obs(pid, SPO2_CODE, 88, "%", days_ago=5, display="SpO2")
    obs(pid, RR_CODE, 32, "/min", days_ago=5, display="ЧД")
    obs(pid, WBC_CODE, 16.0, "10^9/L", days_ago=5, display="Лейкоциты")
    obs(pid, CRP_CODE, 90, "mg/L", days_ago=5, display="СРБ")
    obs(pid, TEMP_CODE, 37.1, "C", days_ago=0, display="Температура")
    obs(pid, SPO2_CODE, 88, "%", days_ago=0, display="SpO2")
    obs(pid, RR_CODE, 32, "/min", days_ago=0, display="ЧД")
    req(pid, "CXR", days_ago=5)
    req(pid, "URINE", days_ago=5)
    req(pid, "ECG", days_ago=5)
    req(pid, "BLOOD_CULT", days_ago=5)


def c5_right():
    pid = make_patient(60, family="Смирнов")
    _c5_base(pid)
    obs(pid, CRP_CODE, 30, "mg/L", days_ago=0, display="СРБ")
    # аспирация + осложнение → меропенем; аспирация → метронидазол; MRSA → линезолид
    med(pid, MEROPENEM, route="iv", start_days_ago=5, duration_days=7)
    med(pid, METRONIDAZOLE, route="iv", start_days_ago=5, duration_days=7)
    med(pid, LINEZOLID, route="iv", start_days_ago=5, duration_days=7)
    req(pid, "CXR_REPEAT", days_ago=0)
    return {"applicable": True, "setting": "inpatient", "severity": "severe",
            "any_warning": True,
            "warn_in": ["icu_indicated"],
            "warn_not_in": ["no_abt", "not_inpatient_first_line", "oral_in_inpatient",
                            "abt_no_effect", "course_too_short"]}


def c5_wrong():
    pid = make_patient(60, family="Смирнов")
    _c5_base(pid)
    med(pid, CEFTRIAXONE, route="iv", start_days_ago=0, duration_days=3)
    req(pid, "CXR_REPEAT", days_ago=0)
    return {"applicable": True, "setting": "inpatient", "severity": "severe",
            "any_warning": True,
            "warn_in": ["not_inpatient_first_line", "course_too_short", "icu_indicated"]}


SCENARIOS = [
    ("P1  амбулаторно, нетяжёлая, амоксициллин — всё верно", "positive", s_positive_outpatient_mild),
    ("P2  амбулаторно, фактор риска, амокс/клавуланат — верно", "positive", s_positive_outpatient_risk_clavulanate),
    ("P3  амбулаторно, IgE-аллергия, макролид — верно", "positive", s_positive_outpatient_ige_allergy_macrolide),
    ("P4  стационар, нетяжёлая, цефтриаксон в/в — верно", "positive", s_positive_inpatient_mild),
    ("N1  АБТ не назначена", "negative", s_negative_no_abt),
    ("N2  не первая линия (азитромицин вместо амоксициллина)", "negative", s_negative_wrong_first_line),
    ("N3  тяжёлая амбулаторно — нужна госпитализация", "negative", s_negative_severe_needs_hospitalization),
    ("N4  тяжёлая, АБТ per os вместо в/в + ОРИТ", "negative", s_negative_severe_oral_in_inpatient),
    ("N5  бронходилататор без бронхообструкции", "negative", s_negative_bronchodilator_not_indicated),
    ("N6  курс АБТ 3 дня (короче 7–14)", "negative", s_negative_course_too_short),
    ("E1  нет пневмонии (ОРВИ) — протокол не применим", "edge", s_edge_no_pneumonia),
    ("E2  IgE-аллергия, но назначен амоксициллин", "edge", s_edge_ige_allergy_wrong_drug),
    ("E3  фактор риска, но назначен амоксициллин", "edge", s_edge_risk_wrong_amoxicillin),
    ("E4  нет эффекта АБТ через 72ч", "edge", s_edge_abt_no_effect_72h),
    ("С1R Иванов 40л, амбулаторно — врач всё верно", "positive", c1_right),
    ("С1W Иванов 40л, амбулаторно — цефтриаксон в/в + курс 3д", "negative", c1_wrong),
    ("С2R Петров 45л, фактор риска — амокс/клав верно", "positive", c2_right),
    ("С2W Петров 45л, фактор риска — амоксициллин", "negative", c2_wrong),
    ("С3R Сидорова 32г, IgE-аллергия — азитромицин верно", "positive", c3_right),
    ("С3W Сидорова 32г, IgE-аллергия — амоксициллин", "negative", c3_wrong),
    ("С4R Кузнецов 55л, тяжёлая — цефтриаксон+макролид", "positive", c4_right),
    ("С4W Кузнецов 55л, тяжёлая — цефтриаксон per os + короткий курс", "negative", c4_wrong),
    ("С5R Смирнов 60л, аспирация+MRSA — меропенем+метронидазол+линезолид", "positive", c5_right),
    ("С5W Смирнов 60л, аспирация+MRSA — цефтриаксон без схемы", "negative", c5_wrong),
]


def check(verdict, expects):
    problems = []
    warns = _warning_codes(verdict)

    if "applicable" in expects and verdict.get("applicable") != expects["applicable"]:
        problems.append(f"applicable: ждали {expects['applicable']}, получили {verdict.get('applicable')}")
    if "setting" in expects and verdict.get("setting") != expects["setting"]:
        problems.append(f"setting: ждали {expects['setting']}, получили {verdict.get('setting')}")
    if "severity" in expects and verdict.get("severity") != expects["severity"]:
        problems.append(f"severity: ждали {expects['severity']}, получили {verdict.get('severity')}")
    if "compliant" in expects and verdict.get("compliant") != expects["compliant"]:
        problems.append(f"compliant: ждали {expects['compliant']}, получили {verdict.get('compliant')}")
    if "any_warning" in expects and (any(warns) != expects["any_warning"]):
        problems.append(f"any_warning: ждали {expects['any_warning']}, получили {bool(warns)}")

    for code in expects.get("warn_in", []):
        if code not in warns:
            problems.append(f"ожидали warning '{code}', но его нет (есть {warns})")
    for code in expects.get("warn_not_in", []):
        if code in warns:
            problems.append(f"warning '{code}' не должен был сработать, но он есть")

    return (len(problems) == 0, problems)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true", help="показать все gap'ы")
    args = ap.parse_args()

    print("=" * 78)
    print("Прогон сценариев протокола ВП (КП МЗ РБ №768, взрослые) — clinic-os")
    print(f"БД: изолированный SQLite ({db.DB_PATH})")
    print("=" * 78)

    passed = failed = 0
    for name, kind, builder in SCENARIOS:
        for t in ("observation", "medication_request", "service_request", "diagnostic_report",
                 "condition_", "encounter", "allergy_intolerance", "clinical_flag",
                 "care_plan", "goal", "pathway", "patient", "practitioner",
                 "medication_knowledge"):
            try:
                db.execute(f"DELETE FROM {t}")
            except Exception:
                pass

        expects = builder()
        pid = fs.get_all_patients()[0]["id"] if fs.get_all_patients() else None
        verdict = pcap.evaluate_cap(pid) if pid else {"applicable": False, "gaps": []}
        ok, problems = check(verdict, expects)

        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        else:
            failed += 1

        kind_tag = {"positive": "ПОЗИТ", "negative": "НЕГАТ", "edge": "КРАЙ"}[kind]
        print(f"\n[{status}] {kind_tag}  {name}")
        print(f"      applicable={verdict.get('applicable')} setting={verdict.get('setting')} "
              f"severity={verdict.get('severity')} compliant={verdict.get('compliant')}")
        if problems:
            for p in problems:
                print(f"      ✗ {p}")
        if args.verbose or not ok:
            warns = [g for g in verdict.get("gaps", []) if g["severity"] == "warning"]
            infos = [g for g in verdict.get("gaps", []) if g["severity"] == "info"]
            if warns:
                print(f"      warnings: {[g['code'] for g in warns]}")
            if infos:
                print(f"      info:     {[g['code'] for g in infos]}")
            if args.verbose:
                for g in verdict.get("gaps", []):
                    print(f"        - [{g['severity']}] {g['code']}: {g['message']}")

    print("\n" + "=" * 78)
    print(f"ИТОГ: {passed} прошли, {failed} провалены (всего {len(SCENARIOS)})")
    print("=" * 78)

    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
