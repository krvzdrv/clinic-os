# Processes BPMN — обзор потоков внебольничной пневмонии

Обзор двух процессов КП МЗ РБ №768 (внебольничная пневмония, взрослые) для нетехнической аудитории и быстрой навигации. Картинки — в `docs/bpmn/*.bpmn`. SSOT шагов — `docs/processes/process_registry.yaml`.

## Связь процессов

```
cap_outpatient (амбулаторно)
   │
   ├── decide_hospitalization ──(показания п.26)──► cap_inpatient (стационар)
   │                                                      │
   │ ◄──(выписка, discharge_summary)── discharge ─────────┘
   │
   └── followup_and_outcome ──► close_episode (recovered)
```

Handoff'ы (артефакт + триггер) — в `process_handoffs` YAML.

---

## cap_outpatient — амбулаторный эпизод ВП

Поток (канон YAML / UI): обращение → анамнез → осмотр/измерения → диагноз → сверка с протоколом (тяжесть / госпитализация) → обследование по показаниям → АБТ per os → симптоматика → план/цель → оценка 48–72 ч → контроль → исход → повторная R-графия → закрытие.

> Если `.bpmn` ещё рисует «исследования → диагноз», для автоматизации и UI приоритет у `process_registry.yaml` и `UI_PROCESS_MAP.md`.

```mermaid
flowchart TD
    A([Start: обращение]) --> B[Task_Intake — приём, жалоба / анамнез]
    B --> C[Task_VitalsExam — t, SpO2, ЧД, ЧСС, осмотр]
    C --> I[Task_SetDiagnosis — condition ВП]
    I --> D[Task_AssessSeverity — класс тяжести п.6.3]
    D --> E{Gateway_Hospitalization — п.26}
    E -- показания --> H1([End_Hospitalized → cap_inpatient])
    E -- нет показаний --> F[Task_OrderDiagnostics — ОАК, СРБ, R-графия]
    F --> G[Task_RecordResults — WBC, CRP, CXR]
    G --> J[Task_PrescribeAbt — АБТ per os п.16-21]
    J --> K[Task_PrescribeSymptomatic — п.40-42]
    J --> L[Task_CreateCarePlan — план + цель]
    J --> M{Gateway_AbtEffect — 48-72 ч}
    M -- нет эффекта --> N[смена АБТ / госпитализация]
    N --> E
    M -- эффект --> O[Task_Followup — контроль, оценка цели]
    O --> P{goal achieved?}
    P -- нет --> Q([цикл коррекции])
    P -- да --> R[Task_ScheduleRepeatCxr — 4-6 нед]
    R --> S[Task_CloseEpisode — recovered]
    S --> T([End_Recovered])
```

Ключевые шлюзы: `Gateway_Hospitalization` (п.26), `Gateway_AbtEffect` (п.15, п.30).

---

## cap_inpatient — стационарный эпизод ВП

Поток: госпитализация → обязательные исследования → оценка тяжести/ОРИТ → выбор режима АБТ в/в → назначение в/в АБТ + симптоматика → оценка 48–72 ч → step-down в/в→per os → критерии выписки → выписка → контроль после выписки.

```mermaid
flowchart TD
    A([Start: hospitalization_decision]) --> B[Task_Admit — encounter inpatient]
    B --> C[Task_AdmissionDiagnostics — ОАК, СРБ, ПКТ, ОАМ, ЭКГ, посевы, CXR п.28-29]
    C --> D[Task_AssessSeverityIcu — показания к ОРИТ п.27]
    D --> E{Gateway_Icu — п.27}
    E -- показания --> F[перевод в ОРИТ: pathway=icu]
    E -- нет показаний --> G[Task_SelectRegimen — режим АБТ в/в п.31,34,36-39]
    F --> G
    G --> H[Task_PrescribeIvAbt — АБТ в/в]
    B --> I[Task_PrescribeSymptomaticInpatient — О2, бронходилат., ГКС, осельтам. п.40-42]
    H --> J{Gateway_AbtEffect — 48-72 ч}
    J -- нет эффекта --> K[эскалация/смена режима]
    K --> G
    J -- эффект --> L[Task_StepDown — в/в → per os п.43]
    L --> M[Task_EvaluateDischarge — критерии п.49]
    M --> N{Gateway_Discharge — met?}
    N -- нет --> L
    N -- да --> O[Task_Discharge — finished, achieved, recovered, CXR_REPEAT]
    O --> P[Task_PostDischargeFollowup — контроль 4-6 нед]
    P --> Q([End → handoff cap_outpatient: оценка исхода])
```

Ключевые шлюзы: `Gateway_Icu` (п.27), `Gateway_AbtEffect` (п.15, п.30), `Gateway_Discharge` (п.49).

---

## Как читать вместе с YAML

1. Открой Mermaid-схему выше (или `.bpmn` в Camunda Modeler) — общая картина.
2. Для каждого шага открой `process_registry.yaml` → `steps[]` по `id` — `entry_conditions`, `exit_conditions`, `signals`, `data_mapping`, `golden_checks_sql`.
3. Семантика статусов — `STATUS_SEMANTICS.md`.
4. Независимая проверка всей картины — `protocol_cap.evaluate_cap(pid)` (вердикт = single source of truth для CDS и метрики).
5. Сигналы / hard-stop / осознанный override — `docs/processes/CDS_SIGNALING.md` (не путать с «соответствует протоколу»).
