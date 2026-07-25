#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Очищает БД и создаёт 10 пациентов с разными сценариями ВП (КП МЗ РБ №768, взрослые),
чтобы увидеть все разделы карты и все исходы оценки по протоколу.

Запуск (на локальной SQLite):
  DATABASE_URL= python3 tools/seed_ten.py
"""
from datetime import date, timedelta

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fhir_store as fs
import care_plan_service as cps


def _d(n):
    return (date.today() + timedelta(days=n)).isoformat()


_DR = ("Терапевт", "Анна", "терапия")


def _patient(fam, giv, pat, gen, bd):
    return fs.add_patient(fam, giv, pat, gen, bd)


def _enc(pid, cls, complaint, start, status="in-progress"):
    return fs.add_encounter(pid, practitioner_id=DR_ID, cls=cls,
                            status=status, start=_d(start), complaint=complaint)


def _obs(pid, code, display, val, unit, day, eid):
    fs.add_observation(pid, code, display, value_numeric=val, value_unit=unit,
                       obs_date=_d(day), encounter_id=eid)


def _sr(pid, code, display, day, eid, status="active"):
    sid = fs.add_service_request(pid, code, display, encounter_id=eid,
                                 occurrence_date=_d(day), status=status)
    return sid


def _rep(pid, code, display, conclusion, day, eid):
    fs.add_diagnostic_report(pid, code, display, conclusion=conclusion,
                             rep_date=_d(day), encounter_id=eid)


def _med(pid, code, name, route, start, end, eid, dose=None, freq=None, dpd=None):
    fs.add_medication(pid, code, name, route=route, dose=dose, frequency=freq,
                      med_date=_d(start), period_end=_d(end), encounter_id=eid,
                      dose_per_day=dpd)


def _flag(pid, key, cat, eid, day=None):
    fs.add_flag(pid, key, value="true", category=cat, encounter_id=eid,
                recorded_date=_d(day) if day else None)


def _cond(pid, code, display, day, eid):
    return fs.add_condition(pid, code, display, onset_date=_d(day), encounter_id=eid)


def _clear():
    for p in fs.get_all_patients():
        fs.delete_patient(p["id"])


DR_ID = None


# P1: эталонный амбулаторный, нетяжёлая, амоксициллин, всё по протоколу.
def p1():
    pid = _patient("Амбулаторов", "Антон", "Петрович", "male", "1985-03-12")
    eid = _enc(pid, "ambulatory", "Кашель, лихорадка 3 дня", -3)
    _cond(pid, "J18.9", "Пневмония неуточненная", -3, eid)
    _obs(pid, "8310-5", "Температура", 38.6, "C", -3, eid)
    _obs(pid, "59408-5", "SpO2", 96, "%", -3, eid)
    _obs(pid, "9279-1", "ЧД", 22, "/мин", -3, eid)
    _obs(pid, "8480-6", "АД систолическое", 120, "mmHg", -3, eid)
    _obs(pid, "8462-4", "АД диастолическое", 78, "mmHg", -3, eid)
    _obs(pid, "30522-7", "СРБ", 48, "mg/L", -3, eid)
    _flag(pid, "local_signs", "exam", eid, -3)
    _sr(pid, "CBC", "ОАК", -3, eid, status="completed")
    _sr(pid, "CRP", "СРБ", -3, eid, status="completed")
    _rep(pid, "CXR", "Рентгенография ОГК", "Очагово-инфильтративные изменения S9 справа", -3, eid)
    _med(pid, "J01CA04", "Амоксициллин", "oral", -3, 7, eid, dose="500 мг", freq="3 раза в день", dpd=1500)
    fs.finish_encounter(eid)
    cps.create_cap_plan(pid)
    e2 = _enc(pid, "followup", "Контроль АБТ через 72 ч", 0)
    _obs(pid, "8310-5", "Температура", 37.0, "C", 0, e2)
    _obs(pid, "59408-5", "SpO2", 97, "%", 0, e2)
    _obs(pid, "9279-1", "ЧД", 18, "/мин", 0, e2)
    _obs(pid, "30522-7", "СРБ", 9, "mg/L", 0, e2)
    fs.finish_encounter(e2)
    fs.set_pathway(pid, "controlled", "Выздоровление, контроль")
    g = fs.get_goals(pid)[0]
    fs.set_goal_status(g["id"], "achieved")


# P2: эталонный стационарный, тяжёлая (плеврит + SpO2 низкий), цефтриаксон в/в, выписан.
def p2():
    pid = _patient("Стационаров", "Сергей", "Иванович", "male", "1978-06-05")
    eid = _enc(pid, "inpatient", "Госпитализация: тяжёлая ВП, плеврит", -8, status="in-progress")
    _flag(pid, "pleurisy", "complication", eid, -8)
    _flag(pid, "pleural_effusion", "exam", eid, -8)
    _flag(pid, "local_signs", "exam", eid, -8)
    _cond(pid, "J18.1", "Долевая пневмония", -8, eid)
    _obs(pid, "8310-5", "Температура", 39.2, "C", -8, eid)
    _obs(pid, "59408-5", "SpO2", 88, "%", -8, eid)
    _obs(pid, "9279-1", "ЧД", 32, "/мин", -8, eid)
    _obs(pid, "8480-6", "АД систолическое", 100, "mmHg", -8, eid)
    _obs(pid, "8462-4", "АД диастолическое", 65, "mmHg", -8, eid)
    _obs(pid, "30522-7", "СРБ", 95, "mg/L", -8, eid)
    _sr(pid, "CBC", "ОАК", -8, eid, status="completed")
    _sr(pid, "CRP", "СРБ", -8, eid, status="completed")
    _sr(pid, "CXR", "Рентгенография ОГК", -8, eid, status="completed")
    _sr(pid, "BLOOD_CULT", "Гемокультура", -8, eid, status="completed")
    _rep(pid, "CXR", "Рентгенография ОГК", "Долевая пневмония справа, выпот в плевральной полости", -8, eid)
    _med(pid, "J01DD04", "Цефтриаксон", "iv", -8, 2, eid, dose="2 г", freq="1 раз в день", dpd=2000)
    fs.finish_encounter(eid)
    cps.create_cap_plan(pid)
    e2 = _enc(pid, "followup", "Выписка — контроль перед уходом", 2)
    _obs(pid, "8310-5", "Температура", 36.8, "C", 2, e2)
    _obs(pid, "59408-5", "SpO2", 96, "%", 2, e2)
    _obs(pid, "9279-1", "ЧД", 20, "/мин", 2, e2)
    _obs(pid, "30522-7", "СРБ", 12, "mg/L", 2, e2)
    fs.finish_encounter(e2)
    fs.set_pathway(pid, "controlled", "Выписан, контроль через 4–6 нед")
    g = fs.get_goals(pid)[0]
    fs.set_goal_status(g["id"], "achieved")


# P3: IgE-аллергия на β-лактамы, но назначен амоксициллин → конфликт (not_first_line_abt).
def p3():
    pid = _patient("Аллергов", "Алиса", "Сергеевна", "female", "1990-02-20")
    fs.add_allergy(pid, "beta-lactam", "Пенициллины", reaction_type="ige")
    eid = _enc(pid, "ambulatory", "Кашель, t 38.5", -1)
    _cond(pid, "J13", "Пневмококковая пневмония", -1, eid)
    _obs(pid, "8310-5", "Температура", 38.5, "C", -1, eid)
    _obs(pid, "59408-5", "SpO2", 95, "%", -1, eid)
    _obs(pid, "9279-1", "ЧД", 24, "/мин", -1, eid)
    _med(pid, "J01CA04", "Амоксициллин", "oral", -1, 9, eid, dose="500 мг", freq="3 раза в день", dpd=1500)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Терапия ВП (коррекция АБТ)")


# P4: амбулаторный, фактор риска (ХОБЛ), цефтриаксон в/в коротким курсом → не та АБТ, в/в, короткий курс.
def p4():
    pid = _patient("Факторов", "Фёдор", "Иванович", "male", "1968-11-30")
    fs.add_condition(pid, "J44", "ХОБЛ", onset_date=_d(-400), encounter_id=None)
    eid = _enc(pid, "ambulatory", "Затяжной кашель, лихорадка, ХОБЛ", -3)
    _cond(pid, "J18.0", "Бронхопневмония", -3, eid)
    _obs(pid, "8310-5", "Температура", 38.2, "C", -3, eid)
    _obs(pid, "59408-5", "SpO2", 94, "%", -3, eid)
    _obs(pid, "9279-1", "ЧД", 26, "/мин", -3, eid)
    _med(pid, "J01DD04", "Цефтриаксон", "iv", -3, 2, eid, dose="1 г", freq="1 раз в день", dpd=1000)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Терапия ВП (отклонения)")


# P5: тяжёлая (SpO2 87, ЧД 32, САД 85), оставлен амбулаторно, АБТ не назначена → госпитализация, ОРИТ, нет АБТ.
def p5():
    pid = _patient("Тяжёлов", "Тимур", "Александрович", "male", "1975-04-18")
    eid = _enc(pid, "ambulatory", "Выраженная одышка, цианоз", -1)
    _flag(pid, "cyanosis", "exam", eid, -1)
    _flag(pid, "consciousness_disorder", "exam", eid, -1)
    _cond(pid, "J18.9", "Пневмония неуточненная", -1, eid)
    _obs(pid, "8310-5", "Температура", 39.0, "C", -1, eid)
    _obs(pid, "59408-5", "SpO2", 87, "%", -1, eid)
    _obs(pid, "9279-1", "ЧД", 32, "/мин", -1, eid)
    _obs(pid, "8480-6", "АД систолическое", 85, "mmHg", -1, eid)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Тяжёлая ВП — нужна госпитализация")


# P6: аспирация + MRSA → карбапенем + линезолид в/в (корректно).
def p6():
    pid = _patient("Аспиратова", "Алина", "Дмитриевна", "female", "1982-09-09")
    eid = _enc(pid, "inpatient", "Тяжёлая ВП, риск аспирации и MRSA", -4)
    _flag(pid, "aspiration_suspicion", "context", eid, -4)
    _flag(pid, "mrsa_suspicion", "context", eid, -4)
    _flag(pid, "local_signs", "exam", eid, -4)
    _cond(pid, "J18.9", "Пневмония неуточненная", -4, eid)
    _obs(pid, "8310-5", "Температура", 39.4, "C", -4, eid)
    _obs(pid, "59408-5", "SpO2", 89, "%", -4, eid)
    _obs(pid, "9279-1", "ЧД", 34, "/мин", -4, eid)
    _sr(pid, "CBC", "ОАК", -4, eid, status="completed")
    _sr(pid, "CRP", "СРБ", -4, eid, status="completed")
    _sr(pid, "CXR", "Рентгенография ОГК", -4, eid, status="completed")
    _sr(pid, "BLOOD_CULT", "Гемокультура", -4, eid, status="completed")
    _med(pid, "J01DH02", "Меропенем", "iv", -4, 6, eid, dose="1 г", freq="3 раза в день", dpd=3000)
    _med(pid, "J01XD01", "Метронидазол", "iv", -4, 6, eid, dose="0,5 г", freq="каждые 8 ч", dpd=1500)
    _med(pid, "J01XX08", "Линезолид", "iv", -4, 6, eid, dose="0,6 г", freq="2 раза в день", dpd=1200)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "inpatient", "Стационар, ВП с факторами резистентности")


# P7: грипп-подозрение, тяжёлая, осельтамивир + цефтриаксон в/в.
def p7():
    pid = _patient("Гриппова", "Галина", "Павловна", "female", "1972-01-15")
    eid = _enc(pid, "inpatient", "Тяжёлая ВП на фоне гриппа", -2)
    _flag(pid, "influenza_suspicion", "context", eid, -2)
    _cond(pid, "J11.0", "Грипп с пневмонией", -2, eid)
    _obs(pid, "8310-5", "Температура", 39.6, "C", -2, eid)
    _obs(pid, "59408-5", "SpO2", 90, "%", -2, eid)
    _obs(pid, "9279-1", "ЧД", 32, "/мин", -2, eid)
    _sr(pid, "CBC", "ОАК", -2, eid)
    _sr(pid, "CRP", "СРБ", -2, eid)
    _sr(pid, "CXR", "Рентгенография ОГК", -2, eid)
    _med(pid, "J01DD04", "Цефтриаксон", "iv", -2, 8, eid, dose="2 г", freq="1 раз в день", dpd=2000)
    _med(pid, "J05AH02", "Осельтамивир", "oral", -2, 3, eid, dose="75 мг", freq="2 раза в день")
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "inpatient", "Стационар, ВП + грипп")


# P8: не-IgE сыпь на макролиды, амоксициллин (верно), но не заказан СРБ/ОАК.
def p8():
    pid = _patient("Кашлев", "Кирилл", "Олегович", "male", "1988-07-25")
    fs.add_allergy(pid, "macrolide", "Макролиды", reaction_type="non-ige")
    eid = _enc(pid, "ambulatory", "Кашель, лихорадка 5 дней", -1)
    _flag(pid, "local_signs", "exam", eid, -1)
    _cond(pid, "J18.0", "Бронхопневмония", -1, eid)
    _obs(pid, "8310-5", "Температура", 37.9, "C", -1, eid)
    _obs(pid, "59408-5", "SpO2", 96, "%", -1, eid)
    _obs(pid, "9279-1", "ЧД", 24, "/мин", -1, eid)
    _med(pid, "J01CA04", "Амоксициллин", "oral", -1, 9, eid, dose="500 мг", freq="3 раза в день", dpd=1500)
    cps.create_cap_plan(pid)
    fs.set_pathway(pid, "treatment", "Терапия ВП (незакрытые исследования)")


# P9: пожилой 65 лет, два приёма (обращение + контроль с улучшением), мониторинг.
def p9():
    pid = _patient("Стариков", "Максим", "Леонидович", "male", "1955-01-24")
    e1 = _enc(pid, "ambulatory", "Кашель, лихорадка 2 дня", -5)
    _cond(pid, "J18.9", "Пневмония неуточненная", -5, e1)
    _obs(pid, "8310-5", "Температура", 38.8, "C", -5, e1)
    _obs(pid, "59408-5", "SpO2", 93, "%", -5, e1)
    _obs(pid, "9279-1", "ЧД", 28, "/мин", -5, e1)
    _obs(pid, "8480-6", "АД систолическое", 135, "mmHg", -5, e1)
    _sr(pid, "CBC", "ОАК", -5, e1, status="completed")
    _sr(pid, "CRP", "СРБ", -5, e1, status="completed")
    _med(pid, "J01CR02", "Амоксициллин/клавуланат", "oral", -5, 5, e1, dose="875/125 мг", freq="2 раза в день", dpd=1750)
    fs.finish_encounter(e1)
    cps.create_cap_plan(pid)
    e2 = _enc(pid, "followup", "Контроль АБТ через 72 ч", 0)
    _obs(pid, "8310-5", "Температура", 37.2, "C", 0, e2)
    _obs(pid, "59408-5", "SpO2", 97, "%", 0, e2)
    _obs(pid, "9279-1", "ЧД", 20, "/мин", 0, e2)
    fs.finish_encounter(e2)
    fs.set_pathway(pid, "monitoring", "Контроль лечения, положительная динамика")


# P10: рецидив — был вылечен, вернулся с ухудшением, коррекция, цель не достигнута.
def p10():
    pid = _patient("Рецидивов", "Роман", "Эдуардович", "male", "1980-12-12")
    e1 = _enc(pid, "ambulatory", "ВП месяц назад — вылечен", -35)
    _cond(pid, "J18.9", "Пневмония неуточненная", -35, e1)
    _med(pid, "J01CA04", "Амоксициллин", "oral", -35, -25, e1, dose="500 мг", freq="3 раза в день", dpd=1500)
    fs.finish_encounter(e1)
    cps.create_cap_plan(pid)
    g = fs.get_goals(pid)[0]
    fs.set_goal_status(g["id"], "achieved", _d(-25))
    e2 = _enc(pid, "followup", "Возвращение: кашель усилился снова", -1)
    _flag(pid, "local_signs", "exam", e2, -1)
    _cond(pid, "J18.9", "Пневмония неуточненная", -1, e2)
    _obs(pid, "8310-5", "Температура", 38.4, "C", -1, e2)
    _obs(pid, "59408-5", "SpO2", 95, "%", -1, e2)
    _obs(pid, "9279-1", "ЧД", 28, "/мин", -1, e2)
    fs.set_goal_status(g["id"], "not-achieved")
    fs.set_pathway(pid, "adjustment", "Рецидив — коррекция плана")


def main():
    global DR_ID
    fs.init_db()
    _clear()
    DR_ID = fs.add_practitioner(*_DR)
    p1(); p2(); p3(); p4(); p5(); p6(); p7(); p8(); p9(); p10()
    print("Создано пациентов: %d" % len(fs.get_all_patients()))


if __name__ == "__main__":
    main()
