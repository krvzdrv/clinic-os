# Паттерны ввода в карточке приёма

> **SSOT UI-паттернов** по типу данных. Порядок секций — [`docs/processes/UI_PROCESS_MAP.md`](../processes/UI_PROCESS_MAP.md).  
> Реализация: `templates/patient.html`, `static/clinic.css`, allowlist ключей — `terminology.py`.

## Принцип

Паттерн выбирается **типом факта**, не «как сделали соседнюю секцию».  
Value-first: сначала видно записанное; ввод — тот же контрол (без дубля «текст + select»).  
Primary fill — только CDS; формы приёма — secondary.

## Словарь тип → паттерн

| Тип | Примеры | Паттерн | Сохранение |
|---|---|---|---|
| Шкала 1-из-N | `general_condition` | `.gc-seg` сегменты | клик = POST |
| Множество из словаря | `CAP_PHYSICAL_FLAG_KEYS`, `CAP_IMAGING_FLAG_KEYS`, social_risk | `.flag-toggle` (все варианты видны; on = selected) | клик = add / clear |
| Свободный текст | жалоба, анамнез, заключение | textarea всегда видна | dirty → «Сохранить»; id вне soft-refresh |
| Виталы (число+ед.) | SpO2, t, АД, ЧСС, ЧД | `.vitals-grid` по полям | blur/Enter или «Сохранить» на поле; replace same code@encounter |
| Лаб. число | WBC, СРБ, ПКТ | каталог + значение (+ заказ) | POST observation |
| Каталог + атрибуты | МКБ, препарат | searchable select / combobox | submit; CDS order-sign до записи |
| Жизненный цикл исследования | SR → report/obs | Заказать → Результат в одной сущности | прогрессия статуса |

## Allowlist протокола (не показывать лишнее)

| Группа | Константа | Где в UI |
|---|---|---|
| Физикальные (КП №768) | `CAP_PHYSICAL_FLAG_KEYS` | Осмотр → Данные физического обследования |
| R-признаки (КП №768) | `CAP_IMAGING_FLAG_KEYS` | Обследование → Инструментальные |
| Факторы риска | `SOCIAL_RISK_FLAG_KEYS` | Анамнез → Факторы риска |
| Красные физикальные | `EXAM_RED_FLAG_KEYS` | chip / toggle `is-severe` |
| Красные R | `IMAGING_RED_FLAG_KEYS` | то же |

Наследие КП №204 (цианоз, сыпь, судороги…) в UI allowlist не входит.

## Soft-refresh контракт

После клинической записи: обновить CDS, chips, `.enc-sub` / `sub-*`.  
**Не** трогать `details.open`, scroll, и textarea жалобы/анамнеза (без `id=sub-*` на черновиках).

## Секции канона

Жалоба → Анамнез → Осмотр → Диагноз → Обследование → Лечение; сверка только в CDS.
