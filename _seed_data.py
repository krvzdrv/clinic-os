"""
Опциональные демо-данные (взрослые пациенты с внебольничной пневмонией, КП №768).

НЕ запускается автоматически. Вызывается явно через fhir_store.seed_demo()
и только если БД пуста. В прод-режиме с реальными данными этот модуль не нужен.

Три пациента для ручного демо (A/B/C):
  Орлов — ведение по протоколу
  Соколов — неверная АБТ (азитромицин вместо амоксициллина)
  Морозов — тяжёлая амбулаторно, АБТ не назначена
"""
from datetime import date, timedelta

import care_plan_service as cps
import fhir_store as fs


_DR = ("Терапевт", "Анна", "терапия")


def _d(n):
    return (date.today() + timedelta(days=n)).isoformat()


def seed_all():
    if fs.get_all_patients():
        return

    dr = fs.add_practitioner(*_DR)

    # A — соответствует протоколу: амбулаторная нетяжёлая ВП, амоксициллин per os
    pid = fs.add_patient("Орлов", "Антон", "Петрович", "male", "1985-03-12")
    eid = fs.add_encounter(pid, practitioner_id=dr, cls="ambulatory",
                           complaint="Кашель, лихорадка 3 дня")
    fs.add_condition(pid, "J18.9", "Пневмония неуточненная", onset_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "8310-5", "Температура", value_numeric=38.6, value_unit="C",
                       obs_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "59408-5", "SpO2", value_numeric=96, value_unit="%",
                       obs_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "9279-1", "ЧД", value_numeric=22, value_unit="/min",
                       obs_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "8480-6", "АД систолическое", value_numeric=120, value_unit="mmHg",
                       obs_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "8462-4", "АД диастолическое", value_numeric=78, value_unit="mmHg",
                       obs_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "30522-7", "СРБ", value_numeric=48, value_unit="mg/L",
                       obs_date=_d(-3), encounter_id=eid)
    fs.add_flag(pid, "local_signs", "true", "exam", encounter_id=eid)
    fs.add_service_request(pid, "CBC", "ОАК", encounter_id=eid, status="completed")
    fs.add_service_request(pid, "CRP", "СРБ", encounter_id=eid, status="completed")
    fs.add_diagnostic_report(pid, "CXR", "Рентгенография ОГК",
                             conclusion="Очагово-инфильтративные изменения S9 справа",
                             rep_date=_d(-3), encounter_id=eid)
    fs.add_medication(pid, "J01CA04", "Амоксициллин", route="oral", dose="500 мг",
                      frequency="3 раза в день", med_date=_d(-3), period_end=_d(7),
                      encounter_id=eid, dose_per_day=1500)
    fs.finish_encounter(eid)
    cps.create_cap_plan(pid)
    e2 = fs.add_encounter(pid, practitioner_id=dr, cls="followup",
                          complaint="Контроль АБТ через 72 ч")
    fs.add_observation(pid, "8310-5", "Температура", value_numeric=37.0, value_unit="C",
                       obs_date=_d(0), encounter_id=e2)
    fs.add_observation(pid, "59408-5", "SpO2", value_numeric=97, value_unit="%",
                       obs_date=_d(0), encounter_id=e2)
    fs.add_observation(pid, "9279-1", "ЧД", value_numeric=18, value_unit="/min",
                       obs_date=_d(0), encounter_id=e2)
    fs.add_observation(pid, "30522-7", "СРБ", value_numeric=9, value_unit="mg/L",
                       obs_date=_d(0), encounter_id=e2)
    fs.finish_encounter(e2)
    fs.set_pathway(pid, "controlled", "Выздоровление, контроль")
    g = fs.get_goals(pid)[0]
    fs.set_goal_status(g["id"], "achieved")

    # B — неверная АБТ: азитромицин вместо амоксициллина (нет факторов риска, нет аллергии).
    # ОАК/СРБ/SpO2 заполнены, чтобы главная подсказка была про препарат, не про анализы.
    pid = fs.add_patient("Соколов", "Борис", "Иванович", "male", "1978-06-05")
    eid = fs.add_encounter(pid, practitioner_id=dr, cls="ambulatory",
                           complaint="Кашель, t 38.5")
    fs.add_condition(pid, "J18.9", "Пневмония неуточненная", onset_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "8310-5", "Температура", value_numeric=38.5, value_unit="C",
                       obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "59408-5", "SpO2", value_numeric=96, value_unit="%",
                       obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "9279-1", "ЧД", value_numeric=22, value_unit="/min",
                       obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "6690-2", "Лейкоциты", value_numeric=9.5, value_unit="10^9/L",
                       obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "30522-7", "СРБ", value_numeric=42, value_unit="mg/L",
                       obs_date=_d(-1), encounter_id=eid)
    fs.add_service_request(pid, "CBC", "ОАК", encounter_id=eid, status="completed")
    fs.add_service_request(pid, "CRP", "СРБ", encounter_id=eid, status="completed")
    fs.add_medication(pid, "J01FA10", "Азитромицин", route="oral", dose="500 мг",
                      frequency="1 раз в день", med_date=_d(-1), period_end=_d(9),
                      encounter_id=eid)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Терапия ВП (отклонение АБТ)")

    # C — тяжёлая амбулаторно, АБТ не назначена → госпитализация
    pid = fs.add_patient("Морозов", "Виктор", "Сергеевич", "male", "1975-04-18")
    eid = fs.add_encounter(pid, practitioner_id=dr, cls="ambulatory",
                           complaint="Выраженная одышка, цианоз")
    fs.add_flag(pid, "cyanosis", "true", "exam", encounter_id=eid)
    fs.add_flag(pid, "consciousness_disorder", "true", "exam", encounter_id=eid)
    fs.add_condition(pid, "J18.9", "Пневмония неуточненная", onset_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "8310-5", "Температура", value_numeric=39.0, value_unit="C",
                       obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "59408-5", "SpO2", value_numeric=87, value_unit="%",
                       obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "9279-1", "ЧД", value_numeric=32, value_unit="/min",
                       obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "8480-6", "АД систолическое", value_numeric=85, value_unit="mmHg",
                       obs_date=_d(-1), encounter_id=eid)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Тяжёлая ВП — нужна госпитализация")
