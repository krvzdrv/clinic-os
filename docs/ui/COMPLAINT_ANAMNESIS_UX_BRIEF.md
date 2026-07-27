# Brief: UX жалобы и анамнеза в приёме

Промпт / ТЗ для UI-агента. **Задача — предложить интерфейсное решение**, не писать код сразу. В конце — варианты и рекомендация, как реализовать в текущем шаблоне.

Проект: **clinic-os** (Flask + Jinja, карточка пациента `templates/patient.html`). Язык UI — русский, врач/протокол, без внутреннего жаргона.

---

## Роль агента

Ты — UX/UI-консультант по клиническому интерфейсу. Нужно:

1. Понять, как сейчас собраны разделы **Жалоба** и **Анамнез**.
2. Найти решение, где **слово «Жалоба» не дублируется** на экране.
3. Сохранить сценарий: **ввести произвольный текст → сохранить → потом отредактировать**.
4. Предложить 2–3 варианта UI и рекомендовать один под текущую архитектуру (`fstep` + rail).
5. Описать, **что менять в разметке** (какие подписи убрать/переименовать, read/edit pattern), без большого рефакторинга бэкенда.

Не предлагай «карточки ради карточек», дашбордный шум, фиолетовые градиенты. Ориентир стиля: `docs/ui/STYLE_GUIDE.md`, паттерн секций: `docs/ui/encounter-sections-collapsed.html`.

---

## Боль пользователя (продукт)

Сейчас на одном экране «Жалоба» повторяется много раз, например:

| Где | Текст |
|-----|--------|
| Rail шагов приёма | `Жалоба` |
| Заголовок секции `fstep` | `Жалоба` |
| Label внутри формы | `Жалоба` |
| Placeholder | «…или „жалоб нет, контроль“» |
| (раньше / при создании приёма) | `Жалоба сегодня` в `encounter_form` |

Итог: визуальный шум, кажется, что это разные сущности. Нужно **одно место смысла** + понятный ввод/редактирование текста.

То же касается **Анамнеза**: заголовок секции + label «Анамнез» в textarea — похожее дублирование.

---

## Желаемое поведение (функционал)

### Жалоба

- Свободный текст (уже `textarea`, `maxlength=300`).
- Сохранить → остаётся в приёме.
- Открыть снова → можно **отредактировать** и сохранить (overwrite).
- Пустой текст = жалоба не указана (секция «не заполнена»).

### Анамнез

- Свободный текст (уже `textarea`).
- Сохранить с `replace=1` **перезаписывает** предыдущий текст анамнеза этого приёма.
- Пустой текст при replace = очистка.
- В той же секции ниже остаются **факторы риска** (чипы) — это отдельная сущность, не смешивать с текстом анамнеза в UI-копирайте.

### Не цель этого брифа

- Не менять модель данных без необходимости.
- Не объединять жалобу и анамнез в один POST, если UI этого не требует.
- Не возвращать чипы/add-panel для самого текста жалобы/анамнеза (от них уже ушли).

---

## Как устроено сейчас (данные)

| Сущность | Хранение | API |
|----------|----------|-----|
| Жалоба | `encounter.complaint` | `POST /patient/<pid>/encounter/<eid>/complaint` → `update_encounter_complaint_route` → `fs.update_encounter_complaint` |
| Анамнез (текст) | `clinical_flag` с `category='anamnesis'`, в `key` лежит сам текст | `POST /patient/<pid>/anamnesis` (`replace=1`, `encounter_id`, `text`) → `add_anamnesis_route` |
| Факторы риска | `clinical_flag` с `category='social_risk'` | отдельная форма `flag_form` в секции Анамнез |

Шаги приёма (rail + `fstep`):  
`Жалоба` → `Анамнез` → `Осмотр` → `Диагноз` → `Обследование` → `Лечение`.

Логика «заполнено»:

- `complaint_done = e.complaint`
- `anam_done = есть anamnesis flags ИЛИ social_risk flags`
- Бейдж свёрнутой жалобы: первые ~42 символа текста (или «Не указана»).
- Бейдж анамнеза: текст / фактор риска / **или красный бейдж первой аллергии пациента** (это отдельный UX-спорный момент: аллергия в шапке + в бейдже «Анамнез»).

---

## Где в коде (файлы)

- UI: `templates/patient.html`
  - макросы `complaint_form`, `anam_form` (~969–992)
  - сборка шагов ~1435–1610 (`flow-complaint`, `flow-anam`)
  - `encounter_form` всё ещё имеет поле «Жалоба сегодня» (~877) — при создании приёма (если форма создания снова появится в UI)
- Routes: `app.py` — `update_encounter_complaint_route`, `add_anamnesis_route`
- Store: `fhir_store.py` — `update_encounter_complaint`, флаги

CDS иногда встраивает `anam_form` в блок «сейчас» (`focus_now == 'anam'`) — учитывать, чтобы копирайт/паттерн не разъехались.

---

## Актуальный код форм (контекст)

### Макросы форм

```jinja
{# Жалоба и анамнез — всегда видимые textarea (без плашек/add-panel). #}
{% macro complaint_form(pid, eid, current='') %}
<div class="fform anam-text-form">
  <form method="POST" action="{{ url_for('update_encounter_complaint_route', pid=pid, eid=eid) }}">
    <div class="field field-full"><label><i class="ti ti-message-2" aria-hidden="true"></i>Жалоба</label>
      <textarea name="complaint" rows="3" maxlength="300" placeholder="Кашель, лихорадка 3 дня — или «жалоб нет, контроль»">{{ current or '' }}</textarea>
    </div>
    <div class="form-actions"><button class="btn-small" type="submit">Сохранить</button></div>
  </form>
</div>
{% endmacro %}

{# Свободный анамнез: один текст на приём — сохранить заменяет прежние записи. #}
{% macro anam_form(pid, eid, current='') %}
<div class="fform anam-text-form">
  <form method="POST" action="{{ url_for('add_anamnesis_route', pid=pid) }}">
    <input type="hidden" name="encounter_id" value="{{ eid }}">
    <input type="hidden" name="replace" value="1">
    <div class="field field-full"><label><i class="ti ti-notes" aria-hidden="true"></i>Анамнез</label>
      <textarea name="text" rows="4" placeholder="Анамнез заболевания и жизни — произвольным текстом">{{ current or '' }}</textarea>
    </div>
    <div class="form-actions"><button class="btn-small" type="submit">Сохранить</button></div>
  </form>
</div>
{% endmacro %}
```

### Встраивание в шаги приёма

```jinja
<nav class="episode-rail" aria-label="Шаги приёма">
  <a href="#flow-complaint" …>Жалоба</a>
  <a href="#flow-anam" …>Анамнез</a>
  …
</nav>

{% call fstep('Жалоба', 'ti-message-2', st_complaint, bj.kind, bj.text, 'flow-complaint',
     open=st_complaint in ('active','attention')) %}
  {{ complaint_form(patient.id, e.id, e.complaint or '') }}
{% endcall %}

{% call fstep('Анамнез', 'ti-notes', st_anam, ba.kind, ba.text, 'flow-anam',
     open=st_anam in ('active','attention')) %}
  {% set anam_text = anam_notes|map(attribute='key')|list|join('\n') %}
  {{ anam_form(patient.id, e.id, anam_text) }}
  <p class="section-label">Факторы риска</p>
  … chips + add_panel('Фактор риска') …
{% endcall %}
```

### Макрос заголовка секции (почему label внутри кажется дублем)

```jinja
{% macro fstep(title, icon, state, badge_kind, badge_text, anchor='', open=false) %}
<details class="fstep {{ state }}" …>
  <summary class="section-header">
    <i class="ti {{ icon }} …"></i>
    <span class="section-header__title">{{ title }}</span>
    <div class="section-header__right">
      {# badge: placeholder / neutral (превью текста) / danger #}
      …
      <i class="ti ti-chevron-right …"></i>
    </div>
  </summary>
  <div class="fstep-b">{{ caller() }}</div>
</details>
{% endmacro %}
```

Когда секция открыта, badge скрывается CSS (`.fstep[open] … badge { display:none }`), но **title «Жалоба» + label «Жалоба»** остаются рядом.

### Создание приёма (второе поле жалобы в продукте)

```jinja
<div class="field grow">
  <label>…Жалоба сегодня</label>
  <input type="text" name="complaint" placeholder="Кашель, лихорадка 3 дня — или «жалоб нет, контроль»">
</div>
```

Сейчас кнопка «Новый приём» из тулбара убрана; поле всё ещё в макросе `encounter_form` (пустое состояние / будущее).

---

## Вопросы, на которые нужен ответ агента

1. **Где оставить слово «Жалоба» один раз?** (rail / title секции / label / sr-only)
2. Нужен ли паттерн **просмотр → «Изменить» → textarea**, или достаточно всегда видимого поля с «Сохранить»?
3. Как показать сохранённый текст в свёрнутом виде, не дублируя слово «жалоба» в badge?
4. Стоит ли **объединить Жалобу и Анамнез** в один шаг «Анамнез» с двумя textarea (Жалоба / Анамнез заболевания), или оставить два шага rail?
5. Что делать с аллергией в красном badge секции «Анамнез» — оставить / убрать / перенести?
6. Копирайт placeholder’ов без тавтологии («жалоб нет…» внутри «Жалоба»).

---

## Ожидаемый формат ответа агента

1. **Диагноз проблемы** (1 абзац): почему сейчас шумит.
2. **Рекомендуемый вариант** + 1–2 альтернативы (таблица: плюсы/минусы).
3. **Wireframe текстом** открытой и свёрнутой секции.
4. **План реализации** по файлам (`patient.html` макросы / CSS / что не трогать в API).
5. **Копирайт**: точные строки label / placeholder / empty / button на русском.
6. **Критерий готово**: на открытой секции слово «Жалоба» видно ≤ 1 раза; текст можно ввести и отредактировать без потери данных.

---

## Ограничения реализации (когда перейдёте к коду)

- Не плодить вторую форму жалобы в шапке приёма.
- Не ломать `replace=1` у анамнеза.
- Сохранить якоря `#flow-complaint`, `#flow-anam` или явно описать миграцию ссылок rail.
- Факторы риска оставить в секции анамнеза отдельным подблоком.
- Язык врача, коротко.
