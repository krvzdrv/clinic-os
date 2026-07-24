"""
Слой 2 — Сервис проверки лекарств.

Проверяет назначаемый препарат против совокупности данных по карточке пациента:
- показания (соответствует ли активным диагнозам);
- противопоказания (беременность, ХБП, аллергия);
- взаимодействия с уже назначенными препаратами;
- дублирование (два препарата одной фармакологической группы).

Источники знаний:
1. Структурированные правила по ATC-группам (DRUG_RULES) — для часто встречающихся
   кардио/эндо препаратов. Это «ядро» проверки, работает без внешних данных.
2. Свободный текст из кэша medication_knowledge (загружается из openFDA) —
   дополнительная информация для отображения врачу, не для логики.

Возвращает структурированный вердикт: safe + список issues с severity
(info / warning / hard-stop). Этот вердикт используют CDS (Слой 5) и
регламент (Слой 3b).
"""
import fhir_store as fs
from terminology import atc_group, atc_display


# Классы аллергии и МКБ/ATC-коды, которые к ним относятся.
# Аллергия хранится в allergy_intolerance.code (см. fhir_store.add_allergy).
ALLERGY_CLASSES = {
    "beta-lactam": {"beta-lactam", "penicillin", "amoxicillin", "amox-clav",
                    "cephalosporin", "cefuroxime", "ceftiaxone"},
    "macrolide":   {"macrolide", "clarithromycin", "azithromycin"},
}

# Структурированные правила по ATC-группе (первые 5 символов кода).
# contraindicated_in — состояния, при которых препарат противопоказан (hard-stop).
# caution_in — состояния, при которых требуется осторожность (warning).
# interacts_with — {ATC-группа: описание} взаимодействий с другими группами.
# duplicate_group — своя группа: два препарата из неё = дублирование.
# indicated_for — МКБ-коды диагнозов, при которых препарат показан.
# contraindicated_if_allergy — классы аллергии (hard-stop).
# caution_if_allergy — классы аллергии (warning).
DRUG_RULES = {
    # --- Сердечно-сосудистые (протокол АГ) ---
    "C09AA": {
        "contraindicated_in": ["pregnancy"],
        "caution_in": ["ckd"],
        "interacts_with": {"C03DA": "Риск гиперкалиемии (ингибитор АПФ + калийсберегающий диуретик)"},
        "duplicate_group": "C09AA",
        "indicated_for": ["I10", "I11", "I13"],
    },
    "C09CA": {
        "contraindicated_in": ["pregnancy"],
        "caution_in": ["ckd"],
        "interacts_with": {"C03DA": "Риск гиперкалиемии (сартан + калийсберегающий диуретик)"},
        "duplicate_group": "C09CA",
        "indicated_for": ["I10", "I11", "I13"],
    },
    "C07AB": {
        "contraindicated_in": ["asthma"],
        "caution_in": [],
        "interacts_with": {},
        "duplicate_group": "C07AB",
        "indicated_for": ["I10", "I48", "I50", "I25"],
    },
    "C08CA": {
        "contraindicated_in": [],
        "caution_in": [],
        "interacts_with": {},
        "duplicate_group": "C08CA",
        "indicated_for": ["I10"],
    },
    "C03AA": {
        "contraindicated_in": [],
        "caution_in": ["ckd"],
        "interacts_with": {},
        "duplicate_group": "C03AA",
        "indicated_for": ["I10"],
    },
    "C03DA": {
        "contraindicated_in": [],
        "caution_in": ["ckd"],
        "interacts_with": {"C09AA": "Риск гиперкалиемии", "C09CA": "Риск гиперкалиемии"},
        "duplicate_group": "C03DA",
        "indicated_for": ["I50"],
    },
    "C10AA": {
        "contraindicated_in": ["pregnancy"],
        "caution_in": [],
        "interacts_with": {},
        "duplicate_group": "C10AA",
        "indicated_for": ["E78", "I25"],
    },
    "A10BA": {
        "contraindicated_in": [],
        "caution_in": ["ckd"],
        "interacts_with": {},
        "duplicate_group": "A10BA",
        "indicated_for": ["E11"],
    },
    # --- Антибактериальные (протокол ВП, КП МЗ РБ №204) ---
    "J01CA": {  # амоксициллин — первая линия ВП без факторов риска
        "contraindicated_in": [],
        "caution_in": ["ckd"],
        "interacts_with": {},
        "duplicate_group": "J01CA",
        "indicated_for": ["J12.9", "J13", "J14", "J15.9", "J18.0", "J18.1", "J18.9"],
        "contraindicated_if_allergy": ["beta-lactam"],
    },
    "J01CR": {  # амоксициллин/клавуланат — при факторах риска резистентности
        "contraindicated_in": [],
        "caution_in": ["ckd"],
        "interacts_with": {},
        "duplicate_group": "J01CR",
        "indicated_for": ["J12.9", "J13", "J14", "J15.2", "J15.9", "J18.0", "J18.1", "J18.9"],
        "contraindicated_if_allergy": ["beta-lactam"],
    },
    "J01FA": {  # макролиды — при аллергии на β-лактамы / атипичная пневмония
        "contraindicated_in": [],
        "caution_in": [],
        "interacts_with": {},
        "duplicate_group": "J01FA",
        "indicated_for": ["J12.9", "J15.9", "J18.9"],
        "contraindicated_if_allergy": ["macrolide"],
    },
    "J01DC": {  # цефуроксим — при не-IgE гиперчувствительности к β-лактамам
        "contraindicated_in": [],
        "caution_in": [],
        "interacts_with": {},
        "duplicate_group": "J01DC",
        "indicated_for": ["J12.9", "J13", "J15.9", "J18.0", "J18.9"],
        "caution_if_allergy": ["beta-lactam"],
    },
    "J01DD": {  # цефалоспорины III пок. — стационар, тяжёлая ВП
        "contraindicated_in": [],
        "caution_in": [],
        "interacts_with": {},
        "duplicate_group": "J01DD",
        "indicated_for": ["J12.9", "J13", "J14", "J15.9", "J18.0", "J18.1", "J18.9"],
        "caution_if_allergy": ["beta-lactam"],
    },
    "J01XX": {  # линезолид — MRSA, аллергия на β-лактамы
        "contraindicated_in": [],
        "caution_in": [],
        "interacts_with": {},
        "duplicate_group": "J01XX",
        "indicated_for": ["J12.9", "J15.2", "J15.9", "J18.9"],
    },
    "J01XA": {  # ванкомицин — MRSA, тяжёлая ВП
        "contraindicated_in": [],
        "caution_in": ["ckd"],
        "interacts_with": {},
        "duplicate_group": "J01XA",
        "indicated_for": ["J12.9", "J15.2", "J15.9", "J18.9"],
    },
    "J01AA": {  # доксициклин — альтернатива макролидам при аллергии на них
        "contraindicated_in": [],
        "caution_in": [],
        "interacts_with": {},
        "duplicate_group": "J01AA",
        "indicated_for": ["J12.9", "J18.9"],
    },
}


# ---- Состояние пациента, релевантное для проверки лекарств ----

def _patient_state(pid):
    """Собирает флаги состояния пациента из его ресурсов (без слоя правил)."""
    conditions = [c["code"] for c in fs.get_conditions(pid) if c.get("clinical_status") == "active"]
    state = {
        "pregnancy": fs.is_fertile_female(pid),
        "ckd": "N18" in conditions,
        "diabetes": any(c.startswith("E1") for c in conditions),
        "asthma": any(c in ("J45", "J46") for c in conditions),
        "active_condition_codes": conditions,
        "allergies": [a["display"] for a in fs.get_allergies(pid)],
        "allergy_codes": [a["code"] for a in fs.get_allergies(pid)],
        "active_meds": fs.get_medications(pid),
    }
    return state


def _has_allergy_class(state, class_name):
    """Есть ли у пациента аллергия указанного класса (по коду или тексту)."""
    codes = set((c or "").lower() for c in state["allergy_codes"])
    displays = " ".join(state["allergies"]).lower()
    for token in ALLERGY_CLASSES.get(class_name, set()):
        if token.lower() in codes or token.lower() in displays:
            return True
    return False


def has_allergy_class(pid, class_name):
    """Публичный интерфейс: есть ли у пациента аллергия указанного класса."""
    return _has_allergy_class(_patient_state(pid), class_name)


def _has_condition(state, codes):
    return any(c in state["active_condition_codes"] for c in codes)


# ---- Главная функция проверки ----

def evaluate_medication(pid, atc_code):
    """
    Возвращает вердикт по препарату для данного пациента:
      {
        "atc_code", "group", "group_name",
        "safe": bool,            # True если нет hard-stop
        "issues": [ {severity, category, message}, ... ],
        "knowledge": {...} | None   # из кэша medication_knowledge, если есть
      }
    """
    prefix, group_name = atc_group(atc_code)
    rule = DRUG_RULES.get(prefix, {})
    state = _patient_state(pid)
    issues = []

    # 1. Дублирование: два препарата из одной группы
    dup_group = rule.get("duplicate_group")
    if dup_group:
        same = [m for m in state["active_meds"]
                if m["code"][:5] == dup_group and m["code"] != atc_code]
        if same:
            issues.append({
                "severity": "warning",
                "category": "duplicate",
                "message": f"Дублирование: уже назначен препарат той же группы ({atc_display(same[0]['code'])}). "
                           f"Оставьте один.",
            })

    # 2. Противопоказания по состоянию
    for cond_key in rule.get("contraindicated_in", []):
        if cond_key == "pregnancy" and state["pregnancy"]:
            issues.append({
                "severity": "hard-stop",
                "category": "contraindication_pregnancy",
                "message": "Противопоказан женщине фертильного возраста без подтверждения, "
                           "что беременность исключена (категория D по FDA).",
            })
        elif cond_key == "asthma" and state["asthma"]:
            issues.append({
                "severity": "hard-stop",
                "category": "contraindication_asthma",
                "message": "β-блокаторы противопоказаны при бронхиальной астме.",
            })

    # 3. Осторожность по состоянию (warning)
    for cond_key in rule.get("caution_in", []):
        if cond_key == "ckd" and state["ckd"]:
            issues.append({
                "severity": "warning",
                "category": "caution_ckd",
                "message": "Требуется коррекция дозы и контроль функции почек / калия.",
            })

    # 4. Взаимодействия с текущими препаратами
    for other_group, descr in rule.get("interacts_with", {}).items():
        conflicting = [m for m in state["active_meds"] if m["code"][:5] == other_group]
        if conflicting:
            issues.append({
                "severity": "warning",
                "category": "interaction",
                "message": f"{descr}. Конфликтует с: {conflicting[0]['display']}.",
            })

    # 5. Аллергии
    # 5a. По классам (структурированно) — hard-stop / warning
    for cls in rule.get("contraindicated_if_allergy", []):
        if _has_allergy_class(state, cls):
            issues.append({
                "severity": "hard-stop",
                "category": "allergy",
                "message": f"Аллергия на класс «{cls}»: препарат противопоказан.",
            })
    for cls in rule.get("caution_if_allergy", []):
        if _has_allergy_class(state, cls):
            issues.append({
                "severity": "warning",
                "category": "allergy_caution",
                "message": f"У пациента аллергия на класс «{cls}» — назначать с осторожностью.",
            })
    # 5b. Текстовое совпадение (на случай, если аллергия заведена произвольным текстом)
    for allergy in state["allergies"]:
        if atc_code.lower() in allergy.lower() or group_name.lower() in allergy.lower():
            issues.append({
                "severity": "hard-stop",
                "category": "allergy",
                "message": f"У пациента аллергия: {allergy}.",
            })

    # 6. Показания: препарат не соответствует ни одному активному диагнозу
    indicated_for = rule.get("indicated_for", [])
    if indicated_for and not _has_condition(state, indicated_for):
        issues.append({
            "severity": "info",
            "category": "indication_missing",
            "message": "Препарат не соответствует ни одному активному диагнозу пациента.",
        })

    knowledge = fs.get_medication_knowledge(atc_code)
    safe = not any(i["severity"] == "hard-stop" for i in issues)
    return {
        "atc_code": atc_code,
        "group": prefix,
        "group_name": group_name,
        "safe": safe,
        "issues": issues,
        "knowledge": dict(knowledge) if knowledge else None,
    }
