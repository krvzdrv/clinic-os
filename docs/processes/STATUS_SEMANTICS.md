# STATUS_SEMANTICS — семантика статусов и состояний

SSOT семантики для `clinic-os`. Все ссылки в `process_registry.yaml` резолвятся сюда.

> Принцип: **тяжесть и эффективность вычисляются**, а не хранятся как статус. Статусы — только у ресурсов с жизненным циклом (encounter, condition_, medication_request, service_request, care_plan, goal, pathway).

---

## 1. pathway.state — путь пациента (state machine)

| state | label | Смысл | Кто выставляет |
|-------|-------|--------|---------------|
| `screening` | Скрининг | Пациент заведён, диагноза ВП ещё нет | `fhir_store.add_patient` |
| `treatment` | Терапия | Амбулаторное лечение ВП (план создан) | `care_plan_service.create_cap_plan` |
| `inpatient` | Стационар | Госпитализация (encounter class='inpatient') | `care_plan_service.admit_inpatient` |
| `icu` | ОРИТ | Перевод в ОРИТ (п.27) | шаг `transfer_icu` |
| `recovered` | Выздоровление | Эпизод закрыт (цель достигнута) | `care_plan_service.discharge_inpatient` / `evaluate_cap_goal` |
| `adjustment` | Коррекция | Цель не достигнута — цикл коррекции (смена АБТ/госпитализация) | `care_plan_service.evaluate_cap_goal` (not-achieved) |

**Правило:** `recovered` — терминальный статус эпизода. `adjustment` — возврат в цикл лечения (не закрытие).

---

## 2. encounter.status / encounter.class

| class | status | Смысл |
|-------|--------|--------|
| `ambulatory` | `planned` / `in-progress` / `finished` | Амбулаторный приём |
| `inpatient` | `in-progress` / `finished` | Стационарный приём (госпитализация) |
| `followup` | `planned` / `in-progress` / `finished` | Контрольный визит (после выписки / оценка исхода) |

- `in-progress` — приём идёт. `finished` — закрыт. `planned` — запланирован (контрольный визит).
- `class='inpatient'` + `status='in-progress'` → `protocol_cap._setting` возвращает `inpatient`.

---

## 3. condition_.clinical_status / verification_status

| clinical_status | Смысл |
|-----------------|--------|
| `active` | Активный диагноз ВП (лечится) |
| `resolved` | Эпизод закрыт (выздоровление) |

| verification_status | Смысл |
|---------------------|--------|
| `provisional` | Предварительный |
| `confirmed` | Подтверждённый |

- `rules_engine.has_pneumonia(pid)` проверяет `clinical_status='active'` и `code in PNEUMONIA_CODES`.
- `code` — МКБ-10 (см. `terminology.PNEUMONIA_CODES`).

---

## 4. medication_request.status / route

| status | Смысл |
|--------|--------|
| `active` | Назначение действует |
| `stopped` | Отменено |

| route | Смысл | Где допустимо |
|-------|--------|--------------|
| `oral` | per os | Амбулаторно (первая линия) + step-down в стационаре |
| `iv` | внутривенно | Стационар (старт АБТ, п.31) |
| `im` | внутримышечно | Стационар |
| `inh` | ингаляционно | Бронходилататоры |

- `code` — ATC (J01* — антибиотики, R05CB/R03AC/R03AK/R03DA/H02AB/J05AH — симптоматика).
- Амбулаторно: только `oral` (п.15). Парентеральные (`PARENTERAL_ONLY`) — только стационар.
- Стационар: старт `iv`/`im` (п.31), затем step-down → `oral` (п.43).

---

## 5. service_request.status

| status | Смысл |
|--------|--------|
| `active` | Заказан, не выполнен |
| `completed` | Выполнен (результат записан) |
| `cancelled` | Отменён |

- `code` — код исследования (см. `terminology.STUDIES`: CBC, CRP, PCT, CXR, CXR_REPEAT, BLOOD_CULT…).

---

## 6. care_plan.status / goal.status

| care_plan.status | Смысл |
|------------------|--------|
| `active` | План действует |
| `suspended` | Приостановлен |
| `completed` | План завершён |

| goal.status | Смысл |
|-------------|--------|
| `in-progress` | Цель в работе |
| `achieved` | Достигнута (выздоровление) |
| `not-achieved` | Не достигнута (цикл коррекции) |

- Цель ВП — «клиническое выздоровление»: нормотермия, SpO2 ≥ 95, нормальный ЧД (п.49).
- `evaluate_cap_goal` сравнивает последние observation с критериями и выставляет `achieved` / `not-achieved`.

---

## 7. observation / diagnostic_report

Статуса жизненного цикла нет (только `status` результата: `final`/`partial`/`registered`). Это **факты**, а не состояния:
- `observation` — числовые показатели (LOINC: TEMP/SPO2/RR/HR/WBC/CRP/PCT).
- `diagnostic_report` — текст-заключения (CXR, CT, US, ЭКГ).

---

## 8. clinical_flag

Универсальный структурированный признак (булев/категориальный), которого нет в числовых observation:
социальные факторы риска, локальные знаки при осмотре, бронхообструкция, подозрение на аспирацию/грипп/MRSA,
осложнения, статус вакцинации. Ключи — фиксированные строки (`terminology.CLINICAL_FLAGS`).

- Влияет на: выбор АБТ, показания к госпитализации/ОРИТ, проверку симптоматики (`protocol_cap`).
- `value` — `"true"`/`"false"` или категория; `category` — `social_risk|exam|context|complication|vaccination`.

---

## 9. allergy_intolerance.reaction_type

| reaction_type | Смысл | Влияние на АБТ |
|---------------|--------|----------------|
| `ige` | IgE-опосредованная (анафилаксия/крапивница) | β-лактамы противопоказаны → макролид (п.19) |
| `non-ige` | не-IgE (сыпь/другое) | цефуроксим осторожно (п.21) |
| `unknown` | тип не установлен | трактуется как IgE (перестраховка) |

- `fhir_store.betalactam_allergy_type(pid)` определяет тип по `allergy_intolerance` с β-лактамным `code`/`display`.
