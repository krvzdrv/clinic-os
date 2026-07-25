# BPMN Practices — практики моделирования для clinic-os

Канон рисования BPMN для процессов внебольничной пневмонии. Цель — чтобы картинка (BPMN) и таблица шагов (YAML) не расходились, а дорожки читались как **функции**, а не должности.

## 1. Один процесс — один канонический BPMN

- `cap_outpatient` → `docs/bpmn/cap-outpatient-mature.bpmn`
- `cap_inpatient` → `docs/bpmn/cap-inpatient-mature.bpmn`

Вторую «обзорную» диаграмму не заводим — она дублирует поток. Обзор для нетехнической аудитории — collapsed subprocess в Camunda Modeler или Mermaid в `PROCESSES_BPMN.md`.

## 2. Lanes = функции, не должности

Дорожки BPMN и `lane` в YAML совпадают по **функции**. Должности (врач-терапевт, медсестра, лаборант, рентгенолог, клинический фармаколог) **не** пишем в названии дорожки.

| YAML `lane` | BPMN lane | Смысл |
|-------------|-----------|--------|
| `intake` | Intake | Приём/регистрация обращения |
| `clinician` | Clinician | Врач: осмотр, диагноз, назначение, оценка |
| `nursing` | Nursing | Измерения, забор, выдача |
| `laboratory` | Laboratory | ОАК, СРБ, ПКТ, посевы |
| `radiology` | Radiology | R-графия, КТ, УЗИ |
| `pharmacy` | Pharmacy | Выписка/проверка лекарств |
| `cds` | CDS | Система CDS/регламент (автоматика) |
| `patient` | Patient | Действия/сигналы пациента/родителя |
| `system` | System | Переходы pathway, системные политики |

Один человек может выполнять несколько функций (например, врач-терапевт = `clinician` + часть `nursing` в малой клинике). При найме назначаем функции, не должности.

## 3. Язык в трёх слоях

- **BPMN element `name`**: EN (например `Task_PrescribeAbt`, `Gateway_Hospitalization`). Опционально — один якорь-сокращение в скобках (`Assess severity (п.6.3)`).
- **Пояснения и инструкции**: RU (`intent_ru`, `reader_intro_ru`, `notes_ru`, BPMN `TextAnnotation`).
- **Медицинские термины и коды**: verbatim в backticks — МКБ-10 (`J18.9`), LOINC (`59408-5`), ATC (`J01CA04`), коды исследований (`CXR`, `CXR_REPEAT`). Колонки БД — snake_case из `schema.sql`.

## 4. Именование элементов BPMN

- `Start_<смысл>`, `End_<смысл>` (например `End_Recovered`, `End_Hospitalized`).
- `Task_<глаголОбъект>` (CamelCase): `Task_Admit`, `Task_PrescribeIvAbt`, `Task_StepDown`.
- `Gateway_<смысл>`: `Gateway_Hospitalization`, `Gateway_Icu`, `Gateway_AbtEffect`.
- `SubProcess_<смысл>` — для сворачиваемых блоков (например цикл коррекции).
- `Event_<тир>_<смысл>`: `Event_Timer72h`, `Event_NoEffect`.

## 5. Gateways — решения по протоколу

Шлюзы соответствуют разделам КП №768:
- `Gateway_Hospitalization` — п.26 (есть показания → handoff в `cap_inpatient`).
- `Gateway_Icu` — п.27 (шок/ДН III–IV/сознание/судороги → ОРИТ).
- `Gateway_AbtEffect` — п.15, п.30 (через 48–72 ч: эффект → step-down/followup; нет эффекта → смена АБТ/госпитализация).
- `Gateway_Discharge` — п.49 (критерии выписки выполнены → discharge).

## 6. Связь с YAML

- Каждый `step.id` в YAML имеет `bpmn_task_id` — ссылку на `id` элемента BPMN (или `null`, если шага нет на картинке, например автоматическое закрытие).
- При расхождении BPMN и YAML приоритет у **YAML** (см. `PROCESS_REGISTRY.md` §3). BPMN обновляем под YAML.

## 7. Не плодить статусы

Тяжесть (`moderate`/`severe`), эффективность АБТ, критерии выписки — **вычисляются** (`protocol_cap`), а не хранятся как статусы. В BPMN это gateway/decision, в данных — `observation` + `clinical_flag`, не новое поле статуса.

## 8. Таймеры

Таймеры BPMN — для SLA-сигналов (48–72 ч на оценку АБТ, 4–6 нед на повторную R-графию). В YAML — `signals.stuck_definition` и `sla_targets`. Источник timestamp — `medication_request.date`, `goal.achievement_date` и т.п. (см. `age_from`).

## 9. Handoff между процессами

Handoff `cap_outpatient → cap_inpatient` (госпитализация) — на BPMN амбулаторного процесса это `End_Hospitalized` + message-flow/`Trigger` на старт стационарного процесса. В YAML — запись в `process_handoffs` (артефакт `hospitalization_decision`).

## 10. Инструменты

- Рисование: Camunda Modeler или https://demo.bpmn.io/.
- Не править layout чужой диаграммы без запроса (см. `*.hand-layout` маркеры в зрелых репозиториях — здесь пока не используем, но принцип тот же).
