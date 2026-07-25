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
| по желанию | `docs/processes/PROCESSES_BPMN.md` + `docs/processes/BPMN_PRACTICES.md` | Mermaid, нюансы BPMN |
| по желанию | `docs/processes/STATUS_SEMANTICS.md` | Семантика статусов pathway/encounter/condition/medication/goal |
| по желанию | Открыть `docs/bpmn/*.bpmn` в Camunda Modeler / bpmn.io | Картинка для обсуждения с командой |

**Три правила, которые нельзя нарушать**

1. **Диагноз ≠ измерение:** `condition_` (диагноз, МКБ) и `observation` (числовые показатели) — разные пространства смысла. Тяжесть вычисляется (`protocol_cap.classify_severity`), а не хранится как статус.
2. **Амбулаторный ≠ стационарный:** `cap_outpatient` (АБТ per os, класс тяжести «средняя») и `cap_inpatient` (АБТ в/в, «тяжёлая»/госпитализация) — отдельные процессы со сквозным handoff.
3. **При расхождении картинки (BPMN) и таблицы шагов (YAML)** для автоматизации и агентов приоритет у **`docs/processes/process_registry.yaml`**; BPMN обновляем под это или явно помечаем расхождение в бэклоге.

---

### 0) Каталог процессов в `process_registry.yaml` (v1)

| `process.id` | Смысл (уровень управления) | BPMN-файл |
|----------------|----------------------------|-----------|
| `cap_outpatient` | **Амбулаторный** эпизод ВП у взрослого: обращение → осмотр/измерения → оценка тяжести → решение о госпитализации → исследования → диагноз → АБТ per os → симптоматика → план/цель → оценка эффективности 48–72 ч → контрольный визит → исход → повторная R-графия | `docs/bpmn/cap-outpatient-mature.bpmn` |
| `cap_inpatient` | **Стационарный** эпизод ВП: госпитализация → обязательные исследования → оценка тяжести/ОРИТ → выбор режима АБТ в/в → назначение в/в АБТ + симптоматика → оценка эффективности 48–72 ч → step-down в/в→per os → критерии выписки → выписка → контроль после выписки | `docs/bpmn/cap-inpatient-mature.bpmn` |

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
| **protocol_cap** | Регламент ВП (независимая проверка) | `assess_severity`, `decide_hospitalization`, `select_*_regimen`, `reassess_*`, `evaluate_discharge` |
| **rules_engine** | Правила (CQL-like) | `assess_severity`, `reassess_*` |
| **drug_service** | Проверка лекарств | `select_and_prescribe_abt`, `prescribe_iv_abt` |
| **openFDA** | Кэш справочника лекарств (`medication_knowledge`) | `load_drugs.py` (офлайн-загрузка) |

---

### 3) Правила поддержки (SSOT)
- **Один источник истины**: процессный SSOT — только `docs/processes/process_registry.yaml`. BPMN и views — производные.
- **Стабильные идентификаторы**: `process.id` и `step.id` не переименовывать без крайней необходимости.
- **Не перепутать статусы**: см. `docs/processes/STATUS_SEMANTICS.md`.
- **Только read-only проверки**: `golden_checks_sql` не должен создавать/изменять объекты.
- **Не усложнять**: реестр минимальный, но достаточный, чтобы агент не придумывал свою «семантику».

> `golden_checks_sql` написаны с прицелом на PostgreSQL (Supabase); в SQLite эквиваленты даты — `date('now','-N days')` вместо `current_date - N`. Все запросы — read-only.
