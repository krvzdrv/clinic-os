"""
Слой 3b — Регламент лечения внебольничной пневмонии (дети), КП МЗ РБ №204 от 18.12.2023.

Параллельный профиль к protocol_engine (АГ). Тот же принцип: независимый
валидатор, который проверяет совокупность ресурсов пациента на соответствие
клиническому протоколу. Не подсказывает в момент одного действия (это CDS),
а оценивает всю картину: диагноз + тяжесть + обязательные исследования +
выбор АБТ + оценка эффективности + длительность курса + показания к
госпитализации.

Машинно-проверяемое подмножество протокола (с указанием пунктов):
  - п.6.3 + приложение — класс тяжести (средняя / тяжёлая) по SpO2 и ДН;
  - п.11 — обязательные исследования: ОАК, СРБ, пульсоксиметрия, термометрия;
  - п.12 — показания к рентгенографии ОГК;
  - п.15–21 — выбор АБТ первой линии (амоксициллин / амоксициллин-клавуланат /
    макролид / цефуроксим) с учётом возраста, факторов риска и аллергии;
  - п.15, 30 — оценка эффективности АБТ через 48–72 ч (лихорадка <38, СРБ);
  - п.15 — длительность курса 10–14 дней (средняя тяжесть);
  - п.26 — показания к госпитализации (возраст <1 г, ДН II+, тахипноэ/тахикардия
    по возрасту, тяжёлый фон, отсутствие эффекта через 48–72 ч).

Что НЕ учтено (упрощения для демо — отмечаем честно):
  - локальность/асимметрия аускультативных и перкуторных данных (нет структурированного
    поля физикального осмотра) — поэтому показание к R-графии «локальные изменения»
    не проверяется;
  - IgE vs не-IgE гиперчувствительность к β-лактамам не различаются — цефуроксим
    выдаётся как альтернатива с пометкой «осторожно», а не как жёсткая замена;
  - социальные факторы риска (посещение детсада, контакт с детсадовцами, путешествия,
    проживание в интернате) не структурированы — учитываются только медицинские
    (предшествующая АБТ за 3 мес, хронические болезни лёгких, СД).

Главный вход: evaluate_cap(pid) → {applicable, severity, hospitalization,
expected_atb, compliant, gaps}.
"""
from datetime import datetime, date

import fhir_store as fs
import rules_engine as re
import drug_service
from terminology import (
    PNEUMONIA_CODES, atc_group, atc_drug_display,
    TEMP_CODE, SPO2_CODE, WBC_CODE, CRP_CODE,
    PARENTERAL_ONLY,
)

PROTOCOL_REF = "КП МЗ РБ №204 от 18.12.2023 (Внебольничная пневмония, дети)"


# ---- Тяжесть (п.6.3 + приложение) ----

def classify_severity(pid):
    """
    Средняя тяжесть — признаки пневмонии без ДН или с ДН I (SpO2 ≥ 90).
    Тяжёлая — SpO2 < 90 (центральный цианоз / ДН II–IV) либо осложнения.
    Возвращает 'moderate' | 'severe' | None (если пневмонии нет).
    """
    if not re.has_pneumonia(pid):
        return None
    spo2 = re.latest_spo2(pid)
    if spo2 is not None and spo2 < 90:
        return "severe"
    # ДН II+ по SpO2 75–89 тоже тяжёлая
    if spo2 is not None and spo2 < 90:
        return "severe"
    return "moderate"


# ---- Показания к госпитализации (п.26) ----

def hospitalization_reasons(pid):
    """Список причин для госпитализации по п.26 КП №204."""
    reasons = []
    if not re.has_pneumonia(pid):
        return reasons

    if re.age_years(pid) < 1:
        reasons.append("возраст ребёнка до 1 года (п.26.1)")

    if re.dn_degree(pid) and re.dn_degree(pid) >= 2:
        reasons.append(f"ДН II+ (SpO2 {re.latest_spo2(pid)}%, п.26.3)")

    if re.is_tachypneic(pid):
        reasons.append(
            f"тахипноэ: ЧД {re.latest_rr(pid)} > {re.tachypnea_threshold(pid)} "
            f"для возраста (п.26.3)"
        )

    if re.is_tachycardic(pid):
        reasons.append(
            f"тахикардия: ЧСС {re.latest_hr(pid)} > {re.tachycardia_threshold(pid)} "
            f"для возраста (п.26.4)"
        )

    if re.has_chronic_lung_disease(pid):
        reasons.append("тяжёлый фон: хронические болезни лёгких (п.26.7)")
    if re.has_diabetes(pid):
        reasons.append("тяжёлый фон: сахарный диабет (п.26.7)")

    # Отсутствие эффекта через 48–72 ч — лихорадка сохраняется ПОСЛЕ контрольной оценки.
    abt = _earliest_active_antibiotic(pid)
    if abt:
        days = (date.today() - _parse_date(abt["date"])).days
        if days >= 3 and re.has_fever(pid) and _has_observation_after(pid, [TEMP_CODE], abt["date"]):
            reasons.append("нет эффекта АБТ через 72 ч: лихорадка сохраняется (п.26.8)")

    return reasons


# ---- Выбор АБТ первой линии (п.15–21) ----

def _risk_factors(pid):
    """Факторы риска инфицирования резистентными/β-лактамазообразующими возбудителями."""
    factors = []
    if re.antibiotics_in_last_3mo(pid):
        factors.append("АБТ в предшествующие 3 мес (п.17)")
    if re.has_chronic_lung_disease(pid):
        factors.append("хронические болезни лёгких (п.18)")
    if re.has_diabetes(pid):
        factors.append("сахарный диабет (п.17)")
    return factors


def expected_antibiotic(pid):
    """
    Возвращает ожидаемую первую линию АБТ для амбулаторного лечения ВП
    средней тяжести: {atc_group, atc_code, name, rationale, ref}.
    """
    if drug_service.has_allergy_class(pid, "beta-lactam"):
        return {
            "atc_group": "J01FA", "atc_code": "J01FA09",
            "name": atc_drug_display("J01FA09"),
            "rationale": "Аллергия на β-лактамы → макролид (кларитромицин/азитромицин).",
            "ref": "п.19 КП №204",
        }
    if re.age_years(pid) <= 2 or _risk_factors(pid):
        return {
            "atc_group": "J01CR", "atc_code": "J01CR02",
            "name": atc_drug_display("J01CR02"),
            "rationale": "Возраст ≤2 лет и/или факторы риска резистентности → "
                         "амоксициллин/клавулановая кислота.",
            "ref": "п.17–18 КП №204",
        }
    return {
        "atc_group": "J01CA", "atc_code": "J01CA04",
        "name": atc_drug_display("J01CA04"),
        "rationale": "Без факторов риска, возраст >2 лет → амоксициллин.",
        "ref": "п.16 КП №204",
    }


# ---- Вспомогательные ----

def _earliest_active_antibiotic(pid):
    meds = [m for m in fs.get_medications(pid) if m["code"].startswith("J01")]
    if not meds:
        return None
    meds = sorted(meds, key=lambda m: m["date"])
    return meds[0]


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def _has_service_request(pid, code):
    return any(sr["code"] == code for sr in fs.get_service_requests(pid))


def _has_observation(pid, code):
    return fs.get_last_observation(pid, code) is not None


# ---- Главная функция ----

def evaluate_cap(pid):
    """
    Полная оценка соответствия протоколу ВП (КП №204) для пациента.
    Возвращает {applicable, severity, hospitalization, expected_atb, compliant, gaps}.
    compliant = True если нет warning-уровневых gap'ов.
    """
    if not re.has_pneumonia(pid):
        return {"applicable": False, "compliant": True, "gaps": []}

    gaps = []
    severity = classify_severity(pid)
    hosp = hospitalization_reasons(pid)
    expected = expected_antibiotic(pid)

    # 1. Показания к госпитализации — приоритетная проверка
    if hosp:
        gaps.append({
            "severity": "warning",
            "code": "hospitalization_indicated",
            "message": "Есть показания к госпитализации: " + "; ".join(hosp),
            "recommendation": "Госпитализация в больничную организацию (п.26).",
        })

    # 2. Обязательные исследования (п.11)
    if not (_has_service_request(pid, "CBC") or _has_observation(pid, WBC_CODE)):
        gaps.append({
            "severity": "warning", "code": "missing_cbc",
            "message": "Не назначен/не выполнен общий анализ крови (ОАК).",
            "recommendation": "Назначить ОАК двукратно (п.11.2).",
        })
    if not (_has_service_request(pid, "CRP") or _has_observation(pid, CRP_CODE)):
        gaps.append({
            "severity": "warning", "code": "missing_crp",
            "message": "Не определён С-реактивный белок (СРБ).",
            "recommendation": "Назначить СРБ — критерий эффективности АБТ (п.11.2, п.15).",
        })
    if not _has_observation(pid, SPO2_CODE):
        gaps.append({
            "severity": "warning", "code": "missing_spo2",
            "message": "Не выполнена пульсоксиметрия (SpO2).",
            "recommendation": "Пульсоксиметрия при каждом осмотре (п.11.3).",
        })
    if not _has_observation(pid, TEMP_CODE):
        gaps.append({
            "severity": "info", "code": "missing_temp",
            "message": "Нет записи температуры тела.",
            "recommendation": "Термометрия при каждом медицинском осмотре (п.11.3).",
        })

    # 3. Рентгенография ОГК по показаниям (п.12)
    cxr_ordered = _has_service_request(pid, "CXR") or any(
        r["code"] == "CXR" for r in fs.get_diagnostic_reports(pid)
    )
    if severity == "severe" and not cxr_ordered:
        gaps.append({
            "severity": "warning", "code": "cxr_indicated",
            "message": "Тяжёлая ВП (SpO2<90) — показана рентгенография ОГК, но она не назначена.",
            "recommendation": "Назначить R-графию ОГК в прямой проекции (п.12).",
        })

    # 4. Антибактериальная терапия
    abt = _earliest_active_antibiotic(pid)
    if not abt:
        gaps.append({
            "severity": "warning", "code": "no_abt",
            "message": "Внебольничная пневмония диагностирована, АБТ не назначена.",
            "recommendation": f"Назначить АБТ первой линии: {expected['name']} ({expected['ref']}).",
        })
    else:
        # 4a. Соответствие первой линии
        grp, _ = atc_group(abt["code"])
        if grp != expected["atc_group"]:
            gaps.append({
                "severity": "warning", "code": "not_first_line_abt",
                "message": (
                    f"Назначен {atc_drug_display(abt['code'])} (группа {grp}), "
                    f"по протоколу первая линия — {expected['name']} (группа {expected['atc_group']})."
                ),
                "recommendation": f"{expected['rationale']} ({expected['ref']}).",
            })

        # 4b. Маршрут: в амбулаторных условиях — перорально (п.15)
        if abt["code"] in PARENTERAL_ONLY:
            gaps.append({
                "severity": "warning", "code": "parenteral_in_outpatient",
                "message": (
                    f"{atc_drug_display(abt['code'])} — парентеральный препарат, "
                    "в амбулаторных условиях АБП назначаются перорально."
                ),
                "recommendation": "Перейти на пероральную форму или госпитализировать (п.15, п.30).",
            })

        # 4c. Оценка эффективности через 48–72 ч (п.15, п.30)
        days = (date.today() - _parse_date(abt["date"])).days
        if days >= 3:
            reassess = _has_observation_after(pid, [TEMP_CODE, SPO2_CODE], abt["date"])
            if not reassess:
                gaps.append({
                    "severity": "warning", "code": "no_reassessment",
                    "message": f"Прошло {days} дн. от начала АБТ, нет контрольной записи (t°/SpO2).",
                    "recommendation": "Оценить эффективность АБТ через 48–72 ч (п.15, п.30).",
                })
            elif re.has_fever(pid):
                gaps.append({
                    "severity": "warning", "code": "abt_no_effect",
                    "message": "На фоне АБТ сохраняется лихорадка ≥38 °C — нет эффекта.",
                    "recommendation": "Госпитализация или смена АБП (п.15, п.30, п.26.8).",
                })

        # 4d. Длительность курса 10–14 дней (п.15)
        if abt.get("period_start") and abt.get("period_end"):
            dur = (_parse_date(abt["period_end"]) - _parse_date(abt["period_start"])).days
            if dur < 10:
                gaps.append({
                    "severity": "warning", "code": "course_too_short",
                    "message": f"Курс АБТ {dur} дн. — короче протокольных 10–14 дней.",
                    "recommendation": "Продолжить курс до 10–14 дней при средней тяжести (п.15).",
                })
            elif dur > 14:
                gaps.append({
                    "severity": "info", "code": "course_too_long",
                    "message": f"Курс АБТ {dur} дн. — длиннее 14 дней.",
                    "recommendation": "Обосновать продление (осложнённая/тяжёлая ВП).",
                })
        else:
            gaps.append({
                "severity": "info", "code": "course_not_set",
                "message": "Не задана дата окончания курса АБТ.",
                "recommendation": "Указать period_end: курс 10–14 дней при средней тяжести (п.15).",
            })

    compliant = not any(g["severity"] == "warning" for g in gaps)
    return {
        "applicable": True,
        "severity": severity,
        "hospitalization": hosp,
        "expected_atb": expected,
        "compliant": compliant,
        "gaps": gaps,
        "protocol": PROTOCOL_REF,
    }


def _has_observation_after(pid, codes, after_iso):
    """Есть ли observation с указанным кодом и датой строго позже after_iso."""
    for code in codes:
        o = fs.get_last_observation(pid, code)
        if o and o["date"] > after_iso:
            return True
    return False
