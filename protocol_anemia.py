"""
Слой 3b — Регламент лечения железодефицитной анемии (взрослые),
КП МЗ РБ №23 от 01.04.2022 (Диагностика и лечение пациентов (взрослое население)
с железодефицитной анемией).

Машинно-проверяемое подмножество протокола:
  - тяжесть по гемоглобину: лёгкая (90–119 г/л) / средняя (70–89) / тяжёлая (<70);
  - обязательные исследования: ОАК, ферритин, железо сыворотки, биохимия крови, ОАМ;
  - терапия: железо внутрь (до 200 мг/сут элементарного железа) — первая линия;
    внутривенно — при мальабсорбции/заболевании ЖКТ/непереносимости перорального железа
    (docs/protocols/ida_therapy_rules.yaml — SSOT выбора);
  - показания к госпитализации/трансфузии: Hb<70; Hb<80 + тяжёлое общее состояние
    или нарушение гемодинамики;
  - контроль: повторный ОАК после начала терапии; критерий эффективности —
    нормализация Hb (>120 жен./>130 муж./>110 берем.) и восполнение ферритина (≥30).

Не моделируются (не входят в машинную проверку): направления на эндоскопию/УЗИ/
консультации из «дополнительных» пунктов КП — нет workflow для внешних направлений в демо.

Главный вход: evaluate_ida(pid) → {applicable, setting, severity, hospitalization,
expected_regimen, transfusion, compliant, gaps, protocol} — та же форма ответа,
что и evaluate_cap, чтобы шаблон UI и диспетчер (protocol_dispatch.py) не различали протоколы.
"""
from datetime import datetime, date

import fhir_store as fs
import rules_engine as re
import protocol_rules_ida as pri
from protocol_cap import _check_dose  # доза — общая проверка (min/max мг/сут), не CAP-специфична
from terminology import (
    FERRITIN_CODE, IRON_CODE, HB_CODE,
    atc_group, atc_drug_display,
    general_condition_display, general_condition_needs_inpatient,
)

PROTOCOL_REF = "КП МЗ РБ №23 от 01.04.2022 (Железодефицитная анемия, взрослое население)"

_HB_TARGET_DEFAULT = 120.0
_HB_TARGET_MALE = 130.0
_HB_TARGET_PREGNANT = 110.0
_FERRITIN_TARGET = 30.0


# ---- Тяжесть (КП №23: по уровню гемоглобина) ----

def classify_severity_ida(pid):
    """Лёгкая (Hb 90–119) / средняя (70–89) / тяжёлая (<70). None — если ЖДА не активна
    или нет данных Hb (используется острое/минимальное значение — тяжесть на пике)."""
    if not re.has_ida(pid):
        return None
    hb = re.worst_hb(pid)
    if hb is None:
        return None
    if hb < 70:
        return "severe"
    if hb < 90:
        return "moderate"
    return "mild"


def _hb_target(pid):
    """Целевой Hb (норма) для оценки эффективности терапии — зависит от пола/беременности."""
    if re.has_clinical_flag(pid, "pregnancy"):
        return _HB_TARGET_PREGNANT
    p = fs.get_patient(pid)
    if p and p.get("gender") == "male":
        return _HB_TARGET_MALE
    return _HB_TARGET_DEFAULT


# ---- Показания к госпитализации / трансфузии (КП №23, взрослые) ----

def hospitalization_reasons_ida(pid):
    """Список причин для госпитализации/трансфузии по КП №23 (взрослые)."""
    reasons = []
    if not re.has_ida(pid):
        return reasons
    hb = re.worst_hb(pid)
    if hb is None:
        return reasons
    if hb < 70:
        reasons.append(f"гемоглобин {hb} г/л (<70) — тяжёлая анемия")
        return reasons
    if hb < 80:
        gc = re.general_condition(pid)
        if gc and general_condition_needs_inpatient(gc):
            reasons.append(
                f"гемоглобин {hb} г/л (<80) + общее состояние: {general_condition_display(gc)}"
            )
        if re.has_clinical_flag(pid, "hemodynamic_instability"):
            reasons.append(f"гемоглобин {hb} г/л (<80) + нарушение гемодинамики")
    return reasons


def transfusion_criteria(pid):
    """Показания к трансфузии эритроцитарной массы (КП №23) — те же пороги, что и
    госпитализация: тяжёлая/декомпенсированная анемия требует и стационара, и трансфузии."""
    return hospitalization_reasons_ida(pid)


# ---- Выбор терапии железом (SSOT: docs/protocols/ida_therapy_rules.yaml) ----

def expected_iron_therapy(pid):
    return pri.select_therapy(pid)


def evaluate_iron_choice(pid, atc_code):
    """
    Проверка выбираемого препарата железа ДО сохранения (order-sign) — тот же
    момент и та же форма ответа, что protocol_cap.evaluate_abt_choice для АБТ,
    только для класса B03A и диагноза ЖДА. Регистрируется в
    protocol_dispatch.DRUG_CHOICE_EVALUATORS — новый протокол со своим классом
    препарата подключается там же, без правок в app.py/cds_service.py.

    Возвращает список issues (как drug_service): пусто — можно сохранять;
    warning not_first_line_iron — soft-stop (чекбокс + опц. причина).
    Не-B03A и пациенты без ЖДА — без замечаний.
    """
    code = (atc_code or "").strip().upper()
    if not code.startswith("B03A"):
        return []
    if not re.has_ida(pid):
        return []

    expected = expected_iron_therapy(pid)
    exp_grp = expected.get("atc_group")
    grp_sel, _ = atc_group(code)
    if exp_grp and grp_sel == exp_grp:
        return []
    exp_name = expected.get("name") or atc_drug_display(expected.get("atc_code") or "")
    return [{
        "severity": "warning",
        "category": "not_first_line_iron",
        "protocol_id": "ida_adult_23",
        "message": (
            f"Препарат {atc_drug_display(code)} не соответствует протоколу ЖДА "
            f"(КП №23) для этого пациента. Ожидается {exp_name}."
        ),
    }]


# ---- Вспомогательные ----

def _has_service_request(pid, code):
    return any(sr["code"] == code for sr in fs.get_service_requests(pid))


def _has_observation(pid, code):
    return fs.get_last_observation(pid, code) is not None


def _has_observation_after(pid, codes, after_iso):
    for code in codes:
        o = fs.get_last_observation(pid, code)
        if o and o["date"] > after_iso:
            return True
    return False


def _earliest_active_iron(pid):
    meds = [m for m in fs.get_medications(pid)
            if m["code"].startswith("B03A") and m.get("status") == "active"]
    if not meds:
        return None
    return sorted(meds, key=lambda m: m.get("date") or "")[0]


def _parse_date(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


# ---- Главная функция ----

def evaluate_ida(pid):
    """
    Полная оценка соответствия протоколу ЖДА (КП №23, взрослые) для пациента.
    Возвращает {applicable, setting, severity, hospitalization, transfusion,
    expected_regimen, compliant, gaps, protocol} — форма совместима с evaluate_cap.
    """
    if not re.has_ida(pid):
        return {"applicable": False, "compliant": True, "gaps": []}

    gaps = []
    severity = classify_severity_ida(pid)
    hosp = hospitalization_reasons_ida(pid)
    setting = re.encounter_setting(pid)
    expected = expected_iron_therapy(pid)

    # 0. Диагноз должен подтверждаться данными.
    has_anam, has_exam = re.diagnosis_support(pid)
    if not has_anam and not has_exam:
        gaps.append({
            "severity": "warning",
            "code": "diagnosis_unsupported",
            "message": "Диагноз ЖДА не подтверждён данными: нет записей анамнеза, жалоб и объективного осмотра.",
            "recommendation": "Заполните анамнез (жалобы, причина кровопотери) или объективный осмотр.",
        })

    # 1. Показания к госпитализации/трансфузии
    if setting == "outpatient" and hosp:
        gaps.append({
            "severity": "warning",
            "code": "hospitalization_indicated",
            "message": "Есть показания к госпитализации: " + "; ".join(hosp),
            "recommendation": "Госпитализация (КП №23).",
        })
    transfusion = transfusion_criteria(pid) if setting == "inpatient" else []
    if transfusion:
        gaps.append({
            "severity": "warning",
            "code": "transfusion_indicated",
            "message": "Показания к трансфузии эритроцитарной массы: " + "; ".join(transfusion),
            "recommendation": "Рассмотреть трансфузию эритроцитарной массы (КП №23).",
        })

    # 2. Обязательные исследования (КП №23)
    if not (_has_service_request(pid, "CBC") or _has_observation(pid, HB_CODE)):
        gaps.append({
            "severity": "warning", "code": "missing_cbc",
            "message": "Не назначен/не выполнен общий анализ крови (ОАК).",
            "recommendation": "Назначить ОАК (КП №23).",
        })
    if not (_has_service_request(pid, "FERRITIN") or _has_observation(pid, FERRITIN_CODE)):
        gaps.append({
            "severity": "warning", "code": "missing_ferritin",
            "message": "Не определён ферритин — основной диагностический критерий ЖДА.",
            "recommendation": "Назначить ферритин (КП №23).",
        })
    if not (_has_service_request(pid, "IRON_SERUM") or _has_observation(pid, IRON_CODE)):
        gaps.append({
            "severity": "warning", "code": "missing_iron_serum",
            "message": "Не определено железо сыворотки.",
            "recommendation": "Назначить железо сыворотки (КП №23).",
        })
    if not _has_service_request(pid, "BIOCHEM"):
        gaps.append({
            "severity": "info", "code": "missing_biochem",
            "message": "Не назначен биохимический анализ крови.",
            "recommendation": "Биохимический анализ крови (КП №23).",
        })
    if not _has_service_request(pid, "URINE"):
        gaps.append({
            "severity": "info", "code": "missing_urine",
            "message": "Не назначен общий анализ мочи (ОАМ).",
            "recommendation": "ОАМ (КП №23).",
        })

    # 3. Терапия железом
    _evaluate_iron_therapy(pid, expected, gaps)

    # 4. Контроль эффективности терапии
    _evaluate_followup_ida(pid, gaps)

    compliant = not any(g["severity"] == "warning" for g in gaps)
    return {
        "applicable": True,
        "setting": setting,
        "severity": severity,
        "hospitalization": hosp,
        "transfusion": transfusion,
        "expected_regimen": expected,
        "compliant": compliant,
        "gaps": gaps,
        "protocol": PROTOCOL_REF,
    }


def _evaluate_iron_therapy(pid, expected, gaps):
    """Проверка терапии железом: наличие, соответствие первой линии, маршрут, доза."""
    med = _earliest_active_iron(pid)
    if not med:
        gaps.append({
            "severity": "warning", "code": "no_iron_therapy",
            "message": "Железодефицитная анемия диагностирована, терапия железом не назначена.",
            "recommendation": f"Назначить {expected['name']} ({expected['route'] == 'oral' and 'внутрь' or 'в/в'}).",
        })
        return

    grp, _ = atc_group(med["code"])
    exp_grp = expected.get("atc_group")
    if med["code"] != expected["atc_code"]:
        ov = bool(med.get("cds_override"))
        base = (f"Назначен {atc_drug_display(med['code'])} (группа {grp}), "
                f"по протоколу первая линия — {expected['name']} (группа {exp_grp}).")
        if ov:
            base += " Врач подтвердил назначение осознанно."
            if med.get("cds_override_detail"):
                base += f" ({med['cds_override_detail']})"
        gaps.append({
            "severity": "warning", "code": "not_first_line_iron",
            "message": base,
            "recommendation": f"{expected['rationale']} ({expected['ref']}).",
            "cds_override": ov,
        })

    route = (med.get("route") or "").lower()
    if expected["route"] == "oral" and route in ("iv", "im"):
        gaps.append({
            "severity": "info", "code": "route_mismatch_iron",
            "message": (f"{atc_drug_display(med['code'])} назначен парентерально — "
                        "по умолчанию КП №23 предполагает приём внутрь без противопоказаний."),
            "recommendation": "Уточнить показания к парентеральному железу (мальабсорбция, заболевание ЖКТ или непереносимость).",
        })
    elif expected["route"] == "iv" and route == "oral":
        gaps.append({
            "severity": "warning", "code": "route_mismatch_iron",
            "message": (f"{atc_drug_display(med['code'])} назначен внутрь, но по указанным факторам "
                        "(мальабсорбция, заболевание ЖКТ или непереносимость) показано внутривенное железо."),
            "recommendation": f"{expected['rationale']} ({expected['ref']}).",
        })

    # Доза — только для перорального железа (парентеральное дозируется индивидуально,
    # числового порога КП №23 не задаёт — adult_dose() для B03AC08 вернёт None и проверка пропустится).
    _check_dose(med, expected, gaps)


def _evaluate_followup_ida(pid, gaps):
    """Контроль эффективности: повторный ОАК, нормализация Hb, восполнение ферритина."""
    med = _earliest_active_iron(pid)
    if not med:
        return
    days = (date.today() - _parse_date(med["date"])).days
    target = _hb_target(pid)

    # Эффективность оценивается не ранее чем через ~4 недели (ответ на терапию железом
    # медленнее, чем на АБТ) — контрольный ОАК после начала терапии.
    if days >= 28:
        reassessed = _has_observation_after(pid, [HB_CODE], med["date"])
        if not reassessed:
            gaps.append({
                "severity": "warning", "code": "no_hb_reassessment",
                "message": f"Прошло {days} дн. от начала терапии железом, нет контрольного ОАК.",
                "recommendation": "Контрольный ОАК через 3–4 нед от начала терапии (КП №23).",
            })
        else:
            hb = re.latest_hb(pid)
            if hb is not None and hb < target:
                gaps.append({
                    "severity": "info", "code": "hb_not_normalized",
                    "message": f"Гемоглобин {hb} г/л — целевой уровень ({target:g}) не достигнут.",
                    "recommendation": "Продолжить терапию железом до нормализации Hb (КП №23).",
                })
            ferritin = re.latest_ferritin(pid)
            if hb is not None and hb >= target and ferritin is not None and ferritin < _FERRITIN_TARGET:
                gaps.append({
                    "severity": "info", "code": "ferritin_not_replenished",
                    "message": f"Hb нормализован, но ферритин {ferritin} нг/мл (<{_FERRITIN_TARGET:g}) — запасы железа не восполнены.",
                    "recommendation": "Продолжить терапию железом до восполнения ферритина ≥30 нг/мл, далее контроль ОАК 1×/мес ×3 мес (КП №23).",
                })

    # Повторный ОАК в динамике (после нормализации) — план наблюдения 1×/мес ×3 мес.
    hb_obs = [o for o in fs.get_observations(pid, HB_CODE) if o.get("value_numeric") is not None]
    if len(hb_obs) < 2 and days >= 28:
        gaps.append({
            "severity": "info", "code": "no_repeat_cbc_plan",
            "message": "Нет данных о плановом повторном ОАК для контроля динамики Hb.",
            "recommendation": "Контроль ОАК 1×/мес в течение 3 мес после нормализации Hb (КП №23).",
        })
