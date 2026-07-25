"""
Опциональные демо-данные (взрослые пациенты с внебольничной пневмонией, КП №768).

НЕ запускается автоматически. Вызывается явно через fhir_store.seed_demo()
и только если БД пуста. В прод-режиме с реальными данными этот модуль не нужен.
"""
from datetime import date, timedelta
import fhir_store as fs


_DR = ("Терапевт", "Анна", "терапия")

# (family, given, patronymic, gender, birth_date) — взрослые
_PATIENTS = [
    ("Иванов", "Пётр", "Сергеевич", "male", "1985-05-10"),
    ("Коваль", "Ольга", "Ивановна", "female", "1972-03-22"),
    ("Сидоров", "Артём", "Дмитриевич", "male", "1990-01-30"),
    ("Левченко", "Мария", "Павловна", "female", "1960-09-14"),
]


def _d(n):
    return (date.today() + timedelta(days=n)).isoformat()


def seed_all():
    if fs.get_all_patients():
        return

    dr = fs.add_practitioner(*_DR)

    pids = []
    for fam, giv, pat, gen, bd in _PATIENTS:
        pids.append(fs.add_patient(fam, giv, pat, gen, bd))

    # Пациент 1: амбулаторная нетяжёлая ВП, без факторов риска → амоксициллин
    pid = pids[0]
    eid = fs.add_encounter(pid, practitioner_id=dr, cls="ambulatory",
                           complaint="Кашель, лихорадка 3 дня")
    fs.add_condition(pid, "J18.9", "Пневмония неуточненная", onset_date=_d(-2), encounter_id=eid)
    fs.add_observation(pid, "8310-5", "Температура", value_numeric=38.6, value_unit="C", obs_date=_d(-2), encounter_id=eid)
    fs.add_observation(pid, "59408-5", "SpO2", value_numeric=96, value_unit="%", obs_date=_d(-2), encounter_id=eid)
    fs.add_observation(pid, "9279-1", "ЧД", value_numeric=22, value_unit="/мин", obs_date=_d(-2), encounter_id=eid)
    fs.add_service_request(pid, "CBC", "ОАК", encounter_id=eid)
    fs.add_service_request(pid, "CRP", "СРБ", encounter_id=eid)
    fs.add_medication(pid, "J01CA04", "Амоксициллин", route="oral", dose="500 мг",
                      frequency="3 раза в день", med_date=_d(-2), period_end=_d(8), encounter_id=eid)
    fs.set_pathway(pid, "treatment", "Терапия ВП")

    # Пациент 2: тяжёлая ВП (SpO2+ЧД), стационар, MRSA → цефтриаксон + линезолид
    pid = pids[1]
    eid = fs.add_encounter(pid, practitioner_id=dr, cls="inpatient", status="in-progress",
                           complaint="Госпитализация: тяжёлая ВП")
    fs.add_flag(pid, "mrsa_suspicion", "true", "context", encounter_id=eid)
    fs.add_flag(pid, "pleural_effusion", "true", "exam", encounter_id=eid)
    fs.add_condition(pid, "J18.1", "Долевая пневмония", onset_date=_d(-5), encounter_id=eid)
    fs.add_observation(pid, "8310-5", "Температура", value_numeric=39.2, value_unit="C", obs_date=_d(-5), encounter_id=eid)
    fs.add_observation(pid, "59408-5", "SpO2", value_numeric=88, value_unit="%", obs_date=_d(-5), encounter_id=eid)
    fs.add_observation(pid, "9279-1", "ЧД", value_numeric=32, value_unit="/мин", obs_date=_d(-5), encounter_id=eid)
    fs.add_service_request(pid, "CBC", "ОАК", encounter_id=eid)
    fs.add_service_request(pid, "CRP", "СРБ", encounter_id=eid)
    fs.add_service_request(pid, "CXR", "Рентгенография ОГК", encounter_id=eid)
    fs.add_service_request(pid, "BLOOD_CULT", "Гемокультура", encounter_id=eid)
    fs.add_medication(pid, "J01DD04", "Цефтриаксон", route="iv", dose="1–2 г",
                      frequency="1 раз в день", med_date=_d(-5), period_end=_d(5), encounter_id=eid)
    fs.add_medication(pid, "J01XX08", "Линезолид", route="iv", dose="0,6 г",
                      frequency="2 раза в день", med_date=_d(-5), period_end=_d(5), encounter_id=eid)
    fs.set_pathway(pid, "inpatient", "Стационарное лечение ВП")

    # Пациент 3: амбулаторная ВП, IgE-аллергия на β-лактамы → азитромицин
    pid = pids[2]
    fs.add_allergy(pid, "beta-lactam", "Пенициллины", reaction_type="ige")
    eid = fs.add_encounter(pid, practitioner_id=dr, cls="ambulatory",
                           complaint="Кашель, t 38.5")
    fs.add_condition(pid, "J13", "Пневмококковая пневмония", onset_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "8310-5", "Температура", value_numeric=38.5, value_unit="C", obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "59408-5", "SpO2", value_numeric=95, value_unit="%", obs_date=_d(-1), encounter_id=eid)
    fs.add_observation(pid, "9279-1", "ЧД", value_numeric=20, value_unit="/мин", obs_date=_d(-1), encounter_id=eid)
    fs.add_medication(pid, "J01FA10", "Азитромицин", route="oral", dose="500 мг",
                      frequency="1 раз в день", med_date=_d(-1), period_end=_d(4), encounter_id=eid)
    fs.set_pathway(pid, "treatment", "Терапия ВП")

    # Пациент 4: амбулаторная ВП, факторы риска (АБТ за 3 мес) → амокс/клавуланат
    pid = pids[3]
    eid = fs.add_encounter(pid, practitioner_id=dr, cls="ambulatory",
                           complaint="Затяжной кашель, лихорадка")
    fs.add_flag(pid, "abt_3mo", "true", "social_risk", encounter_id=eid)
    fs.add_condition(pid, "J18.0", "Бронхопневмония", onset_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "8310-5", "Температура", value_numeric=38.2, value_unit="C", obs_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "59408-5", "SpO2", value_numeric=94, value_unit="%", obs_date=_d(-3), encounter_id=eid)
    fs.add_observation(pid, "9279-1", "ЧД", value_numeric=24, value_unit="/мин", obs_date=_d(-3), encounter_id=eid)
    fs.add_medication(pid, "J01CR02", "Амоксициллин/клавуланат", route="oral", dose="875/125 мг",
                      frequency="2 раза в день", med_date=_d(-3), period_end=_d(7), encounter_id=eid)
    fs.set_pathway(pid, "treatment", "Терапия ВП")
