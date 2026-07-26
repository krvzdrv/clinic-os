# UI ↔ Process map (карта пациента ↔ process_registry)

> **SSOT процессов:** `docs/processes/process_registry.yaml`.  
> Этот файл — мост к UI (`templates/patient.html` + `protocol_verdict.verdict_for_ui`).  
> При расхождении с прозой BPMN приоритет у YAML, затем у этой карты для интерфейса.

## Канонический порядок приёма (амбулаторно)

Совпадает с `canonical_order_ru` / rail приёма:

| № | Процесс (смысл) | Rail / секция UI | `focus_stage` |
|---|-----------------|------------------|---------------|
| 1 | Анамнез | Анамнез `#flow-anam` (`.section-header`) | `anam` |
| 2 | Осмотр / измерения | Осмотр `#flow-exam` (`.section-header`) | `exam` |
| 3 | Диагноз (МКБ) | Диагноз `#flow-cond` (`.section-header`) | `cond` |
| 4 | Сверка с протоколом | CDS `#now-action` (не отдельная форма) | `actions` (госпитализация/ОРИТ) |
| 5 | Обследование | Обследование `#flow-diag` (`.section-header`) | `diag` |
| 6 | Лечение (АБТ + симптоматика) | Лечение `#flow-med` (`.section-header`) | `med` |
| 7–8 | План / оценка 48–72 ч | CDS + «Планирование и выписка» | `reassess` |
| 9–10 | Контроль / повторная R-графия | CDS / планирование | `repeat_cxr` |

Шаги 1–5 на карте — формы приёма; «сверка» всегда в CDS.
В UI: `encounter` ambulatory/inpatient → «Приём»; `followup` → «Контрольный визит» (не «Визит» на всё). Эпизод ВП — весь курс до исхода, не один приём.

**Триаж:** `#triage-panel` над `#conditions-list` — агрегатор CDS issues по диагнозам (клик → `#condition-{id}` или список).  
**Приёмы:** `GET /patient/{id}/encounters?limit=&offset=` + «Показать ещё»; открытый приём на первой странице.  
**Связь:** `encounter_reason` (M2M reasonReference); `condition_.encounter_id` — legacy fallback.  
**Повод при открытии:** форма `encounter_form` — чекбоксы активных диагнозов («продолжение») пишут `encounter_reason` сразу при создании приёма; ничего не отмечено → «новая жалоба», связь появится при `add_condition(encounter_id=…)`. Карточка приёма показывает повод строкой `.enc-reason` («Повод: …» / «Новая жалоба — диагноз ещё не поставлен»). См. `docs/explain/07-encounter-types.md`, `docs/bpmn/encounter-reason-mature.bpmn`.

## `focus_stage` → действие в CDS

| `focus_stage` | Главный CTA в `#now-action` | Шаг процесса (пример) |
|---------------|-----------------------------|------------------------|
| `anam` | форма анамнеза | `collect_anamnesis` |
| `exam` | осмотр / vitals | `perform_exam` |
| `cond` | поставить диагноз | `establish_diagnosis` |
| `diag` | заказ / результат исследования | `order_*` / admission diagnostics |
| `med` | заменить / назначить АБТ (`suggest_route`: oral/iv) | `select_and_prescribe_abt` / `prescribe_iv_abt` |
| `actions` | госпитализировать (± ОРИТ) | `decide_hospitalization` / `transfer_icu` |
| `reassess` | смена АБТ **и** госпитализация *или* план контроля 3 дня | `reassess_48_72h` |
| `repeat_cxr` | запланировать контроль R-графии | `schedule_repeat_cxr` |

## Gaps → focus (сводка)

Полный словарь в `protocol_verdict._FOCUS_BY_GAP`. Ключевые:

- АБТ не по протоколу / нет АБТ / доза / маршрут / step-down → `med`
- Нет эффекта АБТ / СРБ не снижается → `reassess` (смена + госпитализация)
- Не запланирована оценка 48–72 ч → `reassess` (только план контроля)
- ОАК/СРБ/CXR / стационарные исследования → `diag`
- SpO2 / температура → `exam`
- Показана госпитализация / ОРИТ → `actions`
- Диагноз без опоры → `cond`

## Правила согласованности

1. Подписи rail = канон YAML: **Анамнез → Осмотр → Диагноз → Обследование → Лечение**.
2. CDS не дублирует весь приём: одно главное действие (`focus_stage`) + «Ещё».
3. Маршрут АБТ в now-action берётся из режима (`suggest_route`: амбулаторно `oral`, стационар `iv`).
4. Документы (`PROCESS_REGISTRY.md`, `PROCESSES_BPMN.md`) не должны описывать порядок «исследования → диагноз», если YAML говорит иначе.
