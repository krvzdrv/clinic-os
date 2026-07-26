# ClinicalVerdict — контракт для UI (спринт 1)

Единый объект, который шаблон карточки пациента рендерит **без** технических кодов.
Контракт **протокол-независим**: тот же формат используется для любого числа
протоколов у одного пациента (сейчас — ВП `cap_adult_768` и ЖДА `ida_adult_23`).

- Движок сверки — свой модуль на протокол: `protocol_cap.evaluate_cap(pid)` (ВП),
  `protocol_anemia.evaluate_ida(pid)` (ЖДА). Оба возвращают одинаковую форму
  `{applicable, setting, severity, expected_regimen, compliant, gaps, ...}`.
- `protocol_dispatch.py` — единая точка входа: `patient_assessments(pid)` /
  `patient_verdicts(pid)` перечисляют **все** применимые пациенту протоколы
  (диагноз МКБ пациента входит в `icd_codes` протокола в `protocol_registry.yaml`)
  и для каждого зовут `verdict_for_ui(assessment, protocol_id)`.
- У пациента может быть активно сразу несколько протоколов (например ВП + ЖДА) —
  каждый получает свою независимую вложенную CDS-карточку под «своим» диагнозом
  (`verdict_by_condition` в шаблоне), а не один общий баннер.
- Добавление нового протокола = новый evaluate_* модуль той же формы + запись
  в `PROTOCOL_EVALUATORS` (`protocol_dispatch.py`) + `protocol_registry.yaml`.
  `protocol_verdict.py` и шаблон не меняются под конкретный протокол.

## Когда протокол не применим

`protocol_dispatch.patient_assessments(pid)` уже отфильтровал неприменимые
протоколы — `verdict_for_ui(assessment, protocol_id)` с `applicable=False`
вызывается редко (напр. прямой вызов в отладке). Текст общий, не привязан
к конкретному протоколу — заголовок берётся из `protocol_registry.yaml[protocol_id].title`:

```json
{
  "applicable": false,
  "protocol_title": null,
  "headline": "Протокол «Внебольничная пневмония (КП МЗ РБ №768)» не активен",
  "next_step": "Укажите диагноз из справочника МКБ, входящий в протокол",
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
  "headline": "Не назначена антибактериальная терапия",
  "next_step": "Назначить амоксициллин внутрь на 7–14 дней.",
  "show_therapy": true,
  "expected_therapy": {
    "title": "Амоксициллин внутрь",
    "detail": "Нетяжёлая ВП без факторов риска — препарат первой линии."
  },
  "checks_primary": [
    {
      "level": "problem",
      "title": "Антибактериальная терапия не назначена",
      "action": "Назначить амоксициллин внутрь (курс 7–14 дней)."
    }
  ],
  "checks_more": [
    {
      "level": "info",
      "title": "Не запланирована повторная R-графия ОГК через 4–6 нед",
      "action": "Контрольная R-графия ОГК через 4–6 нед."
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
| `focus_stage` | str\|null | Куда вести: `med` / `diag` / `exam` / `cond` / `anam` / `actions` / `reassess` / `repeat_cxr` (см. `UI_PROCESS_MAP.md`) |
| `reason` | str\|null | Одна короткая причина (без стены виталов) |
| `tier` | str | `ok` / `warn` / `critical` — уровень шума CDS |
| `cta_label` | str\|null | Подпись кнопки действия на месте (не «прыжок» по якорю) |
| `suggest_atc` | str\|null | ATC для предвыбора в форме (в текст UI не выводится) |
| `suggest_route` | str\|null | `oral` / `iv` — маршрут терапии по режиму ведения / факторам |
| `no_active_therapy` | bool | `true`, если терапия **не назначена вовсе** (`no_abt`/`no_iron_therapy`) — кнопка «Назначить …», а не «Заменить …» |

### Элемент `checks[]`

| Поле | Значения | UI |
|---|---|---|
| `level` | `problem` \| `info` | problem = отклонение; info = замечание |
| `title` | str | Что не так / чего нет (без кодов gap) |
| `action` | str | Что сделать |
| `cds_override` | bool (опц.) | true — врач подтвердил hard-stop; UI показывает «осознанно», эпизод **не** ok |

Маппинг из внутреннего `gaps[].severity`:
- `warning` → `problem`
- `info` → `info`

Когда есть override: `ok=false`, `headline` отражает осознанное назначение вне протокола (см. `CDS_SIGNALING.md`).  
Внутренние `gaps[].code` (например `not_first_line_abt`) **не попадают** в ClinicalVerdict для шаблона. Для `tools/scenarios.py` по-прежнему можно читать сырой `evaluate_cap`.

## Запрещено в полях для UI

- ATC-коды (`J01CA04`) в `title` / `next_step` / `expected_therapy`
- LOINC-коды, имена gap-кодов, JSON, «warning/info» как текст для врача
- Английские enum'ы (`outpatient`, `severe`) — только русские labels

## Реализация

1. `docs/protocols/protocol_registry.yaml` — реестр `protocol_id -> {title, icd_codes, therapy_rules}`;
   сейчас `cap_adult_768` (ВП) и `ida_adult_23` (ЖДА).
2. `protocol_verdict.verdict_for_ui(assessment, protocol_id=DEFAULT_PROTOCOL_ID)` — **уже в репо**;
   расширять контракт согласованно с UI и для всех протоколов одновременно (не только ВП).
3. `protocol_dispatch.py` — `PROTOCOL_EVALUATORS`, `patient_assessments(pid)`, `patient_verdicts(pid)`.
4. Не менять семантику `evaluate_cap`/`evaluate_ida` без согласования с architect.  
   Override: `CDS_SIGNALING.md` / `process_registry.yaml` → `cds_policy`.

## Потребитель (clinic-ui)

Шаблон использует **только** ClinicalVerdict. Не выводить `cap.gaps[].code`,
`atc_code` в блоке вердикта. Сырой `cap` можно оставить для отладки только если
спрятан и не виден в демо.
