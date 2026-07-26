#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Демо-пациенты: у каждого ровно 1 приём и 1 активный диагноз.

Покрытие протоколов КП №768 (ВП) и КП №23 (ЖДА) — разные варианты, чтобы
на демо можно было увидеть эталон, ошибки назначения, госпитализацию,
аллергию, стационар, пустую карту и ЖДА.

Запуск:
  CLINIC_DB=/path/to.db python3 tools/seed_ten.py          # локальный SQLite
  DATABASE_URL=postgres://... python3 tools/seed_ten.py    # облако (Supabase)
  python3 tools/seed_ten.py --keep                         # не очищать, только добавить

Гостевой сценарий (/demo) — семейство «Соколов».
Каталог ЛС не очищается; при необходимости дополняется.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# CLINIC_DB имеет приоритет над DATABASE_URL (как в prepare_demo_db)
clinic_db = os.environ.get("CLINIC_DB")
if clinic_db and not os.environ.get("FORCE_DATABASE_URL"):
    os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402

if clinic_db and not os.environ.get("DATABASE_URL"):
    db.DB_PATH = os.path.abspath(clinic_db)

import care_plan_service as cps  # noqa: E402
import fhir_store as fs  # noqa: E402
import protocol_cap as pcap  # noqa: E402
import protocol_dispatch as pdisp  # noqa: E402
from protocol_verdict import verdict_for_ui  # noqa: E402


def _ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _clear_clinical():
    """Удалить всех пациентов (каскад) + служебные таблицы. Каталог ЛС сохранить."""
    for p in list(fs.get_all_patients()):
        fs.delete_patient(p["id"])
    fs.clear_pid_cache()
    for t in ("cap_cache", "audit_log", "practitioner"):
        try:
            db.execute(f"DELETE FROM {t}")
        except Exception:
            pass


def _obs(pid, eid, code, display, value, unit, days_ago=0):
    fs.add_observation(
        pid, code, display,
        value_numeric=value, value_unit=unit,
        obs_date=_ago(days_ago), encounter_id=eid,
    )


def _vitals(pid, eid, days_ago=0, *, t=None, spo2=None, rr=None, hr=None,
            sbp=None, dbp=None, wbc=None, crp=None, hb=None, ferritin=None):
    if t is not None:
        _obs(pid, eid, "8310-5", "Температура", t, "C", days_ago)
    if spo2 is not None:
        _obs(pid, eid, "59408-5", "SpO2", spo2, "%", days_ago)
    if rr is not None:
        _obs(pid, eid, "9279-1", "ЧД", rr, "/min", days_ago)
    if hr is not None:
        _obs(pid, eid, "8867-4", "ЧСС", hr, "bpm", days_ago)
    if sbp is not None:
        _obs(pid, eid, "8480-6", "АД систолическое", sbp, "mmHg", days_ago)
    if dbp is not None:
        _obs(pid, eid, "8462-4", "АД диастолическое", dbp, "mmHg", days_ago)
    if wbc is not None:
        _obs(pid, eid, "6690-2", "Лейкоциты", wbc, "10^9/L", days_ago)
    if crp is not None:
        _obs(pid, eid, "30522-7", "СРБ", crp, "mg/L", days_ago)
    if hb is not None:
        _obs(pid, eid, "718-7", "Гемоглобин", hb, "g/L", days_ago)
    if ferritin is not None:
        _obs(pid, eid, "2276-4", "Ферритин", ferritin, "ng/mL", days_ago)


def _gc(pid, eid, key):
    fs.add_flag(pid, key, "true", category="general_condition", encounter_id=eid)


def _med(pid, eid, code, display, *, route="oral", days_ago=0, duration_days=7,
         dose=None, frequency=None, status="active", dose_per_day=None):
    start = _ago(days_ago)
    end = None
    if duration_days is not None:
        end = (date.fromisoformat(start) + timedelta(days=duration_days)).isoformat()
    fs.add_medication(
        pid, code, display,
        dose=dose, frequency=frequency, route=route,
        period_start=start, period_end=end, med_date=start,
        encounter_id=eid, status=status, dose_per_day=dose_per_day,
    )


def _labs_done(pid, eid, days_ago=0):
    fs.add_service_request(pid, "CBC", "ОАК", encounter_id=eid,
                           occurrence_date=_ago(days_ago), status="completed")
    fs.add_service_request(pid, "CRP", "СРБ", encounter_id=eid,
                           occurrence_date=_ago(days_ago), status="completed")


def _cxr(pid, eid, conclusion, days_ago=0):
    fs.add_diagnostic_report(
        pid, "CXR", "Рентгенография ОГК",
        conclusion=conclusion, rep_date=_ago(days_ago), encounter_id=eid,
    )


def seed_ten(dr_id: str):
    """
    У каждого пациента ровно 1 приём и 1 активный диагноз.
    Разные коды МКБ из реестров протоколов и разные клинические варианты.

      1 Орлов       — ВП J15.9, эталон амбулаторно (амоксициллин)
      2 Соколов     — ВП J18.9, гость: неверная АБТ (азитромицин)
      3 Морозов     — ВП J18.0, тяжёлая амбулаторно → госпитализация
      4 Стационаров — ВП J13, стационар OK (цефтриаксон + азитромицин в/в)
      5 Клавуланова — ВП J15.9, риск АБТ 3 мес → нужен амокс/клав
      6 Аллергова   — ВП J14, IgE на β-лактамы + ошибочный амоксициллин
      7 Аспиратов   — ВП J18.9, аспирация + MRSA (мультирежим)
      8 Бронхов     — ВП J15.7, бронхолитик без обструкции + короткий курс
      9 Контролёв   — ВП J15.4, АБТ без эффекта (лихорадка сохраняется)
     10 Пустова     — ВП J18.9, почти пустая карта (gaps UI)
     11 Феррова     — ЖДА D50.9, анализы есть, железо не назначено
     12 Железов     — ЖДА D50.0, терапия железом по протоколу
    """
    stories = []

    # ── 1. Орлов — эталон амбулаторно ─────────────────────────────────────
    pid = fs.add_patient("Орлов", "Антон", "Петрович", "male", "1985-03-12")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(5), complaint="Кашель с мокротой 4 дня, t 38.2")
    fs.add_condition(pid, "J15.9", "Бактериальная пневмония неуточнённая",
                     onset_date=_ago(5), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "Кашель", "true", "anamnesis", encounter_id=e1)
    # Старт: виталы + АБТ; контроль t/SpO2 позже — закрывает no_reassessment.
    _vitals(pid, e1, 5, t=38.2, spo2=96, rr=18, hr=88, sbp=120, dbp=78, wbc=11.2, crp=48)
    _labs_done(pid, e1, 5)
    _cxr(pid, e1, "Очагово-инфильтративные изменения S9 справа", 5)
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=5,
         duration_days=7, dose="500 мг", frequency="3 раза в день", dose_per_day=1500)
    _vitals(pid, e1, 2, t=36.8, spo2=97, rr=16, hr=76, sbp=118, dbp=76, crp=18)
    fs.add_service_request(pid, "CXR_REPEAT", "Контрольная R-графия ОГК",
                           encounter_id=e1, occurrence_date=_ago(0), status="active")
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "controlled")
    stories.append((pid, "Орлов", "эталон амбулаторно · J15.9 · 1 приём"))

    # ── 2. Соколов — гость, неверная АБТ ──────────────────────────────────
    pid = fs.add_patient("Соколов", "Борис", "Иванович", "male", "1978-06-05")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(2), complaint="Кашель, слабость, t 37.8")
    fs.add_condition(pid, "J18.9", "Пневмония неуточнённая",
                     onset_date=_ago(2), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "Кашель", "true", "anamnesis", encounter_id=e1)
    _vitals(pid, e1, 2, t=37.8, spo2=97, rr=17, hr=82, sbp=118, dbp=74, wbc=9.5, crp=42)
    _labs_done(pid, e1, 2)
    _cxr(pid, e1, "Усиление лёгочного рисунка слева", 2)
    _med(pid, e1, "J01FA10", "Азитромицин", route="oral", days_ago=2,
         duration_days=5, dose="500 мг", frequency="1 раз в день")
    cps.create_cap_plan(pid)
    stories.append((pid, "Соколов", "гость: неверная АБТ · J18.9 · 1 приём"))

    # ── 3. Морозов — тяжёлая амбулаторно ──────────────────────────────────
    pid = fs.add_patient("Морозов", "Виктор", "Сергеевич", "male", "1975-04-18")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(1), complaint="Одышка, t 39.5, слабость")
    fs.add_condition(pid, "J18.0", "Бронхопневмония неуточнённая",
                     onset_date=_ago(1), encounter_id=e1)
    _gc(pid, e1, "severe")
    fs.add_flag(pid, "cyanosis", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "consciousness_disorder", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "shock", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "bilateral_infiltration", "true", "imaging", encounter_id=e1)
    _vitals(pid, e1, 1, t=39.5, spo2=86, rr=32, hr=118, sbp=85, dbp=55, wbc=22.0, crp=180)
    _cxr(pid, e1, "Двусторонняя инфильтрация — тяжёлая ВП", 1)
    cps.create_cap_plan(pid)
    stories.append((pid, "Морозов", "тяжёлая амбулаторно → стационар · J18.0 · 1 приём"))

    # ── 4. Стационаров — корректный стационар ─────────────────────────────
    pid = fs.add_patient("Стационаров", "Павел", "Игоревич", "male", "1972-01-18")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="inpatient",
                          start=_ago(5), complaint="Госпитализация: ВП средней тяжести")
    fs.add_condition(pid, "J13", "Пневмония, вызванная Streptococcus pneumoniae",
                     onset_date=_ago(5), encounter_id=e1)
    _gc(pid, e1, "mod_severe")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "Кашель", "true", "anamnesis", encounter_id=e1)
    _vitals(pid, e1, 5, t=38.8, spo2=91, rr=24, hr=102, sbp=110, dbp=70, wbc=14.5, crp=96)
    _labs_done(pid, e1, 5)
    fs.add_service_request(pid, "URINE", "ОАМ", encounter_id=e1,
                           occurrence_date=_ago(5), status="completed")
    fs.add_service_request(pid, "ECG", "ЭКГ", encounter_id=e1,
                           occurrence_date=_ago(5), status="completed")
    _cxr(pid, e1, "Инфильтрация нижней доли слева", 5)
    _med(pid, e1, "J01DD04", "Цефтриаксон", route="iv", days_ago=5,
         duration_days=7, dose="2 г", frequency="1 раз в день")
    _med(pid, e1, "J01FA10", "Азитромицин", route="iv", days_ago=5,
         duration_days=5, dose="500 мг", frequency="1 раз в день")
    _vitals(pid, e1, 2, t=36.8, spo2=96, rr=16, hr=78, sbp=122, dbp=78, crp=28)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "inpatient")
    stories.append((pid, "Стационаров", "стационар OK · J13 · 1 приём"))

    # ── 5. Клавуланова — риск АБТ 3 мес ───────────────────────────────────
    pid = fs.add_patient("Клавуланова", "Мария", "Сергеевна", "female", "1988-05-09")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(3), complaint="Кашель, t 38.0; АБТ месяц назад")
    fs.add_condition(pid, "J15.9", "Бактериальная пневмония неуточнённая",
                     onset_date=_ago(3), encounter_id=e1)
    fs.add_flag(pid, "abt_3mo", "true", "social_risk", encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    _vitals(pid, e1, 3, t=38.0, spo2=96, rr=18, hr=90, sbp=124, dbp=80, wbc=12.0, crp=55)
    _labs_done(pid, e1, 3)
    _cxr(pid, e1, "Инфильтрация справа", 3)
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=3,
         duration_days=7, dose="500 мг", frequency="3 раза в день", dose_per_day=1500)
    cps.create_cap_plan(pid)
    stories.append((pid, "Клавуланова", "риск АБТ 3 мес → амокс/клав · J15.9 · 1 приём"))

    # ── 6. Аллергова — IgE β-лактамы ──────────────────────────────────────
    pid = fs.add_patient("Аллергова", "Елена", "Викторовна", "female", "1995-09-14")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(2),
                          complaint="Кашель, t 37.9; анафилаксия на пенициллин")
    fs.add_condition(pid, "J14", "Пневмония, вызванная Haemophilus influenzae",
                     onset_date=_ago(2), encounter_id=e1)
    fs.add_allergy(pid, "beta-lactam", "β-лактамы", criticality="high",
                   reaction_type="ige", recorded_date=_ago(400))
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    _vitals(pid, e1, 2, t=37.9, spo2=97, rr=17, hr=84, sbp=116, dbp=72, wbc=8.5, crp=36)
    _labs_done(pid, e1, 2)
    _cxr(pid, e1, "Очаговая инфильтрация", 2)
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=2,
         duration_days=7, dose="500 мг", frequency="3 раза в день")
    cps.create_cap_plan(pid)
    stories.append((pid, "Аллергова", "IgE + ошибочный амокс · J14 · 1 приём"))

    # ── 7. Аспиратов — аспирация + MRSA ───────────────────────────────────
    pid = fs.add_patient("Аспиратов", "Игорь", "Николаевич", "male", "1965-04-28")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="inpatient",
                          start=_ago(6), complaint="Аспирационная пневмония, подозрение MRSA")
    fs.add_condition(pid, "J18.9", "Пневмония неуточнённая (аспирационный контекст)",
                     onset_date=_ago(6), encounter_id=e1)
    fs.add_flag(pid, "aspiration_suspicion", "true", "context", encounter_id=e1)
    fs.add_flag(pid, "mrsa_suspicion", "true", "context", encounter_id=e1)
    _gc(pid, e1, "mod_severe")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    _vitals(pid, e1, 6, t=38.6, spo2=90, rr=26, hr=108, sbp=100, dbp=65, wbc=16.0, crp=120)
    _labs_done(pid, e1, 6)
    fs.add_service_request(pid, "SPUTUM_CULTURE", "Посев мокроты", encounter_id=e1,
                           occurrence_date=_ago(6), status="active")
    fs.add_service_request(pid, "BLOOD_CULT", "Посев крови", encounter_id=e1,
                           occurrence_date=_ago(6), status="completed")
    _cxr(pid, e1, "Инфильтрация в зависимых отделах", 6)
    fs.add_diagnostic_report(pid, "CT", "КТ ОГК",
                             conclusion="Участки консолидации",
                             rep_date=_ago(5), encounter_id=e1)
    _med(pid, e1, "J01CR02", "Амоксициллин с клавуланатом", route="iv", days_ago=6,
         duration_days=10, dose="1.2 г", frequency="3 раза в день")
    _med(pid, e1, "J01XX08", "Линезолид", route="iv", days_ago=6,
         duration_days=10, dose="600 мг", frequency="2 раза в день")
    _vitals(pid, e1, 3, t=37.2, spo2=94, rr=20, hr=92, sbp=112, dbp=70, crp=55)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "inpatient")
    stories.append((pid, "Аспиратов", "аспирация+MRSA · J18.9 · 1 приём"))

    # ── 8. Бронхов — бронхолитик без обструкции ───────────────────────────
    pid = fs.add_patient("Бронхов", "Олег", "Андреевич", "male", "1980-12-01")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(3),
                          complaint="Кашель, t 37.6; сальбутамол «на всякий»")
    fs.add_condition(pid, "J15.7", "Пневмония, вызванная Mycoplasma pneumoniae",
                     onset_date=_ago(3), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    _vitals(pid, e1, 3, t=37.6, spo2=97, rr=16, hr=78, sbp=120, dbp=76, wbc=8.0, crp=28)
    _labs_done(pid, e1, 3)
    _cxr(pid, e1, "Лёгкая инфильтрация", 3)
    _med(pid, e1, "J01FA10", "Азитромицин", route="oral", days_ago=3,
         duration_days=3, dose="500 мг", frequency="1 раз в день")
    _med(pid, e1, "R03AC02", "Сальбутамол", route="inhalation", days_ago=3,
         duration_days=5, dose="100 мкг", frequency="по потребности")
    cps.create_cap_plan(pid)
    stories.append((pid, "Бронхов", "бронхолитик без обструкции · J15.7 · 1 приём"))

    # ── 9. Контролёв — АБТ без эффекта (один открытый приём) ─────────────
    pid = fs.add_patient("Контролёв", "Андрей", "Павлович", "male", "1978-06-20")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(5), complaint="Кашель, t 38.4 — без улучшения на АБТ")
    fs.add_condition(pid, "J15.4", "Пневмония, вызванная стрептококками группы A",
                     onset_date=_ago(5), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "Кашель", "true", "anamnesis", encounter_id=e1)
    # Стартовые виталы при назначении + актуальные (всё ещё лихорадка)
    _vitals(pid, e1, 5, t=38.4, spo2=96, rr=19, hr=92, sbp=122, dbp=78, wbc=11.8, crp=62)
    _vitals(pid, e1, 0, t=38.5, spo2=94, rr=21, hr=98, sbp=118, dbp=74, crp=78)
    _labs_done(pid, e1, 5)
    _cxr(pid, e1, "Инфильтрация нижней доли справа", 5)
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=5,
         duration_days=7, dose="500 мг", frequency="3 раза в день", dose_per_day=1500)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "adjustment")
    stories.append((pid, "Контролёв", "АБТ без эффекта · J15.4 · 1 приём"))

    # ── 10. Пустова — почти пустая карта ──────────────────────────────────
    pid = fs.add_patient("Пустова", "Наталья", "Олеговна", "female", "1992-02-11")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(0), complaint="Первичный приём: кашель 2 дня")
    fs.add_condition(pid, "J18.9", "Пневмония неуточнённая",
                     onset_date=_ago(0), encounter_id=e1)
    cps.create_cap_plan(pid)
    stories.append((pid, "Пустова", "пустые этапы UI · J18.9 · 1 приём"))

    # ── 11. Феррова — ЖДА, железо не назначено ────────────────────────────
    pid = fs.add_patient("Феррова", "Ирина", "Дмитриевна", "female", "1982-11-03")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(2), complaint="Слабость, бледность; по ОАК — анемия")
    fs.add_condition(pid, "D50.9", "Железодефицитная анемия неуточнённая",
                     onset_date=_ago(2), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "Слабость", "true", "anamnesis", encounter_id=e1)
    _vitals(pid, e1, 2, t=36.6, spo2=98, rr=16, hr=84, sbp=110, dbp=70,
            hb=92, ferritin=8)
    fs.add_service_request(pid, "CBC", "ОАК", encounter_id=e1,
                           occurrence_date=_ago(2), status="completed")
    fs.add_service_request(pid, "FERRITIN", "Ферритин", encounter_id=e1,
                           occurrence_date=_ago(2), status="completed")
    fs.set_pathway(pid, "treatment")
    stories.append((pid, "Феррова", "ЖДА без железа · D50.9 · 1 приём"))

    # ── 12. Железов — ЖДА с терапией по протоколу ─────────────────────────
    pid = fs.add_patient("Железов", "Сергей", "Михайлович", "male", "1970-08-22")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(7), complaint="Утомляемость, одышка при нагрузке")
    fs.add_condition(pid, "D50.0", "Железодефицитная анемия вторичная",
                     onset_date=_ago(7), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "Слабость", "true", "anamnesis", encounter_id=e1)
    _vitals(pid, e1, 7, t=36.5, spo2=98, rr=15, hr=80, sbp=122, dbp=76,
            hb=98, ferritin=12)
    _obs(pid, e1, "2498-4", "Железо сыворотки", 5.2, "umol/L", 7)
    fs.add_service_request(pid, "CBC", "ОАК", encounter_id=e1,
                           occurrence_date=_ago(7), status="completed")
    fs.add_service_request(pid, "FERRITIN", "Ферритин", encounter_id=e1,
                           occurrence_date=_ago(7), status="completed")
    fs.add_service_request(pid, "IRON_SERUM", "Железо сыворотки", encounter_id=e1,
                           occurrence_date=_ago(7), status="completed")
    _med(pid, e1, "B03AA07", "Сульфат железа", route="oral", days_ago=7,
         duration_days=90, dose="100 мг элементарного Fe", frequency="1–2 раза в день")
    fs.set_pathway(pid, "treatment")
    stories.append((pid, "Железов", "ЖДА с железом · D50.0 · 1 приём"))

    return stories


def _ensure_drugs():
    import importlib.util
    path = os.path.join(REPO, "tools", "seed_drug_catalog.py")
    spec = importlib.util.spec_from_file_location("seed_drug_catalog", path)
    mod = importlib.util.module_from_spec(spec)
    argv = sys.argv[:]
    sys.argv = ["seed_drug_catalog.py"]
    try:
        spec.loader.exec_module(mod)
        mod.main()
    finally:
        sys.argv = argv


def _print_verdicts(stories):
    print("\n── Вердикты ──")
    for pid, name, story in stories:
        try:
            items = pdisp.patient_assessments(pid)
            n_enc = len(fs.get_encounters(pid))
            n_dx = len([c for c in fs.get_conditions(pid)
                        if (c.get("clinical_status") or "active") == "active"])
            shape = f"enc={n_enc} dx={n_dx}"
            if not items:
                print(f"  {name:14} [n/a ] {shape} нет применимого протокола")
                print(f"                 → {story}")
                continue
            for item in items:
                raw = item["assessment"]
                v = verdict_for_ui(raw, item["protocol_id"])
                ok = "OK" if v.get("ok") else ("n/a" if not v.get("applicable") else "GAP")
                tag = f"{name:14}" if item is items[0] else " " * 14
                print(
                    f"  {tag} [{ok:4}] {shape} ({item['protocol_id']}) "
                    f"CTA={(v.get('cta_label') or '—'):22} | "
                    f"{(v.get('headline') or '')[:70]}"
                )
                warns = [g["code"] for g in raw.get("gaps", []) if g.get("severity") == "warning"]
                if warns:
                    print(f"                 warnings: {warns}")
            print(f"                 → {story}")
        except Exception as e:
            print(f"  {name}: ERROR {e}")


def main() -> int:
    keep = "--keep" in sys.argv
    print(f"seed_ten backend={db.backend()} path={getattr(db, 'DB_PATH', None)}")
    fs.init_db()

    if not keep:
        print("clearing clinical data…")
        _clear_clinical()
    else:
        print("keeping existing data (--keep)")

    dr = fs.add_practitioner("Терапевт", "Анна", "терапия")
    stories = seed_ten(dr)
    print(f"seeded {len(stories)} patients (1 encounter + 1 diagnosis each)")

    print("seeding drug catalog…")
    _ensure_drugs()
    n_drugs = len(fs.get_drug_catalog())
    print(f"drug_catalog={n_drugs}")

    print("warming protocol cache…")
    for pid, name, _ in stories:
        pdisp.refresh_protocol_cache(pid)

    _print_verdicts(stories)

    bad = []
    for pid, name, _ in stories:
        n_enc = len(fs.get_encounters(pid))
        n_dx = len([c for c in fs.get_conditions(pid)
                    if (c.get("clinical_status") or "active") == "active"])
        if n_enc != 1 or n_dx != 1:
            bad.append(f"{name}: enc={n_enc} dx={n_dx}")
    if bad:
        print("\nSHAPE FAIL:", "; ".join(bad))
        return 1

    guest = next((s for s in stories if s[1] == "Соколов"), None)
    if guest:
        print(f"\nguest /demo → /patient/{guest[0]}")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
