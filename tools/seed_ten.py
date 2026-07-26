#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Десять взрослых пациентов ВП — покрытие протокола КП №768 и UI-путей.

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
            sbp=None, dbp=None, wbc=None, crp=None):
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


def _gc(pid, eid, key):
    """Общее состояние: key в GENERAL_CONDITION, category=general_condition."""
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


def seed_ten(dr_id: str):
    """
    Карта покрытия UI/протокола:
      1 Орлов       — эталон амбулаторно (compliant, контроль R-графии)
      2 Соколов       — гость: неверная АБТ (азитромицин)
      3 Морозов       — тяжёлая амбулаторно без АБТ → госпитализация/ОРИТ
      4 Стационаров — стационар OK, динамика, step-down / выписка
      5 Клавуланова — риск АБТ 3 мес, простой амоксициллин (нужен амокс/клав)
      6 Аллергова   — IgE на β-лактамы + ошибочный амоксициллин
      7 Аспиратов   — аспирация + MRSA, мультирежим, исследования
      8 Бронхов     — бронхолитик без обструкции + короткий курс
      9 Контролёв   — 3 визита, АБТ без эффекта (лихорадка)
     10 Пустова     — почти пустая карта (пустые этапы UI)
    """
    stories = []

    # ── 1. Орлов — золотой амбулаторный путь (2 встречи) ──────────────────
    pid = fs.add_patient("Орлов", "Антон", "Петрович", "male", "1985-03-12")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(5), complaint="Кашель с мокротой 4 дня, t 38.2")
    fs.add_condition(pid, "J15.9", "Бактериальная пневмония неуточнённая",
                     onset_date=_ago(5), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "Кашель", "true", "anamnesis", encounter_id=e1)
    _vitals(pid, e1, 5, t=38.2, spo2=96, rr=18, hr=88, sbp=120, dbp=78, wbc=11.2, crp=48)
    _labs_done(pid, e1, 5)
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Очагово-инфильтративные изменения S9 справа",
                             rep_date=_ago(5), encounter_id=e1)
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=5,
         duration_days=7, dose="500 мг", frequency="3 раза в день", dose_per_day=1500)
    fs.finish_encounter(e1, end=_ago(5))
    cps.create_cap_plan(pid)
    e2 = fs.add_encounter(pid, practitioner_id=dr_id, cls="followup",
                          start=_ago(2), complaint="Контроль на 3-и сутки АБТ")
    _vitals(pid, e2, 2, t=36.7, spo2=97, rr=16, hr=76, sbp=118, dbp=76, crp=18)
    fs.finish_encounter(e2, end=_ago(2))
    fs.set_pathway(pid, "controlled", "Выздоровление, контроль")
    stories.append((pid, "Орлов", "эталон амбулаторно, 2 визита"))

    # ── 2. Соколов — гостевой сценарий, неверная АБТ ────────────────────────
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
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Усиление лёгочного рисунка слева",
                             rep_date=_ago(2), encounter_id=e1)
    _med(pid, e1, "J01FA10", "Азитромицин", route="oral", days_ago=2,
         duration_days=5, dose="500 мг", frequency="1 раз в день")
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Терапия ВП (отклонение АБТ)")
    stories.append((pid, "Соколов", "гость: неверная АБТ (макролид)"))

    # ── 3. Морозов — тяжёлая амбулаторно, без АБТ ───────────────────────────
    pid = fs.add_patient("Морозов", "Виктор", "Сергеевич", "male", "1975-04-18")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(1), complaint="Одышка, t 39.5, слабость")
    fs.add_condition(pid, "J18.9", "Пневмония неуточнённая",
                     onset_date=_ago(1), encounter_id=e1)
    _gc(pid, e1, "severe")
    fs.add_flag(pid, "cyanosis", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "consciousness_disorder", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "shock", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "bilateral_infiltration", "true", "exam", encounter_id=e1)
    _vitals(pid, e1, 1, t=39.5, spo2=86, rr=32, hr=118, sbp=85, dbp=55, wbc=22.0, crp=180)
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Двусторонняя инфильтрация — тяжёлая ВП",
                             rep_date=_ago(1), encounter_id=e1)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Тяжёлая ВП — нужна госпитализация")
    stories.append((pid, "Морозов", "тяжёлая амбулаторно → госпитализация"))

    # ── 4. Стационаров — корректный стационар, путь к выписке (2) ─────────
    pid = fs.add_patient("Стационаров", "Павел", "Игоревич", "male", "1972-01-18")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="inpatient",
                          start=_ago(6), complaint="Госпитализация: ВП средней тяжести")
    fs.add_condition(pid, "J15.9", "Бактериальная пневмония неуточнённая",
                     onset_date=_ago(6), encounter_id=e1)
    _gc(pid, e1, "mod_severe")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "Кашель", "true", "anamnesis", encounter_id=e1)
    _vitals(pid, e1, 6, t=38.8, spo2=91, rr=24, hr=102, sbp=110, dbp=70, wbc=14.5, crp=96)
    _labs_done(pid, e1, 6)
    fs.add_service_request(pid, "URINE", "ОАМ", encounter_id=e1,
                           occurrence_date=_ago(6), status="completed")
    fs.add_service_request(pid, "ECG", "ЭКГ", encounter_id=e1,
                           occurrence_date=_ago(6), status="completed")
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Инфильтрация нижней доли слева",
                             rep_date=_ago(6), encounter_id=e1)
    _med(pid, e1, "J01DD04", "Цефтриаксон", route="iv", days_ago=6,
         duration_days=7, dose="2 г", frequency="1 раз в день")
    _med(pid, e1, "J01FA10", "Азитромицин", route="iv", days_ago=6,
         duration_days=5, dose="500 мг", frequency="1 раз в день")
    fs.finish_encounter(e1, end=_ago(2))
    e2 = fs.add_encounter(pid, practitioner_id=dr_id, cls="inpatient",
                          start=_ago(1), complaint="Контроль перед выпиской")
    _gc(pid, e2, "satisfactory")
    _vitals(pid, e2, 1, t=36.6, spo2=96, rr=16, hr=78, sbp=122, dbp=78, crp=22)
    fs.add_service_request(pid, "CXR_REPEAT", "Контрольная R-графия ОГК",
                           encounter_id=e2, occurrence_date=_ago(1), status="active")
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Стационар — улучшение")
    stories.append((pid, "Стационаров", "стационар OK, путь к выписке"))

    # ── 5. Клавуланова — фактор риска АБТ 3 мес, неверный выбор ───────────
    pid = fs.add_patient("Клавуланова", "Мария", "Сергеевна", "female", "1988-05-09")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(3), complaint="Кашель, t 38.0; АБТ месяц назад")
    fs.add_condition(pid, "J15.9", "Бактериальная пневмония неуточнённая",
                     onset_date=_ago(3), encounter_id=e1)
    fs.add_flag(pid, "abt_3mo", "true", "social_risk", encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    # Предшествующая АБТ до onset
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=40,
         duration_days=5, dose="500 мг", frequency="3 раза в день", status="completed")
    _vitals(pid, e1, 3, t=38.0, spo2=96, rr=18, hr=90, sbp=124, dbp=80, wbc=12.0, crp=55)
    _labs_done(pid, e1, 3)
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Инфильтрация справа",
                             rep_date=_ago(3), encounter_id=e1)
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=3,
         duration_days=7, dose="500 мг", frequency="3 раза в день", dose_per_day=1500)
    cps.create_cap_plan(pid)
    stories.append((pid, "Клавуланова", "риск АБТ 3 мес → нужен амокс/клав"))

    # ── 6. Аллергова — IgE β-лактамы + ошибочный пенициллин ───────────────
    pid = fs.add_patient("Аллергова", "Елена", "Викторовна", "female", "1995-09-14")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(2),
                          complaint="Кашель, t 37.9; анафилаксия на пенициллин")
    fs.add_condition(pid, "J18.9", "Пневмония неуточнённая",
                     onset_date=_ago(2), encounter_id=e1)
    fs.add_allergy(pid, "beta-lactam", "β-лактамы", criticality="high",
                   reaction_type="ige", recorded_date=_ago(400))
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    _vitals(pid, e1, 2, t=37.9, spo2=97, rr=17, hr=84, sbp=116, dbp=72, wbc=8.5, crp=36)
    _labs_done(pid, e1, 2)
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Очаговая инфильтрация",
                             rep_date=_ago(2), encounter_id=e1)
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=2,
         duration_days=7, dose="500 мг", frequency="3 раза в день")
    cps.create_cap_plan(pid)
    stories.append((pid, "Аллергова", "IgE на β-лактамы + ошибочный амокс"))

    # ── 7. Аспиратов — аспирация + MRSA, мультирежим (2) ──────────────────
    pid = fs.add_patient("Аспиратов", "Игорь", "Николаевич", "male", "1965-04-28")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="inpatient",
                          start=_ago(8), complaint="Аспирационная пневмония, подозрение MRSA")
    # J18.9 — в зоне применимости CAP; аспирация задаётся флагом (не отдельным МКБ вне PNEUMONIA_CODES)
    fs.add_condition(pid, "J18.9", "Пневмония неуточнённая (аспирационный контекст)",
                     onset_date=_ago(8), encounter_id=e1)
    fs.add_flag(pid, "aspiration_suspicion", "true", "context", encounter_id=e1)
    fs.add_flag(pid, "mrsa_suspicion", "true", "context", encounter_id=e1)
    _gc(pid, e1, "mod_severe")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    _vitals(pid, e1, 8, t=38.6, spo2=90, rr=26, hr=108, sbp=100, dbp=65, wbc=16.0, crp=120)
    _labs_done(pid, e1, 8)
    fs.add_service_request(pid, "SPUTUM_CULTURE", "Посев мокроты", encounter_id=e1,
                           occurrence_date=_ago(8), status="active")
    fs.add_service_request(pid, "BLOOD_CULT", "Посев крови", encounter_id=e1,
                           occurrence_date=_ago(8), status="completed")
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Инфильтрация в зависимых отделах",
                             rep_date=_ago(8), encounter_id=e1)
    fs.add_diagnostic_report(pid, "CT", "КТ ОГК",
                             conclusion="Участки консолидации",
                             rep_date=_ago(7), encounter_id=e1)
    _med(pid, e1, "J01CR02", "Амоксициллин с клавуланатом", route="iv", days_ago=8,
         duration_days=10, dose="1.2 г", frequency="3 раза в день")
    _med(pid, e1, "J01XX08", "Линезолид", route="iv", days_ago=8,
         duration_days=10, dose="600 мг", frequency="2 раза в день")
    fs.finish_encounter(e1, end=_ago(4))
    e2 = fs.add_encounter(pid, practitioner_id=dr_id, cls="inpatient",
                          start=_ago(3), complaint="Контроль терапии")
    _vitals(pid, e2, 3, t=37.2, spo2=94, rr=20, hr=92, sbp=112, dbp=70, crp=68)
    cps.create_cap_plan(pid)
    stories.append((pid, "Аспиратов", "аспирация+MRSA, мультирежим, исследования"))

    # ── 8. Бронхов — бронхолитик без обструкции + короткий курс ───────────
    pid = fs.add_patient("Бронхов", "Олег", "Андреевич", "male", "1980-12-01")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(4),
                          complaint="Кашель, t 37.6; сальбутамол «на всякий»")
    fs.add_condition(pid, "J18.9", "Пневмония неуточнённая",
                     onset_date=_ago(4), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    # Нет bronchial_obstruction / ХОБЛ — бронхолитик лишний
    _vitals(pid, e1, 4, t=37.6, spo2=97, rr=16, hr=78, sbp=120, dbp=76, wbc=8.0, crp=28)
    _labs_done(pid, e1, 4)
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Лёгкая инфильтрация",
                             rep_date=_ago(4), encounter_id=e1)
    _med(pid, e1, "J01FA10", "Азитромицин", route="oral", days_ago=4,
         duration_days=3, dose="500 мг", frequency="1 раз в день")
    _med(pid, e1, "R03AC02", "Сальбутамол", route="inhalation", days_ago=4,
         duration_days=5, dose="100 мкг", frequency="по потребности")
    cps.create_cap_plan(pid)
    stories.append((pid, "Бронхов", "бронхолитик без обструкции + короткий курс"))

    # ── 9. Контролёв — 3 визита, АБТ без эффекта ──────────────────────────
    pid = fs.add_patient("Контролёв", "Андрей", "Павлович", "male", "1978-06-20")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(8), complaint="Первичный осмотр: кашель, t 38.4")
    fs.add_condition(pid, "J15.9", "Бактериальная пневмония неуточнённая",
                     onset_date=_ago(8), encounter_id=e1)
    _gc(pid, e1, "satisfactory")
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=e1)
    fs.add_flag(pid, "Кашель", "true", "anamnesis", encounter_id=e1)
    _vitals(pid, e1, 8, t=38.4, spo2=96, rr=19, hr=92, sbp=122, dbp=78, wbc=11.8, crp=62)
    _labs_done(pid, e1, 8)
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Инфильтрация нижней доли справа",
                             rep_date=_ago(8), encounter_id=e1)
    _med(pid, e1, "J01CA04", "Амоксициллин", route="oral", days_ago=8,
         duration_days=7, dose="500 мг", frequency="3 раза в день", dose_per_day=1500)
    fs.finish_encounter(e1, end=_ago(8))
    e2 = fs.add_encounter(pid, practitioner_id=dr_id, cls="followup",
                          start=_ago(5), complaint="Контроль на 3-и сутки — улучшений нет")
    _vitals(pid, e2, 5, t=38.3, spo2=95, rr=20, hr=96, sbp=120, dbp=76, crp=70)
    fs.finish_encounter(e2, end=_ago(5))
    e3 = fs.add_encounter(pid, practitioner_id=dr_id, cls="followup",
                          start=_ago(1), complaint="Повтор: сохраняется лихорадка на АБТ")
    _vitals(pid, e3, 1, t=38.5, spo2=94, rr=21, hr=98, sbp=118, dbp=74, crp=78)
    cps.create_cap_plan(pid)
    stories.append((pid, "Контролёв", "3 визита: АБТ без эффекта / смена"))

    # ── 10. Пустова — почти пустая карта ──────────────────────────────────
    pid = fs.add_patient("Пустова", "Наталья", "Олеговна", "female", "1992-02-11")
    e1 = fs.add_encounter(pid, practitioner_id=dr_id, cls="ambulatory",
                          start=_ago(0), complaint="Первичный приём: кашель 2 дня")
    fs.add_condition(pid, "J18.9", "Пневмония неуточнённая",
                     onset_date=_ago(0), encounter_id=e1)
    # Только жалоба/диагноз — без виталов, R-графии, АБТ
    cps.create_cap_plan(pid)
    stories.append((pid, "Пустова", "пустая карта: gaps + пустые этапы UI"))

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
            raw = pcap.evaluate_cap(pid)
            v = verdict_for_ui(raw)
            ok = "OK" if v.get("ok") else ("n/a" if not v.get("applicable") else "GAP")
            print(
                f"  {name:14} [{ok:4}] "
                f"CTA={(v.get('cta_label') or '—'):22} | "
                f"{(v.get('headline') or '')[:70]}"
            )
            print(f"                 → {story}")
            warns = [g["code"] for g in raw.get("gaps", []) if g.get("severity") == "warning"]
            if warns:
                print(f"                 warnings: {warns}")
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
    print(f"seeded {len(stories)} patients")

    print("seeding drug catalog…")
    _ensure_drugs()
    n_drugs = len(fs.get_drug_catalog())
    print(f"drug_catalog={n_drugs}")

    print("warming cap_cache…")
    for pid, name, _ in stories:
        cap = pcap.evaluate_cap(pid)
        fs.save_cap_cache(pid, cap)

    _print_verdicts(stories)

    guest = next((s for s in stories if s[1] == "Соколов"), None)
    if guest:
        print(f"\nguest /demo → /patient/{guest[0]}")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
