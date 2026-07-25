-- Схема данных (FHIR R4-подобная модель).
-- Переносима между PostgreSQL (Supabase) и SQLite: только общие типы
-- (TEXT, INTEGER), ручные текстовые ID, CREATE TABLE IF NOT EXISTS.
-- Каждый ресурс = таблица. Связи — по текстовым ID (patient_id, encounter_id и т.д.).

-- ===== Субъекты =====

CREATE TABLE IF NOT EXISTS practitioner (
    id          TEXT PRIMARY KEY,
    family      TEXT,
    given       TEXT,
    specialty   TEXT
);

CREATE TABLE IF NOT EXISTS patient (
    id          TEXT PRIMARY KEY,
    family      TEXT,
    given       TEXT,
    patronymic  TEXT,
    gender      TEXT,
    birth_date  TEXT
);

-- ===== Приём =====

CREATE TABLE IF NOT EXISTS encounter (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT,
    practitioner_id TEXT,
    status        TEXT,   -- planned / in-progress / finished
    class         TEXT,   -- ambulatory / program / followup
    start         TEXT,
    ended_at       TEXT,
    reason_code   TEXT,   -- МКБ-код повода (может быть пустым)
    complaint     TEXT    -- текст жалобы
);

-- ===== Диагноз =====

CREATE TABLE IF NOT EXISTS condition_ (
    id               TEXT PRIMARY KEY,
    patient_id       TEXT,
    encounter_id     TEXT,
    code_system      TEXT,
    code             TEXT,   -- МКБ-10
    display          TEXT,
    clinical_status  TEXT,   -- active / resolved
    verification_status TEXT, -- provisional / confirmed
    onset_date       TEXT,
    recorded_date    TEXT
);

-- ===== Измерения и анализы (числовые) =====
-- Один ресурс на любой числовой показатель: температура, SpO2, ЧД, ЧСС, лейкоциты, СРБ...
-- Разные анализы различаются по code (LOINC), а не по отдельным таблицам.

CREATE TABLE IF NOT EXISTS observation (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT,
    encounter_id  TEXT,
    code          TEXT,   -- LOINC
    display       TEXT,
    value_numeric REAL,
    value_unit    TEXT,
    value_text    TEXT,   -- для текстовых значений (редко)
    ref_low       REAL,
    ref_high      REAL,
    interpretation TEXT,  -- normal / high / low
    status        TEXT,
    date          TEXT
);

-- ===== Заключения исследований (ЭКГ, УЗИ, холтер) =====
-- Не числовые результаты, а текст-заключение + вложение.

CREATE TABLE IF NOT EXISTS diagnostic_report (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT,
    encounter_id  TEXT,
    code          TEXT,
    display       TEXT,
    status        TEXT,   -- registered / partial / final
    conclusion    TEXT,
    attachment_url TEXT,
    date          TEXT
);

-- ===== Заказы (назначения анализов/исследований) =====

CREATE TABLE IF NOT EXISTS service_request (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT,
    encounter_id  TEXT,
    practitioner_id TEXT,
    code          TEXT,   -- LOINC / SNOMED
    display       TEXT,
    status        TEXT,   -- active / completed / cancelled
    intent        TEXT,   -- order
    occurrence_date TEXT,
    reason_code   TEXT
);

-- ===== Назначения препаратов =====

CREATE TABLE IF NOT EXISTS medication_request (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT,
    encounter_id  TEXT,
    code          TEXT,   -- ATC
    display       TEXT,
    status        TEXT,   -- active / stopped
    dose          TEXT,
    frequency     TEXT,
    route         TEXT,   -- oral / iv / im / inh (ингаляционно)
    period_start  TEXT,
    period_end    TEXT,
    date          TEXT,
    dose_per_day  NUMERIC  -- суточная доза в мг (для сверки с протоколом)
);

-- ===== Справочник лекарств (кэш из openFDA) =====

CREATE TABLE IF NOT EXISTS medication_knowledge (
    atc_code          TEXT PRIMARY KEY,
    name              TEXT,
    indications       TEXT,
    contraindications TEXT,
    interactions      TEXT,
    pregnancy_category TEXT,
    dose_info         TEXT,
    fetched_at        TEXT
);

-- ===== Каталог препаратов (взрослые, КП МЗ РБ №768) =====
-- Справочник для формы назначения: ATC → название, маршрут, фиксированная доза.
-- Выбор «какую АБТ» — НЕ здесь: SSOT = docs/protocols/cap_abt_rules.yaml.
-- protocol_ref / note — подписи для UI, не машинный регламент.

CREATE TABLE IF NOT EXISTS drug_catalog (
    atc_code           TEXT PRIMARY KEY,
    name               TEXT,    -- русское название
    generic_name       TEXT,    -- из openFDA (англ.), опционально
    group_name         TEXT,    -- ATC-группа (рус.)
    dosage_form        TEXT,    -- из openFDA
    form               TEXT,    -- oral / iv / im / inh / both
    route_options      TEXT,    -- CSV допустимых маршрутов: oral,iv
    indications        TEXT,
    contraindications  TEXT,
    interactions       TEXT,
    pregnancy          TEXT,
    dosage_text        TEXT,    -- текст инструкции (openFDA), опционально
    dose_note          TEXT,    -- взрослая доза по протоколу (фиксированная, не мг/кг)
    frequency          TEXT,    -- кратность (для формы)
    max_daily_mg       REAL,     -- макс. суточная доза (мг), для сверки
    default_dose       TEXT,    -- короткая строка дозы для формы
    default_frequency  TEXT,    -- кратность для формы
    protocol_ref       TEXT,    -- ссылка на протокол (подпись)
    note               TEXT,    -- краткая пометка для UI
    category           TEXT,    -- antibiotic_outpatient/antibiotic_inpatient/symptomatic/antiviral
    verify_flag        INTEGER DEFAULT 0
);

-- ===== Аллергии =====

CREATE TABLE IF NOT EXISTS allergy_intolerance (
    id             TEXT PRIMARY KEY,
    patient_id     TEXT,
    code           TEXT,
    display        TEXT,
    criticality    TEXT,
    reaction_type  TEXT,   -- ige / non-ige / unknown (тип реакции на β-лактамы)
    recorded_date  TEXT
);

-- ===== Структурированный анамнез/осмотр/факторы риска =====
-- Универсальный «флаг» пациента: булевы/категориальные признаки, которых
-- нет в числовых observation: социальные факторы риска, локальные знаки при
-- осмотре, бронхообструкция, подозрение на аспирацию/грипп/MRSA, осложнения,
-- статус вакцинации. Ключи — фиксированные строки (см. CLINICAL_FLAGS в rules_engine).

CREATE TABLE IF NOT EXISTS clinical_flag (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT,
    encounter_id  TEXT,
    key           TEXT,   -- что за флаг (напр. "hospitalized_3mo", "local_signs", "bronchial_obstruction")
    value         TEXT,   -- "true"/"false" или категория
    category      TEXT,   -- social_risk / exam / complication / context / vaccination
    recorded_date TEXT
);

CREATE INDEX IF NOT EXISTS idx_clinflag_patient ON clinical_flag (patient_id, key);

-- ===== План лечения + цель =====

CREATE TABLE IF NOT EXISTS care_plan (
    id          TEXT PRIMARY KEY,
    patient_id  TEXT,
    condition_id TEXT,
    status      TEXT,   -- active / suspended / completed
    intent      TEXT,   -- plan
    period_start TEXT,
    period_end   TEXT,
    created_date TEXT
);

CREATE TABLE IF NOT EXISTS goal (
    id            TEXT PRIMARY KEY,
    patient_id    TEXT,
    care_plan_id  TEXT,
    description   TEXT,
    target_metric TEXT,   -- LOINC код (напр. АД-систола)
    target_value  REAL,
    target_unit   TEXT,
    status        TEXT,   -- in-progress / achieved / not-achieved
    start_date    TEXT,
    achievement_date TEXT
);

-- ===== Путь пациента (state machine) =====

CREATE TABLE IF NOT EXISTS pathway (
    patient_id  TEXT PRIMARY KEY,
    state       TEXT,
    label       TEXT
);

-- ===== Кэш оценки по протоколу ВП (чтобы дашборд не делал N+1 CAP-расчётов) =====

CREATE TABLE IF NOT EXISTS cap_cache (
    patient_id  TEXT PRIMARY KEY,
    applicable  INTEGER,   -- 0/1
    severity    TEXT,      -- moderate / severe / NULL
    setting     TEXT,      -- outpatient / inpatient / NULL
    compliant   INTEGER,   -- 0/1
    computed_at TEXT
);

-- ===== Индексы =====

CREATE INDEX IF NOT EXISTS idx_encounter_patient     ON encounter (patient_id, start);
CREATE INDEX IF NOT EXISTS idx_condition_patient    ON condition_ (patient_id);
CREATE INDEX IF NOT EXISTS idx_observation_patient   ON observation (patient_id, code, date);
CREATE INDEX IF NOT EXISTS idx_diagreport_patient   ON diagnostic_report (patient_id, date);
CREATE INDEX IF NOT EXISTS idx_servicereq_patient   ON service_request (patient_id, status);
CREATE INDEX IF NOT EXISTS idx_medreq_patient       ON medication_request (patient_id, status);
CREATE INDEX IF NOT EXISTS idx_allergy_patient      ON allergy_intolerance (patient_id);
CREATE INDEX IF NOT EXISTS idx_goal_patient         ON goal (patient_id, status);
