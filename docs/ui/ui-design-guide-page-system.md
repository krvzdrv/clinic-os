# clinic-os — единая система на страницу (addendum к ui-design-guide.md)

Базовые атомы (status badge, icon avatar, labeled attribute row, connector rail, Tabler outline) не меняются. Здесь — как их применять по зонам, где они не подходят напрямую, и какие новые атомы нужны.

> Реализация: CSS-атомы в `static/clinic.css` (`.badge.accent`, `.category-tag`, `.tag-chip`, `.section-label`, `.page-banner`, `.icon-avatar`).  
> Связано: `ui-design-guide.md`, `STYLE_GUIDE.md`, `CLINICAL_HIERARCHY.md`, `.cursor/rules/clinic-ui-patterns.mdc`.  
> Tabler CDN: `…/@tabler/icons-webfont@3.31.0/dist/tabler-icons.min.css`.

## Ключевое структурное решение: Encounter ≠ Condition, но оба — "container with rail"

Это не два разных визуальных языка, а два экземпляра одного и того же атома-контейнера с разной ролью state-бейджа:

| | Condition (диагноз) | Encounter (приём / контрольный визит) |
|---|---|---|
| Иконка заголовка | `ti-stethoscope` | `ti-calendar-event` |
| State-бейдж | `bg-success`/`bg-gray` — активный/разрешён (клиническое суждение) | `bg-accent`/`bg-gray` — открыт/закрыт (административное состояние) |
| Дети (rail) | Лечение, Цель терапии | Факты визита (см. зону 2), секции |
| Вложенный алерт | Несоответствие протоколу | (обычно нет — issue живёт на Condition, не на Encounter) |

**Почему разные роли цвета для state:** success/danger — это клиническая оценка (хорошо/плохо для пациента). Open/closed визита — это административный факт, не оценка. Если использовать `success` для "визит открыт", он визуально сольётся с "диагноз активный и всё хорошо" — на одной странице это создаёт ложную связь. Поэтому Encounter всегда использует `accent` (нейтрально-синий) для состояния, никогда `success`/`danger`.

## Таблица по зонам

| Зона | Паттерн / компонент | Токены / иконка | Не делать | Референс |
|---|---|---|---|---|
| 1. Свёрнутый аккордеон визита | Тот же parent-header, что у Condition, но collapsed: icon avatar 40px + заголовок + мета (дата) + state-бейдж + `ti-chevron-right` справа | `ti-calendar-event`, `bg-accent`/`text-accent` (открыт) или `bg-surface-2`/`text-muted` (закрыт) | Не давать визиту отдельную форму карточки, отличную от диагноза (например, без иконки или с левой цветной полосой) | см. icon avatar + parent-header из основного гайда |
| 1. Шапка открытого визита | Тот же parent-header, expanded: слева icon avatar+заголовок+дата, справа class-тег (нейтральный, нового атома "category tag", см. ниже) + secondary-кнопка "Закрыть" | `ti-calendar-event`; class-тег — `bg-surface-2`/`text-secondary`, НЕ ролевой цвет | Не красить class "амбулаторный/стационар" в success/danger — это классификация, не состояние | — |
| 1. Список прошлых визитов | Стек свёрнутых аккордеонов, каждый — collapsed parent-header | как выше | Не превращать список визитов в таблицу — он остаётся карточным списком, как диагнозы | — |
| 1. Секции внутри визита (анамнез/осмотр/диагноз/обследование/лечение) | Лёгкий атом **section header** (не parent-header с avatar): icon + заголовок + summary-badge + chevron; два состояния collapsed/expanded как у шапки визита | `ti-notes` / `ti-stethoscope` / `ti-file-description` / `ti-flask` / `ti-pill`; badge `danger`/`neutral`/`accent` или placeholder | Не давать секциям connector rail и 40px avatar; не дублировать badge в развёрнутом состоянии | `docs/ui/encounter-sections-collapsed.html` |
| 2. Факты визита (SpO2, t°, ЧД и т.п. — цифровые измерения) | **labeled attribute row** (как в диагнозе): лейбл+иконка сверху, значение снизу, badge только если вне нормы | иконки по смыслу (`ti-heartbeat`, `ti-thermometer`, `ti-lungs`); badge — `bg-danger`/`text-danger` если вне референса | Не показывать как chip — измерение не является "тегом", у него нет действия "удалить" в норме | — |
| 2. Факты визита (диагнозы визита, флаги, заказы — дискретные, удаляемые сущности) | Новый атом **removable tag chip** — отличается от status badge наличием `×` и семантикой "принадлежность к множеству", а не "состояние" | нейтральный `bg-surface-2`/`text-secondary` по умолчанию; ролевой цвет только если сам флаг клинически значим (напр. красный флаг → `bg-danger`) | Никогда не путать с status badge — badge read-only и означает состояние одного факта, chip — интерактивный и означает членство в списке | новый атом, см. ниже |
| 3. Формы ввода (+ Добавить, add-panel) | **labeled attribute row**-стиль лейблов, НЕ 11px uppercase — единый лейбл-стиль на весь продукт: 12px, `text-secondary`, sentence case, с иконкой. Плотность увеличенная (gap 6–8px вместо 10–14px) | иконки те же, что у соответствующего атрибута в карточке (согласованность форма↔отображение) | Не вводить второй лейбл-стиль (uppercase 11px) параллельно с 12px sentence-case — выбрать один на весь продукт | — |
| 3. Primary-кнопка в форме | Один `--fill-accent`/`--on-accent` кнопка на панель (Сохранить/Добавить). Остальные — secondary (ghost, `border-strong`) | `--fill-accent` для обычных форм; `--fill-danger` зарезервирован строго за CDS/protocol-alert кнопками | **Zone-scoped:** не более одной accent-filled кнопки на открытую зону (см. риски §3); danger CDS — другая роль и не конкурирует | — |
| 4. CDS при отсутствии диагноза | Тот же parent-header атом Condition, но в placeholder-состоянии: icon avatar нейтральный/warning, заголовок "Диагноз не установлен", сразу внутри — тот же алерт-паттерн (border danger/warning) с действием "Указать МКБ-код" | `ti-stethoscope`, `bg-warning`/`text-warning` для avatar, `border-warning` для issue-рамки (не danger — отсутствие диагноза это gap, не подтверждённая клиническая ошибка) | Не делать отдельную "пустую карточку" без иконки/структуры — она должна визуально быть тем же типом объекта, просто в пустом состоянии | — |
| 5. Дашборд — колонки таблицы | Остаётся табличным. Токены цвета и status badge — да; **connector rail — нет** (rail — для вложенных клинических сущностей, строки таблицы плоские, не иерархия) | `bg-{role}`/`text-{role}` для badge как везде | Не добавлять rail/parent-header в строку таблицы — это сломает построчную читаемость | — |
| 5. "Сделать сейчас" в дашборде | Переиспользовать **status badge** (danger/warning по срочности) | `bg-danger`/`text-danger` или `bg-warning`/`text-warning` | Не изобретать отдельный "priority chip" — это тот же атом, что и "не достигнута" в диагнозе | из основного гайда |
| 5. Идентификатор пациента в строке | **icon avatar**, но 32px вместо 40px (компактнее для табличного контекста), с инициалами | `bg-accent`/`text-accent`, текст инициалов 12px | Не использовать 40px — в таблице это займёт слишком много вертикального места и собьёт line-height строк | — |
| 5. Guest-banner | Новый атом **page banner** — см. ниже | `bg-{role}`/`text-{role}` по смыслу баннера (обычно `accent` или `warning`) | Не делать баннер с цветной полосой слева — тот же запрет, что и на карточках | новый атом, см. ниже |

## Риски, выявленные при разборе — и их решение

### 1. Двойной header визита (summary + открытая шапка)
Это не два компонента, а один и тот же узел в двух состояниях. Avatar + заголовок + дата не пересоздаются при разворачивании — меняется только правая часть строки (`ti-chevron-right` → `ti-chevron-down` + кнопка "Закрыть" вместо только chevron) и добавляется контент ниже. Второй avatar/заголовок внутри открытого визита — ошибка, удалить: rail с фактами начинается сразу под единственной шапкой.

```html
<div style="display:flex; align-items:center; gap:8px;">
  <div><!-- icon avatar, не пересоздаётся при toggle --></div>
  <span style="font-weight:500; font-size:15px;">Приём · 24.07.2026</span>
  <span style="margin-left:auto;">
    <!-- collapsed: только ti-chevron-right -->
    <!-- expanded: category tag + ghost-кнопка "Закрыть" + ti-chevron-down -->
  </span>
</div>
```

### 2. Секции визита — один атом `.section-header`, не степ-аккордеон
Нумерация шагов и отдельная «attention-плашка» запрещены. Единая форма для всех секций: icon + title + summary-badge + chevron. Приоритет badge: (1) CDS/protocol issue секции → `danger`, (2) red flag / вне нормы → `danger`, (3) данные в норме → `neutral` или без badge (осмотр — без badge), (4) пусто → placeholder-текст без пилюли. Развёрнуто: badge скрыт, chevron-down, контент ниже — тот же принцип, что у шапки визита.

### 3. Restraint "одна accent-кнопка" — уточнение области действия
Правило работает не на уровне всей страницы, а **на уровне зоны** (одна открытая карточка/панель):
- `fill-accent` — максимум одна на открытую зону, зарезервирована за основным действием этой зоны (Сохранить в форме, Сохранить в шапке визита).
- Остальные действия в той же зоне — ghost/secondary, включая "Закрыть".
- `fill-danger` (CDS-issue) — отдельная ролевая зона (issue, не primary-действие), не считается в лимит accent и может сосуществовать с accent-кнопкой другой зоны на одном экране.

### 4. Дашборд — таблица без rail (осознанно)
"Табличная зона как исключение из rail" остаётся в силе. Guest-banner и icon avatar 32px **уже** на общих атомах (`page-banner`, `.icon-avatar.sm`) — долг закрыт; дальше только точечный polish (см. статус ниже).

## Статус внедрения (актуально)

| # | Работа | Статус |
|---|--------|--------|
| 1 | Визит — один header, два состояния | **Done** |
| 2 | Секции визита — `.section-header` + summary-badge, без степ-номеров | **Done** |
| 3 | Факты: labeled row / `.tag-chip` | **Done** (виталы, флаги, диагнозы визита, АБТ; ОС = labeled row) |
| 4 | Формы: 12px sentence-case + zone-scoped accent | **Partial** — лейблы ок; иконки в лейблах и плотность 6–8px — open |
| 5 | CDS empty-state («Диагноз не установлен») | **Done** |
| 6 | Дашборд: page-banner + avatar 32px + compact «Сделать сейчас» | **Done** — см. `quality-dashboard-form.html` |
| — | CDS CTA → `--fill-danger` | **Done** |
| — | Open-visit avatar `.accent` / visit-list `.sm` | **Done** |

Остаточный polish не блокирует демо: form icons/density; soft nav `.episode-rail` / `.diag-sub` не раздувать.

## Новые атомы (нет в ui-design-guide.md)

### Section header (секции визита)
Сворачиваемый заголовок секции внутри Encounter (не путать с parent-header диагноза/визита). Референс вариантов: `docs/ui/encounter-sections-collapsed.html`.

```html
<details class="fstep">
  <summary class="section-header">
    <i class="ti ti-notes section-header__icon"></i>
    <span class="section-header__title">Анамнез</span>
    <div class="section-header__right">
      <span class="section-badge section-badge--neutral">Кашель, лихорадка…</span>
      <i class="ti ti-chevron-right chevron"></i>
    </div>
  </summary>
  <!-- контент -->
</details>
```

`.section-label` остаётся для **подзаголовков внутри** уже открытой секции (напр. «Факторы риска»).

### Removable tag chip
Отличается от status badge наличием крестика и интерактивностью — обозначает членство в множестве (флаг, заказ, диагноз визита), а не состояние одного факта.

```html
<span style="display:inline-flex; align-items:center; gap:4px;
     background: var(--bg-{role}); color: var(--text-{role});
     font-size:12px; font-weight:500; padding:3px 6px 3px 10px;
     border-radius:12px;">
  {label}
  <i class="ti ti-x" style="font-size:14px; cursor:pointer; opacity:0.6;"
     aria-hidden="true"></i>
</span>
```
`{role}` — `gray`/neutral по умолчанию (обычный флаг/заказ); ролевой цвет (`danger` и т.п.) только если сам факт клинически значим (красный флаг). Крестик — всегда `ti-x`, `opacity:0.6`, при hover — `opacity:1` и `color: var(--text-danger)`.

### Category tag (не путать со status badge)
Для классификационных меток без клинической оценки (тип визита: амбулаторный/стационар; тип записи и т.п.).

```html
<span style="background: var(--surface-2); color: var(--text-secondary);
     font-size:11px; font-weight:500; padding:2px 8px;
     border-radius:12px; border:0.5px solid var(--border);">
  {категория}
</span>
```
Всегда нейтральный (`surface-2`/`text-secondary`), никогда `bg-{success/danger/warning}` — это единственное, что отличает его от status badge при одинаковой форме, и это отличие обязательно, иначе пользователь начнёт читать категорию как состояние.

### Page banner
Полноширинный баннер для системных уведомлений уровня страницы (guest-banner и подобные).

```html
<div style="background: var(--bg-{role}); border-radius:12px;
     padding:0.75rem 1rem; display:flex; align-items:center; gap:10px;">
  <i class="ti ti-{icon}" style="font-size:18px; color:var(--text-{role});"
     aria-hidden="true"></i>
  <span style="font-size:13px; color:var(--text-{role}); flex:1;">{текст}</span>
  <button style="flex-shrink:0;">{CTA}</button>
</div>
```
Без rounded-corner на одностороннем бордере, без цветной полосы слева — та же логика запрета, что и для карточек.

## Антипаттерны — подтверждено + добавлено

Из запроса — подтверждаю все четыре как обязательные запреты на весь продукт:
- ❌ Левая цветная полоса на карточке (акцент — рамкой на 2px по всему периметру, не полосой)
- ❌ Сырые ATC/LOINC/gap-коды напрямую врачу (только человекочитаемое название + код тусклым рядом, как `J15.9`)
- ❌ Второй набор цветов вне токенов clinic.css (никаких инлайновых hex)
- ❌ Статус в середине предложения (всегда отдельным badge справа через flex)

Дополнительные, обнаруженные при разборе зон 1–5:
- ❌ **Chip и status badge как взаимозаменяемые** — chip обозначает членство/интерактивность (есть `×`), badge — состояние (read-only). Смешение этих двух смыслов в одной форме — главный источник "второго языка" на текущем экране визита.
- ❌ **Больше одной accent-filled кнопки на открытую зону** (кроме отдельной по роли danger-кнопки CDS, которая не конкурирует, т.к. другая роль) — scope = зона/панель, не вся страница
- ❌ **Больше двух уровней connector rail** — Condition/Encounter → факты, и всё. Секции внутри визита — только section label, без своего rail.
- ❌ **Два лейбл-стиля одновременно** (11px uppercase и 12px sentence-case) — выбрать 12px `text-secondary` sentence-case как единственный на весь продукт, uppercase допустим только для `section label`, у которого другая роль (разделитель группы, не подпись атрибута).
- ❌ **Connector rail в строках таблицы дашборда** — rail только для карточных иерархических блоков, не для плоских списков.
- ❌ **Ролевой (success/danger/warning) цвет на категориальных метках** (class визита, category tag) — ролевые цвета зарезервированы строго за состоянием/оценкой, не за классификацией.
- ❌ **Смешение filled и outline иконок Tabler в одном интерфейсе** — только `-outline` (базовый набор без суффикса `-filled`).
- ❌ **Второй avatar/заголовок внутри открытого визита** — header один; collapsed/expanded меняют только правую часть строки.
- ❌ **Степ-аккордеон (нумерация + card) внутри Encounter** — только section label + контент; attention → severity-dot.
