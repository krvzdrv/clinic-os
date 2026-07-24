"""
Слой 3b — Регламенты лечения (независимый валидатор).

Отличие от CDS (Слой 5): CDS подсказывает врачу в момент одного действия.
Регламент же проверяет совокупность — всю картину по пациенту
(диагноз + ко-морбидности + препараты + результаты + цель) — на соответствие
клиническому протоколу. Это другой вопрос и отдельный слой, как и требуется.

Источником протокола служат клинические рекомендации по артериальной
гипертензии (Минздрав РБ / общепринятые). Здесь кодировано подмножество,
достаточное для демо. В прод-системе это был бы формализованный PlanDefinition
(CPG-on-FHIR), читаемый из конфигурации, а не из кода.

Главный вход: evaluate_htn(pid) → { compliant, gaps: [...] }.
Каждый gap: {severity: 'warning'|'info', code, message, recommendation}.
"""
import fhir_store as fs
import rules_engine as re
import drug_service
from terminology import BP_SYS, BP_DIA, atc_group, atc_display


# Целевое АД по протоколу
def target_bp(pid):
    """Целевое АД: <140/90 обычно; <130/80 при СД/ХБП/протеинурии."""
    if re.has_diabetes(pid) or re.has_ckd(pid):
        return (130, 80)
    return (140, 90)


def _at_target(pid):
    bp = fs.get_last_bp(pid)
    if not bp or bp["systolic"] is None:
        return None
    t_sys, t_dia = target_bp(pid)
    return bp["systolic"] <= t_sys and bp["diastolic"] <= t_dia


# --- Запрещённые комбинации препаратов (агрегатная проверка) ---
def _forbidden_combos(pid):
    """Возвращает список запрещённых/опасных комбинаций среди активных препаратов."""
    meds = fs.get_medications(pid)
    codes = [m["code"] for m in meds]
    groups = [atc_group(c)[0] for c in codes]
    issues = []

    # Два ингибитора АПФ / два сартана / и т.п. — дублирование внутри группы
    from collections import Counter
    dup = Counter([g for g in groups if g])
    for g, cnt in dup.items():
        if cnt > 1:
            issues.append({
                "severity": "warning",
                "code": "duplicate_therapy",
                "message": f"Дублирование: два препарата группы {atc_display(g + 'X')}.",
                "recommendation": "Оставить один препарат из группы.",
            })

    # Ингибитор АПФ + сартан — не рекомендуется
    if "C09AA" in groups and "C09CA" in groups:
        issues.append({
            "severity": "warning",
            "code": "ace_arb_combo",
            "message": "Совместное назначение ингибитора АПФ и сартана не рекомендуется.",
            "recommendation": "Оставить один из двух классов.",
        })

    # Ингибитор АПФ/сартан + калийсберегающий диуретик — риск гиперкалиемии
    if ("C09AA" in groups or "C09CA" in groups) and "C03DA" in groups:
        issues.append({
            "severity": "warning",
            "code": "hyperkalemia_risk",
            "message": "Риск гиперкалиемии: ингибитор АПФ/сартан + калийсберегающий диуретик.",
            "recommendation": "Контроль K+ и креатинина через 1–2 недели.",
        })
    return issues


# --- Главная функция ---
def evaluate_htn(pid):
    """
    Полная оценка соответствия протоколу АГ для пациента.
    Возвращает { compliant: bool, target, at_target, gaps: [...] }.
    compliant = True если нет warning-уровневых gap'ов.
    """
    gaps = []

    if not re.has_hypertension(pid):
        return {"applicable": False, "compliant": True, "gaps": []}

    # 1. Цель лечения должна быть задана
    goals = fs.get_goals(pid, status="in-progress")
    if not goals:
        t_sys, t_dia = target_bp(pid)
        gaps.append({
            "severity": "info",
            "code": "no_goal",
            "message": "Не задана цель лечения (целевое АД).",
            "recommendation": f"Поставить цель: АД ≤ {t_sys}/{t_dia} мм рт. ст.",
        })

    # 2. Целевое АД должно учитывать ко-морбидности
    t_sys, t_dia = target_bp(pid)
    for g in goals:
        # Цель хранится как target_metric + target_value; проверяем систолическую цель
        if g["target_metric"] == BP_SYS and g["target_value"] and g["target_value"] > t_sys:
            gaps.append({
                "severity": "warning",
                "code": "target_too_loose",
                "message": f"Цель по систолическому АД ({g['target_value']}) мягче протокола ({t_sys}).",
                "recommendation": f"При СД/ХБП цель ≤ {t_sys}/{t_dia}.",
            })

    # 3. Терапия должна соответствовать протоколу (первая линия + запреты)
    meds = fs.get_medications(pid)
    if not meds:
        gaps.append({
            "severity": "warning",
            "code": "no_therapy",
            "message": "Гипертония диагностирована, антигипертензивной терапии нет.",
            "recommendation": "Назначить препарат первой линии (ингибитор АПФ / сартан / БКК / тиазид).",
        })
    else:
        groups = [atc_group(m["code"])[0] for m in meds]
        first_line = {"C09AA", "C09CA", "C08CA", "C03AA"}
        if not any(g in first_line for g in groups):
            gaps.append({
                "severity": "warning",
                "code": "not_first_line",
                "message": "Нет препарата первой линии (иАПФ/сартан/БКК/тиазид).",
                "recommendation": "Добавить препарат первой линии.",
            })

    # 4. Запрещённые комбинации
    gaps.extend(_forbidden_combos(pid))

    # 5. Достигнута ли цель?
    at_target = _at_target(pid)
    if at_target is False:
        gaps.append({
            "severity": "warning",
            "code": "not_at_target",
            "message": "АД не достигло цели.",
            "recommendation": "Усилить терапию (добавить 2-й препарат) или проверить приверженность.",
        })

    # 6. Передержка наблюдения
    if re.bp_overdue(pid, days=90):
        gaps.append({
            "severity": "warning",
            "code": "overdue",
            "message": "Нет измерения АД более 90 дней — пациент выпал из наблюдения.",
            "recommendation": "Контроль АД не реже 1 раза в 3 месяца.",
        })

    # 7. Базовые лабораторные показатели при АГ (креатинин/СКФ)
    egfr = re.latest_lab(pid, "33914-3")
    if not egfr:
        gaps.append({
            "severity": "info",
            "code": "no_baseline_renal",
            "message": "Не оценена функция почек (СКФ/креатинин).",
            "recommendation": "Назначить креатинин/СКФ на исходном уровне.",
        })

    compliant = not any(g["severity"] == "warning" for g in gaps)
    return {
        "applicable": True,
        "target": {"systolic": t_sys, "diastolic": t_dia},
        "at_target": at_target,
        "compliant": compliant,
        "gaps": gaps,
    }
