"""
Слой 3b — Регламент лечения внебольничной пневмонии (взрослые), КП МЗ РБ №768 от 05.07.2012.

Полная версия: амбулаторный + стационарный блоки, структурированный анамнез/осмотр.

Машинно-проверяемое подмножество протокола (КП №768, взрослое население):
  АМБУЛАТОРНЫЙ БЛОК
  - тяжесть: нетяжёлая (лёгкая + средней тяжести) / тяжёлая (≥2 «малых» или ≥1 «большой» критерий);
  - обязательные исследования: ОАК, СРБ, пульсоксиметрия, термометрия;
  - показания к рентгенографии ОГК (в т.ч. локальные знаки при осмотре);
  - длительность курса 7–14 дней; пероральный маршрут;
  - оценка эффективности АБТ через 48–72 ч (лихорадка <38, снижение СРБ);
  - амбулаторно: амоксициллин/клавуланат 875/125 мг 2 р/сут (при факторах риска) или амоксициллин;
  - при IgE-аллергии на β-лактамы → макролид (азитромицин 500 мг);
  - при не-IgE гиперчувствительности → цефуроксим (осторожно);
  - показания к госпитализации (ЧД≥30, САД<90, ДАД≤60, ЧСС≥125, t°<35,5/≥39,9, лейкоциты<4/>20,
    SaO2<92, креатинин>176,7, мочевина>7, инфильтрация>1 доли, полости, выпот, Hb<90,
    возраст>60, сопутствующие, неэффективность АБТ, беременность).

  СТАЦИОНАРНЫЙ БЛОК
  - показания к ОРИТ (≥2 «малых» или ≥1 «большой» критерий: ИВЛ, быстрое прогрессирование,
    септический шок/вазопрессоры≥4ч, ОПН);
  - старт АБТ в/в: цефтриаксон 1–2 г 1 р/сут (или амокс/клавуланат, цефуроксим в/в);
  - тяжёлая ВП → цефалоспорин III + макролид;
  - MRSA → линезолид/ванкомицин;
  - аспирация → амокс/клавуланат 1,2 г в/в или карбапенем + метронидазол;
  - резерв → респираторные фторхинолоны (левофлоксацин 0,75 г, моксифлоксацин 0,4 г);
  - ступенчатая терапия: переход в/в → per os при клиническом ответе;
  - критерии выписки (нормотермия, ЧД/SpO2 в норме).

Главный вход: evaluate_cap(pid) → {applicable, setting, severity, hospitalization,
expected_regimen, compliant, gaps, protocol}.
"""
from datetime import datetime, date

import fhir_store as fs
import rules_engine as re
import drug_service
import protocol_rules
from terminology import (
    PNEUMONIA_CODES, atc_group, atc_drug_display,
    TEMP_CODE, SPO2_CODE, WBC_CODE, CRP_CODE, RR_CODE, HR_CODE,
    PARENTERAL_ONLY, ORAL_ANTIBIOTICS,
    general_condition_display, general_condition_needs_inpatient,
    adult_dose,
    format_expected_dose,
    abt_equivalent,
)

PROTOCOL_REF = "КП МЗ РБ №768 от 05.07.2012 (Внебольничная пневмония, взрослое население)"


# ---- Тяжесть (КП №768: малые/большие критерии тяжёлого течения) ----

def classify_severity(pid):
    """
    Нетяжёлая — пневмония лёгкого или средней тяжести (объединены в КП №768 в одну группу).
    Тяжёлая — ≥2 «малых» либо ≥1 «большой» критерий тяжёлого течения (КП №768).
    Возвращает 'mild' | 'severe' | None (если пневмонии нет).
    """
    if not re.has_pneumonia(pid):
        return None
    if re.large_severe_criteria(pid):
        return "severe"
    if len(re.small_severe_criteria(pid)) >= 2:
        return "severe"
    return "mild"


def _setting(pid):
    """'inpatient' если есть стационарный приём (любой статус — активный или выписанный), иначе 'outpatient'."""
    return re.encounter_setting(pid)


# ---- Показания к госпитализации (КП №768, взрослые) ----

def hospitalization_reasons(pid):
    """Список причин для госпитализации по КП №768 (взрослые)."""
    reasons = []
    if not re.has_pneumonia(pid):
        return reasons

    # 1. Данные физического обследования (острые/пиковые значения — тяжесть при поступлении)
    rr = re.worst_rr(pid)
    if rr is not None and rr >= 30:
        reasons.append(f"тахипноэ: ЧД {rr} ≥30/мин")
    sbp = re.worst_sbp(pid)
    if sbp is not None and sbp < 90:
        reasons.append(f"САД {sbp} <90 мм рт.ст.")
    dbp = re.worst_dbp(pid)
    if dbp is not None and dbp <= 60:
        reasons.append(f"ДАД {dbp} ≤60 мм рт.ст.")
    hr = re.worst_hr(pid)
    if hr is not None and hr >= 125:
        reasons.append(f"тахикардия: ЧСС {hr} ≥125/мин")
    t = re.worst_temp(pid)
    if t is not None and (t < 35.5 or t >= 39.9):
        reasons.append(f"температура {t}°C (<35,5 или ≥39,9)")
    if re.has_clinical_flag(pid, "consciousness_disorder"):
        reasons.append("нарушение сознания")

    # 2. Лабораторные и рентгенологические данные (острые значения)
    wbc = re.worst_wbc(pid)
    if wbc is not None and (wbc < 4.0 or wbc > 20.0):
        reasons.append(f"лейкоциты {wbc}×10⁹/л (<4,0 или >20,0)")
    s = re.worst_spo2(pid)
    if s is not None and s < 92:
        reasons.append(f"SaO2 {s}% (<92%)")
    cr = re.worst_creat(pid)
    if cr is not None and cr > 176.7:
        reasons.append(f"креатинин {cr} мкмоль/л (>176,7)")
    u = re.worst_urea(pid)
    if u is not None and u > 7.0:
        reasons.append(f"мочевина {u} ммоль/л (>7,0)")
    hb = re.worst_hb(pid)
    if hb is not None and hb < 90:
        reasons.append(f"гемоглобин {hb} г/л (<90)")
    if re.has_clinical_flag(pid, "bilateral_infiltration"):
        reasons.append("двусторонняя/многодолевая инфильтрация")
    if re.has_clinical_flag(pid, "cavity"):
        reasons.append("полости распада")
    if re.has_clinical_flag(pid, "pleural_effusion"):
        reasons.append("плевральный выпот")

    # 3. Невозможность адекватного ухода дома — по социальным флагам
    if re.has_clinical_flag(pid, "alcoholism") or re.has_clinical_flag(pid, "drug_addiction"):
        reasons.append("невозможность адекватного ухода дома (алкоголизм/наркомания)")

    # Экстренные/неотложные признаки
    if re.has_clinical_flag(pid, "shock"):
        reasons.append("шок / сепсис")

    # Общее состояние по оценке врача: тяжёлое/крайне тяжёлое — показание к госпитализации.
    gc = re.general_condition(pid)
    if gc and general_condition_needs_inpatient(gc):
        reasons.append(f"общее состояние: {general_condition_display(gc)} — показание к госпитализации")

    # Отсутствие эффекта через 48–72 ч — лихорадка сохраняется.
    abt = _earliest_active_antibiotic(pid)
    if abt:
        days = (date.today() - _parse_date(abt["date"])).days
        if days >= 3 and re.has_fever(pid) and _has_observation_after(pid, [TEMP_CODE], abt["date"]):
            reasons.append("нет эффекта АБТ через 72 ч: лихорадка сохраняется")

    return reasons


def prefer_inpatient_reasons(pid):
    """Предпочтительность стационарного лечения (КП №768) — не жёсткие показания,
    а факторы, при которых стационар предпочтительнее амбулаторного. Отдельно от
    hospitalization_reasons, чтобы не уводить нетяжёлую ВП в стационар автоматически."""
    pref = []
    if not re.has_pneumonia(pid):
        return pref
    if re.age_years(pid) > 60:
        pref.append(f"возраст старше 60 лет ({re.age_years(pid)})")
    if re.has_chronic_lung_disease(pid):
        pref.append("сопутствующее: хронические болезни лёгких/ХОБЛ")
    if re.has_diabetes(pid):
        pref.append("сопутствующее: сахарный диабет")
    if re.has_ckd(pid):
        pref.append("сопутствующее: хроническая почечная недостаточность")
    if re.has_clinical_flag(pid, "pregnancy"):
        pref.append("беременность")
    return pref


# ---- Выбор АБТ (SSOT: docs/protocols/cap_abt_rules.yaml) ----

def _risk_factors(pid):
    """Человекочитаемый список факторов риска (для текстов gap'ов / UI)."""
    factors = []
    if re.antibiotics_in_last_3mo(pid):
        factors.append("АБТ в предшествующие 3 мес")
    if re.has_clinical_flag(pid, "hospitalized_3mo"):
        factors.append("госпитализация в предшествующие 3 мес")
    if re.has_chronic_lung_disease(pid):
        factors.append("хронические болезни лёгких/ХОБЛ")
    if re.has_diabetes(pid):
        factors.append("сахарный диабет")
    if re.has_clinical_flag(pid, "immunosuppression"):
        factors.append("иммуносупрессия")
    return factors


def expected_antibiotic(pid):
    """Ожидаемая амбулаторная АБТ — по YAML-правилам (не по drug_catalog.protocol_role)."""
    return protocol_rules.select_outpatient(pid)


def expected_inpatient_regimen(pid, severity=None):
    """Ожидаемый стационарный режим АБТ — по YAML-правилам."""
    return protocol_rules.select_inpatient(pid, severity=severity)


def evaluate_abt_choice(pid, atc_code):
    """
    Проверка выбираемого АБТ ДО сохранения (order-sign).

    Возвращает список issues (как drug_service): пусто — можно сохранять;
    warning not_first_line_abt — soft-stop (чекбокс + опц. причина).
    Не-J01 и пациенты без ВП — без замечаний.
    """
    code = (atc_code or "").strip().upper()
    if not code.startswith("J01"):
        return []
    if not re.has_pneumonia(pid):
        return []

    severity = classify_severity(pid)
    hosp = hospitalization_reasons(pid)
    encounter_setting = _setting(pid)
    needed_inpatient = (severity == "severe") or bool(hosp)
    setting = "inpatient" if needed_inpatient else encounter_setting

    if setting == "inpatient":
        expected = expected_inpatient_regimen(pid, severity)
        primary = expected.get("primary") or {}
        allowed_groups = set()
        names = []
        pcode = (primary.get("atc_code") or "").upper()
        if pcode:
            grp, _ = atc_group(pcode)
            if grp:
                allowed_groups.add(grp)
            names.append(primary.get("name") or atc_drug_display(pcode))
        for addon in expected.get("addons") or []:
            ac = (addon.get("atc_code") or "").upper()
            if not ac:
                continue
            grp, _ = atc_group(ac)
            if grp:
                allowed_groups.add(grp)
            names.append(addon.get("name") or atc_drug_display(ac))
        grp_sel, _ = atc_group(code)
        if grp_sel in allowed_groups:
            return []
        expect_txt = ", ".join(names) if names else "режим по КП №768"
        return [{
            "severity": "warning",
            "category": "not_first_line_abt",
            "message": (
                f"Препарат {atc_drug_display(code)} не соответствует протоколу "
                f"для этого пациента. Ожидается: {expect_txt}."
            ),
        }]

    expected = expected_antibiotic(pid)
    exp_grp = expected.get("atc_group")
    grp_sel, _ = atc_group(code)
    if exp_grp and grp_sel == exp_grp:
        return []
    exp_name = expected.get("name") or atc_drug_display(expected.get("atc_code") or "")
    return [{
        "severity": "warning",
        "category": "not_first_line_abt",
        "message": (
            f"Препарат {atc_drug_display(code)} не соответствует протоколу "
            f"для этого пациента. По КП №768 ожидается {exp_name}."
        ),
    }]


def icu_criteria(pid):
    """Показания к переводу в ОРИТ (КП №768). Возвращает список причин."""
    crit = []
    large = re.large_severe_criteria(pid)
    crit.extend(large)
    small = re.small_severe_criteria(pid)
    if len(small) >= 2 and not large:
        crit.append("≥2 «малых» критерия тяжёлого течения")
    if re.has_clinical_flag(pid, "shock"):
        if not any("шок" in c for c in crit):
            crit.append("септический шок / вазопрессоры ≥4 ч")
    return crit


def discharge_criteria(pid):
    """
    Критерии выписки (КП №768). Возвращает {met: bool, missing: [...]}.
    Оценивается по последним наблюдениям: нормотермия, ЧД в норме, SpO2 >= 95.
    """
    missing = []
    t = re.latest_temp(pid)
    if t is None or t >= 38.0:
        missing.append("нормотермия (<38 °C) не достигнута")
    rr = re.latest_rr(pid)
    if rr is not None and rr >= 30:
        missing.append(f"тахипноэ сохраняется (ЧД {rr})")
    spo2 = re.latest_spo2(pid)
    if spo2 is not None and spo2 < 95:
        missing.append(f"SpO2 {spo2}% < 95%")
    return {"met": len(missing) == 0, "missing": missing}


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


def _active_abts(pid):
    """Активные антибиотики (J01*), отсортированные по дате начала."""
    meds = [m for m in fs.get_medications(pid)
            if m["code"].startswith("J01") and m.get("status") == "active"]
    return sorted(meds, key=lambda m: m.get("date") or "")


def _has_med_class(pid, prefix):
    return any(m["code"].startswith(prefix) for m in fs.get_medications(pid)
               if m.get("status") == "active")


def _has_equivalent_med(pid, expected_code):
    """Есть ли среди активных назначений сам ожидаемый препарат или его протокольная замена
    (см. terminology.abt_equivalent) — напр. второй антибиотик в схеме уже покрывает ожидание."""
    return any(abt_equivalent(m["code"], expected_code) for m in fs.get_medications(pid)
               if m.get("status") == "active")


def _step_down_done(pid):
    """Выполнен ли переход в/в → per os: есть пероральный антибиотик, начатый
    после стартового в/в препарата."""
    abts = _active_abts(pid)
    if not abts:
        return False
    iv = [m for m in abts if (m.get("route") or "") in ("iv", "im")]
    oral = [m for m in abts if (m.get("route") or "") == "oral"]
    if not iv or not oral:
        return False
    first_iv_date = iv[0].get("date") or ""
    return any((o.get("date") or "") > first_iv_date for o in oral)


# ---- Главная функция ----

def evaluate_cap(pid):
    """
    Полная оценка соответствия протоколу ВП (КП №768, взрослые) для пациента.
    Ветвится на амбулаторный и стационарный блоки по тяжести/наличию госпитализации.
    Возвращает {applicable, setting, severity, hospitalization, expected_regimen,
    icu, discharge, compliant, gaps, protocol}.
    compliant = True если нет warning-уровневых gap'ов.
    """
    if not re.has_pneumonia(pid):
        return {"applicable": False, "compliant": True, "gaps": []}

    gaps = []
    severity = classify_severity(pid)
    hosp = hospitalization_reasons(pid)
    prefer = prefer_inpatient_reasons(pid)
    encounter_setting = _setting(pid)
    needed_inpatient = (severity == "severe") or bool(hosp)
    setting = "inpatient" if needed_inpatient else encounter_setting

    # 0. Диагноз должен подтверждаться данными: жалобами/анамнезом или объективным осмотром.
    has_anam, has_exam = re.diagnosis_support(pid)
    if not has_anam and not has_exam:
        gaps.append({
            "severity": "warning",
            "code": "diagnosis_unsupported",
            "message": "Диагноз ВП не подтверждён данными: нет записей анамнеза/жалоб и объективного осмотра.",
            "recommendation": "Заполните анамнез (жалобы, анамнез заболевания) или объективный осмотр (t, SpO2, ЧД, ЧСС, локальные знаки).",
        })

    if setting == "inpatient":
        expected = expected_inpatient_regimen(pid, severity)
    else:
        expected = expected_antibiotic(pid)

    # 1. Показания к госпитализации / ОРИТ
    # Сигнализируем, когда по факту приём амбулаторный, а по тяжести/показаниям нужен стационар.
    if encounter_setting == "outpatient" and needed_inpatient:
        if hosp:
            gaps.append({
                "severity": "warning",
                "code": "hospitalization_indicated",
                "message": "Есть показания к госпитализации: " + "; ".join(hosp),
                "recommendation": "Госпитализация (КП №768).",
            })
        else:
            gaps.append({
                "severity": "warning",
                "code": "hospitalization_indicated",
                "message": "Тяжёлая ВП — показана госпитализация.",
                "recommendation": "Госпитализация (КП №768).",
            })
    # Предпочтительность стационара (возраст >60, сопутствующие, беременность) — info, не жёсткое показание.
    if encounter_setting == "outpatient" and not needed_inpatient and prefer:
        gaps.append({
            "severity": "info",
            "code": "inpatient_preferable",
            "message": "Предпочтителен стационар: " + "; ".join(prefer),
            "recommendation": "Рассмотреть стационарное лечение (КП №768).",
        })
    icu = icu_criteria(pid) if setting == "inpatient" else []
    if icu:
        gaps.append({
            "severity": "warning",
            "code": "icu_indicated",
            "message": "Показания к переводу в ОРИТ: " + "; ".join(icu),
            "recommendation": "Перевод в ОРИТ (КП №768).",
        })

    # 2. Обязательные исследования (КП №768)
    if not (_has_service_request(pid, "CBC") or _has_observation(pid, WBC_CODE)):
        gaps.append({
            "severity": "warning", "code": "missing_cbc",
            "message": "Не назначен/не выполнен общий анализ крови (ОАК).",
            "recommendation": "Назначить ОАК (КП №768).",
        })
    if not (_has_service_request(pid, "CRP") or _has_observation(pid, CRP_CODE)):
        gaps.append({
            "severity": "warning", "code": "missing_crp",
            "message": "Не определён С-реактивный белок (СРБ).",
            "recommendation": "Назначить СРБ — критерий эффективности АБТ (КП №768).",
        })
    if not _has_observation(pid, SPO2_CODE):
        gaps.append({
            "severity": "warning", "code": "missing_spo2",
            "message": "Не выполнена пульсоксиметрия (SpO2).",
            "recommendation": "Пульсоксиметрия при каждом осмотре (КП №768).",
        })
    if not _has_observation(pid, TEMP_CODE):
        gaps.append({
            "severity": "info", "code": "missing_temp",
            "message": "Нет записи температуры тела.",
            "recommendation": "Термометрия при каждом медицинском осмотре (КП №768).",
        })

    # 2b. Доп. исследования в стационаре (КП №768)
    if setting == "inpatient":
        if not (_has_service_request(pid, "URINE") or _has_service_request(pid, "ECG")):
            gaps.append({
                "severity": "info", "code": "missing_inpt_studies",
                "message": "В стационаре не назначены ОАМ / ЭКГ.",
                "recommendation": "ОАМ, ЭКГ, по показаниям ПКТ и посевы (КП №768).",
            })
        # Посевы (гемокультура/мокрота) — при тяжёлом течении.
        if severity == "severe" and not (_has_service_request(pid, "BLOOD_CULT") or _has_service_request(pid, "SPUTUM_CULT")):
            gaps.append({
                "severity": "info", "code": "missing_cultures",
                "message": "Не взяты посевы (гемокультура/мокрота).",
                "recommendation": "До старта АБТ — посевы при тяжёлом течении (КП №768).",
            })

    # 3. Рентгенография ОГК по показаниям (КП №768)
    cxr_ordered = _has_service_request(pid, "CXR") or any(
        r["code"] == "CXR" for r in fs.get_diagnostic_reports(pid)
    )
    if severity == "severe" and not cxr_ordered:
        gaps.append({
            "severity": "warning", "code": "cxr_indicated",
            "message": "Тяжёлая ВП — показана рентгенография ОГК, но она не назначена.",
            "recommendation": "Назначить R-графию ОГК в прямой проекции (КП №768).",
        })
    if re.has_local_signs(pid) and not cxr_ordered:
        gaps.append({
            "severity": "info", "code": "cxr_local_signs",
            "message": "Локальные/асимметричные знаки в лёгких — показание к R-графии ОГК.",
            "recommendation": "Назначить R-графию ОГК (КП №768).",
        })

    # 4. Антибактериальная терапия
    _evaluate_abt(pid, setting, severity, expected, gaps)

    # 5. Симптоматическая терапия по показаниям (п.40-42)
    _evaluate_symptomatic(pid, gaps)

    # 6. Динамическая оценка и завершение
    _evaluate_followup(pid, setting, gaps)

    compliant = not any(g["severity"] == "warning" for g in gaps)
    return {
        "applicable": True,
        "setting": setting,
        "severity": severity,
        "hospitalization": hosp,
        "prefer_inpatient": prefer,
        "expected_regimen": expected,
        "icu": icu,
        "discharge": discharge_criteria(pid) if setting == "inpatient" else None,
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


def _evaluate_abt(pid, setting, severity, expected, gaps):
    """Проверка антибактериальной терапии: наличие, соответствие, маршрут, длительность."""
    abt = _earliest_active_antibiotic(pid)
    sev_label = "Тяжёлая ВП" if severity == "severe" else "ВП в стационаре"

    if setting == "outpatient":
        exp_name = expected["name"]
        exp_grp = expected["atc_group"]
        exp_dose = expected.get("dose", "")
        if not abt:
            gaps.append({
                "severity": "warning", "code": "no_abt",
                "message": "Внебольничная пневмония диагностирована, АБТ не назначена.",
                "recommendation": f"Назначить {exp_name} внутрь",
            })
            return
        grp, _ = atc_group(abt["code"])
        if not abt_equivalent(abt["code"], expected["atc_code"]):
            ov = bool(abt.get("cds_override"))
            base = (f"Назначен {atc_drug_display(abt['code'])} (группа {grp}), "
                    f"по протоколу первая линия — {exp_name} (группа {exp_grp}).")
            if ov:
                base += " Врач подтвердил назначение осознанно."
                if abt.get("cds_override_detail"):
                    base += f" ({abt['cds_override_detail']})"
            gaps.append({
                "severity": "warning", "code": "not_first_line_abt",
                "message": base,
                "recommendation": f"{expected['rationale']} ({expected['ref']}).",
                "cds_override": ov,
            })
        # Маршрут: в амбулаторных условиях — перорально (КП №768)
        if abt["code"] in PARENTERAL_ONLY or (abt.get("route") or "") in ("iv", "im"):
            gaps.append({
                "severity": "warning", "code": "parenteral_in_outpatient",
                "message": (f"{atc_drug_display(abt['code'])} — парентеральный препарат, "
                            "в амбулаторных условиях АБП назначаются перорально."),
                "recommendation": "Перейти на пероральную форму или госпитализировать (КП №768).",
            })
        _check_dose(abt, expected, gaps)
        _check_course(abt, gaps)
    else:
        # Стационар
        primary = expected["primary"]
        if not abt:
            gaps.append({
                "severity": "warning", "code": "no_abt",
                "message": f"{sev_label}, АБТ не назначена.",
                "recommendation": f"Назначить {primary['name']} в/в",
            })
            return
        grp, _ = atc_group(abt["code"])
        exp_grp, _ = atc_group(primary["atc_code"])
        if not abt_equivalent(abt["code"], primary["atc_code"]) and not _has_equivalent_med(pid, primary["atc_code"]):
            ov = bool(abt.get("cds_override"))
            base = (f"Назначен {atc_drug_display(abt['code'])} (группа {grp}), "
                    f"по протоколу стационар — {primary['name']} (группа {exp_grp}).")
            if ov:
                base += " Врач подтвердил назначение осознанно."
            gaps.append({
                "severity": "warning", "code": "not_inpatient_first_line",
                "message": base,
                "recommendation": f"{primary['reason']} ({expected['ref']}).",
                "cds_override": ov,
            })
        # Маршрут: в стационаре старт в/в (КП №768)
        if (abt.get("route") or "") not in ("iv", "im") and abt["code"] in PARENTERAL_ONLY:
            gaps.append({
                "severity": "warning", "code": "oral_in_inpatient",
                "message": (f"{atc_drug_display(abt['code'])} назначен не в/в — "
                            "в стационаре старт АБТ внутривенно (КП №768)."),
                "recommendation": "Назначить препарат внутривенно (КП №768).",
            })
        # Аддоны (MRSA, грипп, атипичная)
        for addon in expected.get("addons", []):
            ag, _ = atc_group(addon["atc_code"])
            if ag and not _has_med_class(pid, ag):
                gaps.append({
                    "severity": "info", "code": "missing_addon",
                    "message": f"Не назначен {addon['name']}.",
                    "recommendation": f"{addon['reason']}",
                })
        # Ступенчатая терапия: после клинического ответа — переход в/в → per os
        days = (date.today() - _parse_date(abt["date"])).days
        if days >= 3 and not re.has_fever(pid) and not _step_down_done(pid):
            gaps.append({
                "severity": "info", "code": "no_stepdown",
                "message": "Клинический ответ есть, но переход в/в → per os не выполнен.",
                "recommendation": "Ступенчатая терапия: перейти на пероральный приём (КП №768).",
            })
        _check_dose(abt, expected, gaps)
        _check_course(abt, gaps)


def _check_dose(abt, expected, gaps):
    """Сверка дозы назначенного препарата с ожидаемой взрослой дозой (КП №768).
    Проверяет по дневной дозе (period_start..period_end + dose_per_day), если заданы."""
    exp_dose = adult_dose(abt["code"])
    if not exp_dose:
        return
    route, dose_text, min_mg, max_mg = exp_dose
    dpd = abt.get("dose_per_day")
    if dpd is None:
        return
    try:
        v = float(dpd)
    except (TypeError, ValueError):
        return
    expected_label = format_expected_dose(abt["code"])
    if v < min_mg * 0.5:
        gaps.append({
            "severity": "warning", "code": "dose_too_low",
            "message": f"Доза {atc_drug_display(abt['code'])} {v} мг/сут — ниже ожидаемой ({dose_text}).",
            "recommendation": f"Ожидаемая доза: {expected_label} (КП №768).",
        })
    elif v > max_mg * 1.5:
        gaps.append({
            "severity": "warning", "code": "dose_too_high",
            "message": f"Доза {atc_drug_display(abt['code'])} {v} мг/сут — выше ожидаемой ({dose_text}).",
            "recommendation": f"Ожидаемая доза: {expected_label} (КП №768).",
        })


def _check_course(abt, gaps):
    """Длительность курса 7–14 дней (КП №768, взрослые)."""
    if abt.get("period_start") and abt.get("period_end"):
        dur = (_parse_date(abt["period_end"]) - _parse_date(abt["period_start"])).days
        if dur < 7:
            gaps.append({
                "severity": "warning", "code": "course_too_short",
                "message": f"Курс АБТ {dur} дн. — короче протокольных 7–14 дней.",
                "recommendation": "Продолжить курс до 7–14 дней (КП №768).",
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
            "recommendation": "Указать period_end: курс 7–14 дней (КП №768).",
        })


def _evaluate_symptomatic(pid, gaps):
    """Симптоматическая терапия по показаниям (КП №768)."""
    # Муколитики без показаний — не назначать избыточно (info)
    if _has_med_class(pid, "R05CB") and not re.has_bronchial_obstruction(pid):
        gaps.append({
            "severity": "info", "code": "mucolytic_optional",
            "message": "Муколитик назначен без бронхообструкции/показаний.",
            "recommendation": "Муколитики по показаниям — при продуктивном кашле (КП №768).",
        })
    # Бронхолитик без бронхообструкции — warning
    if _has_med_class(pid, "R03AC") or _has_med_class(pid, "R03AK"):
        if not re.has_bronchial_obstruction(pid):
            gaps.append({
                "severity": "warning", "code": "bronchodilator_not_indicated",
                "message": "Бронхолитик назначен без признаков бронхообструкции.",
                "recommendation": "Бронхолитики — при бронхообструкции (КП №768).",
            })
    # Глюкокортикоиды системные без показаний — warning
    if _has_med_class(pid, "H02AB"):
        if not (re.has_bronchial_obstruction(pid) or re.has_complication(pid) or re.has_emergency_sign(pid)):
            gaps.append({
                "severity": "warning", "code": "steroid_not_indicated",
                "message": "Системный глюкокортикоид назначен без показаний.",
                "recommendation": "ГКС — при тяжёлой ВП с бронхообструкцией/сепсисом (КП №768).",
            })
    # Осельтамивир без подозрения на грипп — info
    if _has_med_class(pid, "J05AH") and not re.has_influenza_suspicion(pid):
        gaps.append({
            "severity": "info", "code": "oseltamivir_not_indicated",
            "message": "Осельтамивир назначен без подозрения на грипп.",
            "recommendation": "Осельтамивир — при подозрении на грипп (КП №768).",
        })


def _evaluate_followup(pid, setting, gaps):
    """Динамическая оценка эффективности АБТ, выписка, повторная R-графия."""
    abt = _earliest_active_antibiotic(pid)
    if not abt:
        return
    days = (date.today() - _parse_date(abt["date"])).days

    # Оценка эффективности через 48-72 ч (амбулаторно) / 24-48 ч (стационар) (КП №768)
    if days >= 3:
        reassess = _has_observation_after(pid, [TEMP_CODE, SPO2_CODE], abt["date"])
        if not reassess:
            gaps.append({
                "severity": "warning", "code": "no_reassessment",
                "message": f"Прошло {days} дн. от начала АБТ, нет контрольной записи (t/SpO2).",
                "recommendation": "Оценить эффективность АБТ через 48–72 ч (КП №768).",
            })
        else:
            if re.has_fever(pid):
                gaps.append({
                    "severity": "warning", "code": "abt_no_effect",
                    "message": "На фоне АБТ сохраняется лихорадка — нет эффекта.",
                    "recommendation": "Госпитализация или смена АБП (КП №768).",
                })
            # СРБ должен снижаться
            dec = re.crp_decreased(pid)
            if dec is False:
                gaps.append({
                    "severity": "warning", "code": "crp_not_decreasing",
                    "message": "СРБ не снижается на фоне АБТ — нет эффекта.",
                    "recommendation": "Снижение СРБ — критерий эффективности (КП №768).",
                })

    # Повторная R-графия ОГК через 4-6 нед (КП №768)
    if not (_has_service_request(pid, "CXR_REPEAT") or
            any(r["code"] == "CXR_REPEAT" for r in fs.get_diagnostic_reports(pid))):
        gaps.append({
            "severity": "info", "code": "no_repeat_cxr",
            "message": "Не запланирована повторная R-графия ОГК через 4–6 нед.",
            "recommendation": "Контрольная R-графия ОГК через 4–6 нед (КП №768).",
        })

    # Критерии выписки — только в стационаре
    if setting == "inpatient":
        dc = discharge_criteria(pid)
        if not dc["met"]:
            gaps.append({
                "severity": "info", "code": "discharge_not_ready",
                "message": "Критерии выписки не выполнены: " + "; ".join(dc["missing"]),
                "recommendation": "Выписка при нормотермии, нормальном ЧД, SpO2 ≥ 95% (КП №768).",
            })

    # Вакцинопрофилактика
    if re.has_clinical_flag(pid, "no_pneumo_vaccine"):
        gaps.append({
            "severity": "info", "code": "vaccination",
            "message": "Пневмококковая вакцинация не завершена.",
            "recommendation": "Вакцинопрофилактика пневмококковой инфекции (КП №768).",
        })
