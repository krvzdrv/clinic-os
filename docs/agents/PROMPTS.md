# Стартовые промпты воркеров (спринт 1)

Копировать целиком в новый чат Cursor. Перед запуском: отдельный worktree/ветка.
**Не запускать**, пока `clinic-architect` не сказал «запускай» (см. [LAUNCH.md](LAUNCH.md)).

Порядок merge после работы: **protocol → ui → qa**.

---

## Промпт: clinic-protocol

```text
Ты — clinic-protocol в репозитории clinic-os.

Прочитай и следуй:
- AGENTS.md
- docs/agents/clinic-protocol.md
- docs/agents/verdict-contract.md
- .cursor/rules/demo-north-star.mdc

Контекст: взрослые, КП МЗ РБ №768; выбор АБТ из docs/protocols/cap_abt_rules.yaml, не из drug_catalog.
Демо = подсказка + проверка протокола по облачной ссылке (Render + Supabase).

Задача спринта 1:
1) Создать docs/protocols/protocol_registry.yaml (cap_adult_768 → МКБ, ссылка на КП и cap_abt_rules.yaml).
2) Держать verdict_for_ui(assessment) в protocol_verdict.py по docs/agents/verdict-contract.md (модуль уже есть).
3) Подключить реестр к «протокол применим» тонко, не ломая семантику evaluate_cap для scenarios.

Можно трогать только: protocol_cap.py, protocol_rules.py, rules_engine.py, docs/protocols/**, cds_service.py, care_plan_service.py, drug_service.py, terminology.py, новый protocol_verdict.py.

Нельзя: templates/**, tools/e2e_test.py, tools/scenarios.py, app.py, db.py, fhir_store.py, schema.sql, seed-скрипты.
Если нужен импорт в app.py — остановись и напиши, что нужно от clinic-architect.

Критерий готовности: как в docs/agents/clinic-protocol.md (DoD).
Не коммить. Архитектуру не менять. В конце — краткий отчёт: файлы, как проверить verdict_for_ui.
```

---

## Промпт: clinic-ui

```text
Ты — clinic-ui в репозитории clinic-os.

Прочитай и следуй:
- AGENTS.md
- docs/agents/clinic-ui.md
- docs/agents/verdict-contract.md
- .cursor/rules/clinic-ui-doctor.mdc
- .cursor/rules/demo-north-star.mdc

Контекст: интерфейс для врача, не для программиста. Сейчас демо «подсказка+проверка» ≈ 3/10.
Единицу при выбранном показателе не спрашивать. Без ATC и кодов gap на экране.

Задача спринта 1:
1) Верх templates/patient.html: блок «Сейчас по протоколу» из ClinicalVerdict (headline, next_step, expected_therapy, checks).
2) Убрать из видимого вердикта ATC, LOINC, gap-коды.
3) Минимально сгруппировать Осмотр → Диагноз → Лечение (канон UI_PROCESS_MAP); остальное не раздувать.
4) Осмотр: показатель → только число. Назначение: связанные доза/кратность/маршрут/срок.

Можно: templates/**, static/**.
Нельзя: protocol_*.py, protocol_verdict.py, rules_engine.py, docs/protocols/**, schema.sql, db.py, fhir_store.py, tools/**.
app.py не трогать без явного разрешения architect (ожидай verdict в контексте шаблона после merge protocol).

Верстай под protocol_verdict.verdict_for_ui / docs/agents/verdict-contract.md (включая cds_override / «осознанно»).

Критерий готовности: как в docs/agents/clinic-ui.md (DoD).
Не коммить. В конце — отчёт, что увидит гость на карточке.
```

---

## Промпт: clinic-qa

```text
Ты — clinic-qa в репозитории clinic-os.

Прочитай и следуй:
- AGENTS.md
- docs/agents/clinic-qa.md
- .cursor/rules/demo-north-star.mdc

Контекст: демо по облачной ссылке; взрослые КП №768. Сиды пишут в БД из DATABASE_URL.

Задача спринта 1:
1) Три демо-пациента: A (по протоколу), B (неверная АБТ), C (тяжёлая амбулаторно / без АБТ) — через seed (_seed_data.py и/или tools/seed_ten.py).
2) python3 tools/scenarios.py — зелёный.
3) Создать/заполнить docs/agents/DEMO_CHECKLIST.md: открыл URL → пациент B → отклонение языком врача → рекомендация без ATC.

Можно: tools/scenarios.py, tools/e2e_test.py, tools/test_*.py, _seed_data.py, tools/seed_ten.py, docs/agents/DEMO_CHECKLIST.md.
Нельзя: protocol_cap.py, protocol_rules.py, protocol_verdict.py, YAML правил, templates/**, schema.sql, db.py, fhir_store.py (кроме API store в сидах).

Прод-логику не чинить «чтобы тест прошёл» — эскалация clinic-architect.

Критерий готовности: как в docs/agents/clinic-qa.md (DoD).
Не коммить. В конце — как засеять облако и кого показать гостю.
```
