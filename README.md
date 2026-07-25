# Клиническая ИС для маленькой клиники — протокол лечения внебольничной пневмонии

Рабочее MVP-приложение, демонстрирующее разумную архитектуру клинической
информационной системы: подсказки врачу в точке помощи (CDS), независимая
проверка соответствия клиническим протоколам, метрики качества для руководства,
и всё это — на одном движке правил (single source of truth).

Реализован один профиль протокола:
- **Внебольничная пневмония (взрослые)** — по клиническому протоколу МЗ РБ №768
  от 05.07.2012: амбулаторный и стационарный блоки, оценка тяжести, показания
  к госпитализации и ОРИТ, обязательные исследования, выбор АБТ (с учётом
  аллергии на β-лактамы, факторов резистентности, MRSA, аспирации, гриппа),
  симптоматическая терапия, оценка эффективности АБТ через 48–72 ч, step-down
  с в/в на per os, длительность курса, критерии выписки, повторная R-графия.

## Что это

Система для ведения пациентов с внебольничной пневмонией в маленькой клинике:
- **Врач** открывает карту пациента → видит CDS-подсказки и оценку соответствия
  протоколу ВП (КП №768);
- **Зав. отделением** открывает дашборд → видит метрику качества (доля
  соответствия протоколу) и распределение по тяжести/условиям лечения;
- **Экспорт в CSV** — для отчётов, которые просят сверху.

## Быстрый старт (локально)

```bash
cd ~/Projects/clinic-os
pip3 install -r requirements.txt
python3 app.py
```

Открой http://127.0.0.1:5566 в браузере.

## Деплой онлайн (бесплатно)

См. [`docs/deployment.md`](docs/deployment.md) — пошаговая инструкция
для PythonAnywhere. Результат: `https://твоё-имя.pythonanywhere.com`, 0 ₽.

## Архитектура

```
app.py            ← UI-слой (Flask-роуты + шаблоны)
  ↑
cds_service.py    ← Слой 5: CDS Hooks (карточки в момент работы врача)
  ↑
rules_engine.py   ← Слой 3: движок правил (CQL-like, single source of truth)
protocol_cap.py   ← Слой 3b: регламент ВП (независимая проверка)
  ↑
fhir_store.py     ← Слой 0/1: репозиторий FHIR-подобных ресурсов
  ↑
db.py             ← сервис подключения к БД (единственный, кто трогает драйвер)
  ↑
Supabase Postgres  /  clinic.db (SQLite, локальный fallback)
```

**Слои разделены жёстко:** только `db.py` открывает соединение с БД.
`fhir_store` ходит через `db.py`. Правила и CDS ходят через `fhir_store`
и никогда не трогают БД напрямую. Переключение SQLite ↔ Supabase —
одна переменная окружения `DATABASE_URL`.

**Single source of truth:** функция `protocol_cap.evaluate_cap()` используется
и для подсказки врачу (через `cds_service`), и для метрики качества (через
`quality_measure_cap()`). Один код — два применения.

Подробное обсуждение архитектуры — в [`docs/architecture.md`](docs/architecture.md).

## Бизнес-процессы (Process Registry)

Клинические процессы ВП описаны как машиночитаемый реестр (SSOT процессного слоя):

- **Реестр (YAML):** [`docs/processes/process_registry.yaml`](docs/processes/process_registry.yaml) — два процесса
  (`cap_outpatient`, `cap_inpatient`), шаги, дорожки (функции), условия входа/выхода,
  сигналы (застрял/SLA), маппинг на таблицы БД и read-only `golden_checks_sql`.
- **Как читать реестр:** [`docs/processes/PROCESS_REGISTRY.md`](docs/processes/PROCESS_REGISTRY.md)
- **Семантика статусов:** [`docs/processes/STATUS_SEMANTICS.md`](docs/processes/STATUS_SEMANTICS.md)
- **BPMN-практики:** [`docs/processes/BPMN_PRACTICES.md`](docs/processes/BPMN_PRACTICES.md)
- **Обзор потоков (Mermaid):** [`docs/processes/PROCESSES_BPMN.md`](docs/processes/PROCESSES_BPMN.md)
- **BPMN-диаграммы:** [`docs/bpmn/cap-outpatient-mature.bpmn`](docs/bpmn/cap-outpatient-mature.bpmn),
  [`docs/bpmn/cap-inpatient-mature.bpmn`](docs/bpmn/cap-inpatient-mature.bpmn)
  (открываются в Camunda Modeler / bpmn.io).

## Слои и файлы

| Слой | Файл | Что делает |
|---|---|---|
| Подключение | `db.py` | Postgres (Supabase) или SQLite; единый интерфейс fetchone/fetchall/execute |
| 0/1 — Хранилище+модель | `fhir_store.py` | Репозиторий FHIR-подобных ресурсов (Patient, Condition, Observation, MedicationRequest, ClinicalFlag …) |
| 2 — Терминологии | `terminology.py` | МКБ-10/LOINC/ATC/исследования по протоколу ВП |
| 2 — Лекарства | `drug_service.py` | проверка антибиотиков и симптоматики (показания, аллергия, дублирование) |
| 3 — Правила | `rules_engine.py` | правила ВП (тяжесть, тахипноэ/тахикардия, ДН по SpO2, факторы риска, флаги) |
| 3b — Регламент | `protocol_cap.py` | независимая проверка соответствия КП МЗ РБ №768 (амбулаторно + стационар) |
| 4 — Путь пациента | `care_plan_service.py` | план/цель/цикл лечения ВП, госпитализация, выписка, контроль |
| 5 — CDS | `cds_service.py` | CDS Hooks: карточки info / suggestion / hard-stop по ВП |
| UI | `app.py` + `templates/` | Flask-роуты, дашборд, карта пациента, формы ввода |

## Хранилище данных

По умолчанию — локальный SQLite-файл `clinic.db` (бэкап = копия).
Для прод-режима — **Supabase Postgres** (бесплатно 500 МБ, управляемые бэкапы):

1. Создайте проект на https://supabase.com.
2. В Project Settings → Database возьмите **Connection string** (Direct).
3. Задайте её в переменной окружения `DATABASE_URL` (см. `.env.example`).
4. При запуске схема создастся автоматически — фейковых пациентов нет,
   БД стартует пустой. Реальные данные вносятся через веб-формы.

## Что увидеть

1. **Дашборд** (`/`) — метрика соответствия протоколу ВП, тяжесть, условия.
   Список пуст до добавления пациентов.
2. **Новый пациент** (`/patient/new`) — форма ввода ФИО, пола, даты рождения.
3. **Карта пациента** (`/patient/<id>`) — CDS-карточки, регламент ВП,
   клинические данные, формы добавления диагноза/препарата/флагов.
4. **Экспорт CSV** (`/export`) — таблица для отчётов (открывается в Excel).
5. **CDS Hooks API** — POST на `/cds-services/patient-view` и `/cds-services/order-sign`.

## API

```bash
# Метрика качества (JSON)
curl http://127.0.0.1:5566/api/measure

# Регламент ВП для пациента (JSON)
curl http://127.0.0.1:5566/api/protocol-cap/<patient_id>

# CDS Hook: patient-view
curl -X POST http://127.0.0.1:5566/cds-services/patient-view \
  -H "Content-Type: application/json" \
  -d '{"hook":"patient-view","context":{"patientId":"<patient_id>"}}'

# CDS Hook: order-sign (проверка противопоказания)
curl -X POST http://127.0.0.1:5566/cds-services/order-sign \
  -H "Content-Type: application/json" \
  -d '{"hook":"order-sign","context":{"patientId":"<patient_id>","medicationCode":"J01CA04"}}'
```

## Документация

- [`docs/architecture.md`](docs/architecture.md) — архитектурное обсуждение, слои, выбор технологий
- [`docs/concepts.md`](docs/concepts.md) — конспект концепций (DDD, CDS Hooks, CQL, CPG-on-FHIR)
- [`docs/deployment.md`](docs/deployment.md) — инструкция деплоя на PythonAnywhere (бесплатно)

## Стек

- **Python 3 + Flask** — веб-сервер, один файл `app.py`
- **PostgreSQL (Supabase)** / **SQLite** — база данных
- **HTML/CSS** — UI без фреймворков (без React, без сборки)

## Лицензия

Внутреннее использование. См. файлы для деталей.
