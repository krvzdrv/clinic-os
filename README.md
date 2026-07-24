# Контроль артериальной гипертензии — клиническая ИС для маленькой клиники

Рабочее MVP-приложение, демонстрирующее разумную архитектуру клинической
информационной системы: подсказки врачу в точке помощи (CDS), метрики
качества для руководства, и всё это — на одном движке правил
(single source of truth).

## Что это

Система для контроля артериальной гипертензии в маленькой клинике:
- **Врач** открывает карту пациента → видит CDS-подсказки по протоколу;
- **Зав. отделением** открывает дашборд → видит метрику качества (доля контроля АД);
- **Экспорт в CSV** — для отчётов, которые просят сверху.

## Быстрый старт (локально)

```bash
cd ~/Projects/hypertension-cds-demo
pip3 install -r requirements.txt
python3 app.py
```

Открой http://127.0.0.1:5566 в браузере.

## Деплой онлайн (бесплатно)

См. [`docs/deployment.md`](docs/deployment.md) — пошаговая инструкция
для PythonAnywhere. Результат: `https://твоё-имя.pythonanywhere.com`, 0 ₽.

## Архитектура

```
clinic.db (SQLite, один файл)           ← бэкап = копия файла
  ↑
fhir_store.py (Слой 0: данные)          ← FHIR-подобная модель
  ↑
rules_engine.py (Слой 3: 6 правил)     ← CQL-like, single source of truth
  ↑
cds_service.py (Слой 5: CDS Hooks)     ← карточки в момент работы врача
  ↑
app.py (Flask: роуты + UI)             ← один файл, одна команда запуска
```

**Single source of truth:** функция `uncontrolled_bp()` используется и для
подсказки врачу (через `cds_service`), и для метрики качества (через
`quality_measure_controlled()`). Один код — два применения.

Подробное обсуждение архитектуры — в [`docs/architecture.md`](docs/architecture.md).

## Слои и файлы

| Слой | Файл | Что делает |
|---|---|---|
| 0 — Хранилище | `fhir_store.py` | SQLite, FHIR-подобная модель, 10 тестовых пациентов |
| 3 — Правила | `rules_engine.py` | 6 правил: АД, передержка, диабет, дублирование терапии |
| 5 — CDS | `cds_service.py` | CDS Hooks: карточки info / suggestion / hard-stop |
| UI | `app.py` + `templates/` | Flask-роуты, дашборд, карта пациента, форма записи АД |

## Что увидеть

1. **Дашборд** (`/`) — 10 пациентов, метрика: доля контроля АД, флаги (передержка, диабет).
2. **Карта пациента** (`/patient/p1`) — CDS-карточки, клинические данные, этап пути.
3. **Форма записи АД** (`/patient/p1/bp`) — врач вводит измерение, всё пересчитывается.
4. **Экспорт CSV** (`/export`) — таблица для отчётов (открывается в Excel).
5. **CDS Hooks API** — POST на `/cds-services/patient-view` и `/cds-services/order-sign`.

## API

```bash
# Метрика качества (JSON)
curl http://127.0.0.1:5566/api/measure

# CDS Hook: patient-view
curl -X POST http://127.0.0.1:5566/cds-services/patient-view \
  -H "Content-Type: application/json" \
  -d '{"hook":"patient-view","context":{"patientId":"p1"}}'

# CDS Hook: order-sign (проверка противопоказания)
curl -X POST http://127.0.0.1:5566/cds-services/order-sign \
  -H "Content-Type: application/json" \
  -d '{"hook":"order-sign","context":{"patientId":"p2","medicationCode":"C09AA01"}}'
```

## Документация

- [`docs/architecture.md`](docs/architecture.md) — архитектурное обсуждение, слои, выбор технологий
- [`docs/concepts.md`](docs/concepts.md) — конспект концепций (DDD, CDS Hooks, CQL, CPG-on-FHIR, HAPI FHIR, Peleg)
- [`docs/deployment.md`](docs/deployment.md) — инструкция деплоя на PythonAnywhere (бесплатно)

## Стек

- **Python 3 + Flask** — веб-сервер, один файл `app.py`
- **SQLite** — база данных, один файл `clinic.db` (бэкап = копия)
- **HTML/CSS** — UI без фреймворков (без React, без сборки)
- **0 внешних зависимостей** кроме Flask

## Лицензия

Внутреннее использование. См. файлы для деталей.
