# ClinicalVerdict — контракт для UI (спринт 1)

Единый объект, который шаблон карточки пациента рендерит **без** технических кодов.
Движок сверки остаётся `protocol_cap.evaluate_cap(pid)`; для UI — обёртка
`verdict_for_ui(assessment)` (реализует `clinic-protocol`).

## Когда протокол не применим

```json
{
  "applicable": false,
  "protocol_title": null,
  "headline": "Протокол ВП не активен",
  "next_step": "Укажите диагноз внебольничной пневмонии из справочника МКБ.",
  "checks": [],
  "ok": true
}
```

## Когда протокол активен

```json
{
  "applicable": true,
  "protocol_id": "cap_adult_768",
  "protocol_title": "Внебольничная пневмония (КП МЗ РБ №768)",
  "setting_label": "Амбулаторно",
  "severity_label": "Нетяжёлая",
  "ok": false,
  "headline": "Есть отклонения от протокола",
  "next_step": "Назначить амоксициллин внутрь на 7–14 дней.",
  "expected_therapy": {
    "title": "Амоксициллин внутрь",
    "detail": "Нетяжёлая ВП без факторов риска — препарат первой линии."
  },
  "checks": [
    {
      "level": "problem",
      "title": "Антибактериальная терапия не назначена",
      "action": "Назначить амоксициллин внутрь (курс 7–14 дней)."
    },
    {
      "level": "info",
      "title": "Нет записи температуры тела",
      "action": "Измерить и записать температуру при осмотре."
    }
  ]
}
```

## Поля (обязательные для спринта 1)

| Поле | Тип | Для UI |
|---|---|---|
| `applicable` | bool | Показывать блок протокола или нет |
| `protocol_id` | str\|null | Внутри; в UI можно не показывать |
| `protocol_title` | str\|null | Заголовок блока |
| `setting_label` | str | «Амбулаторно» / «Стационар» |
| `severity_label` | str | «Нетяжёлая» / «Тяжёлая» |
| `ok` | bool | Соответствует протоколу (нет problem-зазоров) |
| `headline` | str | Одна строка итога |
| `next_step` | str\|null | Главная **подсказка**: что сделать дальше |
| `expected_therapy.title` | str | Ожидаемая терапия **без ATC** |
| `expected_therapy.detail` | str | Краткое обоснование языком врача |
| `checks[]` | list | Список **проверок** |
| `focus_stage` | str\|null | Куда вести: `med` / `diag` / `exam` / `cond` / `anam` |
| `cta_label` | str\|null | Подпись кнопки «К назначениям» и т.п. |

### Элемент `checks[]`

| Поле | Значения | UI |
|---|---|---|
| `level` | `problem` \| `info` | problem = отклонение; info = замечание |
| `title` | str | Что не так / чего нет (без кодов gap) |
| `action` | str | Что сделать |

Маппинг из внутреннего `gaps[].severity`:
- `warning` → `problem`
- `info` → `info`

Внутренние `gaps[].code` (например `not_first_line_abt`) **не попадают** в ClinicalVerdict для шаблона. Для `tools/scenarios.py` по-прежнему можно читать сырой `evaluate_cap`.

## Запрещено в полях для UI

- ATC-коды (`J01CA04`) в `title` / `next_step` / `expected_therapy`
- LOINC-коды, имена gap-кодов, JSON, «warning/info» как текст для врача
- Английские enum'ы (`outpatient`, `severe`) — только русские labels

## Реализация (clinic-protocol)

1. `docs/protocols/protocol_registry.yaml` — `cap_adult_768` + МКБ + ссылка на правила АБТ.
2. Функция `verdict_for_ui(assessment: dict) -> dict` — предпочтительно новый модуль
   `protocol_verdict.py` (импорт из `app.py` сделает architect при merge, либо
   узкий импорт согласуется в ТЗ).
3. Не менять семантику `evaluate_cap` без согласования с architect.

## Потребитель (clinic-ui)

Шаблон использует **только** ClinicalVerdict. Не выводить `cap.gaps[].code`,
`atc_code` в блоке вердикта. Сырой `cap` можно оставить для отладки только если
спрятан и не виден в демо.
