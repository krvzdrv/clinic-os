# Бриф: clinic-protocol (спринт 1)

## Роль

Движок протокола ВП (КП №768, взрослые): реестр протокол↔МКБ, правила АБТ,
сверка фактов с ожиданием, контракт **ClinicalVerdict** для UI без технических кодов.

## North star

Каждое изменение должно усиливать демо «подсказка + проверка протокола» по облачной ссылке.
Не раздувать архитектуру ради второго протокола или NLP.

## Ветка

`agent/clinic-protocol/sprint1-verdict`

## Можно трогать

- `protocol_cap.py`, `protocol_rules.py`, `rules_engine.py`
- `docs/protocols/**` (в т.ч. новый `protocol_registry.yaml`, `cap_abt_rules.yaml`)
- `cds_service.py`, `care_plan_service.py`, `drug_service.py`
- `terminology.py` (осторожно: согласовать с UI при смене подписей/доз)
- `protocol_verdict.py` — `verdict_for_ui()` (существует; не создавать заново)

## Нельзя трогать

- `templates/**`
- `tools/e2e_test.py`, `tools/scenarios.py`
- `app.py`, `db.py`, `fhir_store.py`, `schema.sql`, `_seed_data.py`, `tools/seed_*.py`
  (если нужен импорт в `app.py` — остановись, эскалация `clinic-architect`)

## Задачи спринта 1

1. Создать [`docs/protocols/protocol_registry.yaml`](../protocols/protocol_registry.yaml):
   - `protocol_id: cap_adult_768`
   - название, ссылка на КП №768
   - `icd_codes` (как `PNEUMONIA_CODES` в terminology)
   - ссылка на `cap_abt_rules.yaml`
2. Подключить реестр к определению «протокол применим» (можно тонкой обёрткой над
   существующим `has_pneumonia` / `PNEUMONIA_CODES`, без ломки семантики).
3. Реализовать `verdict_for_ui(assessment)` строго по
   [`verdict-contract.md`](verdict-contract.md):
   - русские labels для setting/severity;
   - `expected_therapy` без ATC;
   - `checks[]` без gap-кодов;
   - `next_step` — одна главная подсказка (первый problem или ожидаемая терапия).
4. Не дублировать логику выбора АБТ вне YAML.

## Критерий готовности (DoD)

- [ ] Есть `protocol_registry.yaml` с `cap_adult_768`
- [ ] `verdict_for_ui(evaluate_cap(pid))` возвращает структуру из контракта
- [ ] В полях для UI нет ATC и кодов вроде `not_first_line_abt`
- [ ] Существующая семантика `evaluate_cap` для scenarios не сломана
  (`python3 tools/scenarios.py` — попросить qa прогнать или прогнать сам на изолированной SQLite)
- [ ] Краткий отчёт: что сделано, какие файлы, что нужно от architect (импорт в app)

## Запреты

- Не коммитить без спроса
- Не менять UI
- Не «угадывать» протокол из свободного текста
- Не тащить второй клинический протокол
