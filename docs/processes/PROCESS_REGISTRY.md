# Process Registry (SSOT): связь клинических процессов ↔ данными (BPMN ↔ Supabase/SQLite)

> **Репозиторий:** `clinic-os`. Данные — FHIR-подобные таблицы (`schema.sql`), исполняются через `db.py` (Postgres Supabase / SQLite).
> SSOT процессного слоя — `docs/processes/process_registry.yaml`. BPMN (`docs/bpmn/*.bpmn`) — визуализация.

### Как изучать процессы (человек или агент)

**Цель:** без догадок понять, *что у нас считается отдельным процессом*, *где истина в данных* и *как не перепутать диагноз, измерения и назначения*.

**Рекомендуемый порядок чтения**

| Время | Шаг | Зачем |
|------|-----|--------|
| ~5 мин | `docs/processes/PROCESS_REGISTRY.md` (этот файл) — §0 | Два процесса в репозитории и что каждый покрывает |
| ~10 мин | `docs/processes/process_registry.yaml` — у каждого процесса `reader_intro_ru`, затем `steps` | Машиночитаемые шаги, условия, таблицы, SQL-проверки |
| ~5 мин | `docs/processes/UI_PROCESS_MAP.md` | Как шаги процесса ложатся на карту пациента и CDS |
| ~5 мин | `docs/processes/CDS_SIGNALING.md` + блок `cds_policy` в YAML | Сигнал врачу, hard-stop / осознанный override, непрерывный пересчёт |
| ~5 мин | `docs/processes/STATUS_SEMANTICS.md` — **§0 Encounter ≠ Condition** | Два lifecycle (приём vs диагноз), M2M `encounter_reason`, UI-фильтр ≠ вложенность |
| по желанию | `docs/processes/PROCESSES_BPMN.md` + `docs/processes/BPMN_PRACTICES.md` | Mermaid, нюансы BPMN |
| по желанию | `docs/processes/STATUS_SEMANTICS.md` — остальные § | Семантика статусов pathway/encounter/condition/medication/goal |
| по желанию | Открыть `docs/bpmn/*.bpmn` в Camunda Modeler / bpmn.io | Картинка (без дорожек, подписи по-русски) |

**Правила, которые нельзя нарушать**

1. **Диагноз ≠ измерение:** `condition_` (диагноз, МКБ) и `observation` (числовые показатели) — разные пространства смысла. Тяжесть вычисляется (`protocol_cap.classify_severity`), а не хранится как статус.
2. **Приём ≠ диагноз:** `encounter` закрывается в конце каждого контакта; `condition_` — когда болезнь разрешилась. Связь — `encounter_reason` (M2M), не вложенность. Подробно: `STATUS_SEMANTICS.md` §0.
3. **Амбулаторный ≠ стационарный:** `cap_outpatient` (АБТ per os, класс тяжести «средняя») и `cap_inpatient` (АБТ в/в, «тяжёлая»/госпитализация) — отдельные процессы со сквозным handoff.
4. **При расхождении картинки (BPMN) и таблицы шагов (YAML)** для автоматизации и агентов приоритет у **`docs/processes/process_registry.yaml`**; BPMN обновляем под это или явно помечаем расхождение в бэклоге.

**Пятое (CDS):** сигнал continuous + врач может override + override видим. Подробно — `CDS_SIGNALING.md`; не «зеленить» эпизод после `cds_override=1`.

**Процесс ≠ инструкция.** BPMN — этапы и решения эпизода. Критерии/дозы/списки исследований КП №768 — в YAML (`intent_ru`) и в коде; на диаграмме у шлюзов только якоря `КП №768, п.…` для сверки. Подробнее: `BPMN_PRACTICES.md` §0.

---

### 0) Каталог процессов в `process_registry.yaml` (v1)

| `process.id` | Смысл (уровень управления) | BPMN-файл |
|----------------|----------------------------|-----------|
| `care_outpatient` | **Амбулаторный визит** (обращение → приём → куда вести → обследование → лечение/план). Без петель | `docs/bpmn/care-outpatient-mature.bpmn` |
| `care_outpatient_control` | **Контроль** (оценка → стационар / коррекция / ещё визит / эпизод завершён). Исходы — концы | `docs/bpmn/care-outpatient-control-mature.bpmn` |
| `cap_outpatient` | Узкая протокольная схема ВП (вторично к общему ведению) | `docs/bpmn/cap-outpatient-mature.bpmn` |
| `cap_inpatient` | Стационарный эпизод ВП (пока) | `docs/bpmn/cap-inpatient-mature.bpmn` |
| `ida_outpatient` | Узкая протокольная схема ЖДА (вторично) | `docs/bpmn/ida-outpatient-mature.bpmn` |

**Сквозные передачи** — в корневом блоке `process_handoffs` (артефакт + `trigger_ru`):
- `cap_outpatient → cap_inpatient` (артефакт `hospitalization_decision`, п.26)
- `cap_inpatient → cap_outpatient` (артефакт `discharge_summary`, контроль исхода после выписки)

---

### 1) Что такое Process Registry

`docs/processes/process_registry.yaml` — реестр процессов с данными для:
- построения визуализаций (дашборд, Operations Console)
- интерпретации срезов (агенты)
- формальных проверок (guardrails)
- «процессных сигналов» (застрял, нет эффекта АБТ, не закрыт эпизод)

---

### 2) Формат `docs/processes/process_registry.yaml` (v1)

Корневые поля:
- `registry_version`: версия формата (целое)
- `updated_at`: дата (ISO-8601)
- `process_handoffs`: сквозные передачи между процессами
- `lane_canon`: канон дорожек (функции, не должности)
- `processes`: список процессов

#### 2.1 Поля процесса
- `id` (snake_case, стабильный), `name`, `scope`
- `trigger_ru`, `outcome_ru`, `handoff_to`
- `reader_intro_ru` — **обязательно**: 2–5 строк простым языком
- `bpmn_files`, `business_owner`, `data_owner`, `entities`, `links`, `steps`

#### 2.2 Поля шага
- `id` (snake_case, стабильный), `bpmn_task_id` (ссылка на элемент BPMN или null)
- `lane` — **функция**, не должность (`intake|clinician|nursing|laboratory|radiology|pharmacy|cds|patient|system`)
- `intent_ru`, `entry_conditions`, `exit_conditions`
- `signals`: `now_state`, `stuck_definition` (`age_from`, `stuck_after_days`, `meaning_ru`), `sla_targets`
- `inputs`, `outputs`
- `data_mapping`: `contract_views`, `raw_tables`, `keys`, `columns`, `notes_ru`
- `golden_checks_sql`: read-only SQL (короткие, воспроизводимые)

#### 2.3 «Карточка шага» (чеклист качества)
- **Смысл**: `intent_ru`
- **Границы**: `entry_conditions` / `exit_conditions`
- **Участник/функция**: `lane`
- **Входы/выходы**: `inputs` / `outputs`
- **Где в данных**: `data_mapping` + ключи/колонки
- **Сигналы**: `signals` (сейчас, застрял, SLA)
- **Золотая проверка**: `golden_checks_sql`

#### 2.4 Канон lanes (функции)
Должности (врач-терапевт, медсестра, лаборант…) **не** пишем в `lane` — только **функцию**. Один человек может выполнять несколько функций; при найме назначаем функции, не должности. См. `lane_canon` в YAML.

#### 2.5 Process contract
- `trigger_ru` — что запускает (событие + где известно)
- `outcome_ru` — терминальный результат и артефакт
- `handoff_to` — id следующего процесса или null
Сквозные передачи — в `process_handoffs` (артефакт + `trigger_ru`).

#### 2.6 Systems map (процессы ↔ системы)

| System | Role | Key steps |
|--------|------|-----------|
| **clinic-os (Flask)** | UI оператора + API + CDS | все шаги (формы ввода) |
| **Supabase Postgres / SQLite** | Хранилище FHIR-ресурсов | `data_mapping` всех шагов |
| **protocol_cap** | Регламент ВП (независимая проверка) | `assess_severity`, `decide_hospitalization`, `select_*_regimen`, `reassess_*`, `evaluate_discharge`; continuous — `evaluate_cap` |
| **rules_engine** | Правила (CQL-like) | `assess_severity`, `reassess_*` |
| **drug_service** | Проверка лекарств | `select_and_prescribe_abt`, `prescribe_iv_abt` (order-sign) |
| **cds_service** | Карточки CDS Hooks | `patient-view`, `order-sign` (hard-stop → confirm / override) |
| **openFDA** | Кэш справочника лекарств (`medication_knowledge`) | `load_drugs.py` (офлайн-загрузка) |

Политика сигналов и override: `docs/processes/CDS_SIGNALING.md` (якорь в YAML: `cds_policy`).

---

### 3) Правила поддержки (SSOT)
- **Один источник истины**: процессный SSOT — только `docs/processes/process_registry.yaml`. BPMN и views — производные.
- **UI соответствует процессу**: подписи и порядок шагов карты — `docs/processes/UI_PROCESS_MAP.md` (алиасы `focus_stage` ↔ `step.id`).
- **Стабильные идентификаторы**: `process.id` и `step.id` не переименовывать без крайней необходимости.
- **Не перепутать статусы**: см. `docs/processes/STATUS_SEMANTICS.md`.
- **Только read-only проверки**: `golden_checks_sql` не должен создавать/изменять объекты.
- **Не усложнять**: реестр минимальный, но достаточный, чтобы агент не придумывал свою «семантику».

> `golden_checks_sql` написаны с прицелом на PostgreSQL (Supabase); в SQLite эквиваленты даты — `date('now','-N days')` вместо `current_date - N`. Все запросы — read-only.
