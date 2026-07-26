# UI design guide — clinical card components

Переиспользуемый набор: токены, Tabler Icons, компоненты (status badge, icon avatar, labeled attribute row), вложенность.  
**Значения цветов в clinic-os** — только из `static/clinic.css` (см. §2). Не подставлять hex из чужих макетов.

Связано: [`STYLE_GUIDE.md`](./STYLE_GUIDE.md) · [`CLINICAL_HIERARCHY.md`](./CLINICAL_HIERARCHY.md) · [`ui-design-guide-page-system.md`](./ui-design-guide-page-system.md) (зоны Encounter/Condition, chip≠badge, статус внедрения) · `.cursor/rules/clinic-ui-patterns.mdc`

Прикладывать агенту вместе со скриншотами-референсами и задачей на UI.

---

## 1. Иконки

Библиотека: **Tabler Icons** (MIT), стиль **outline**, не filled.  
Каталог: https://tabler.io/icons

### CDN (Flask / без сборки) — как в `templates/patient.html`

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@tabler/icons-webfont@3.31.0/dist/tabler-icons.min.css">
```

Использование: `<i class="ti ti-stethoscope" aria-hidden="true"></i>` — префикс всегда `ti ti-{название}`.  
Предпочтительна **зафиксированная минорная версия**, не `@latest` (воспроизводимость демо).

### npm (React/Vue)

```bash
npm install @tabler/icons-react
```

```jsx
import { IconStethoscope } from '@tabler/icons-react';
<IconStethoscope size={20} color="var(--text-secondary)" />
```

### Таблица иконок

| Класс | Где | Смысл |
|-------|-----|--------|
| `ti-stethoscope` | icon avatar / заголовок диагноза | диагноз / осмотр |
| `ti-pill` | labeled row «Лечение» | АБТ / препарат |
| `ti-target-arrow` | labeled row «Цель терапии» | целевые показатели |
| `ti-flask` | алерт несоответствия (опц.) | клиническое/лаб. замечание |
| `ti-alert-triangle` | бейдж «Аллергии не указаны» | предупреждение |
| `ti-circle-filled` | точки severity у «Ещё N» | индикатор важности (цвет через `color`) |
| `ti-chevron-right` / `ti-chevron-down` | аккордеоны | раскрыть / свернуть |
| `ti-calendar` | контроль / даты | срок, визит |

Правило: иконка по смыслу поля. Новые — только outline (без `-filled`), кроме severity-точек.

---

## 2. Токены цвета (clinic-os)

**Не объявлять второй `:root` с другими hex.** Используй канон / алиасы из `clinic.css`:

| Семантика | clinic-os |
|-----------|-----------|
| `--surface-0` | страница (`--cream`) |
| `--surface-1` | карточка (`--surface`) |
| `--surface-2` | панель (`--tint`) |
| `--text-primary` / `--secondary` / `--muted` | `--ink` / `--soft` / `--faint` |
| `--border` / `--border-strong` | `--line` |
| `--bg-danger` + `--text-danger` | `--red-bg` + `--red` |
| `--bg-success` + `--text-success` | `--green-bg` + `--green` |
| `--bg-warning` + `--text-warning` | warning-пара в `:root` |
| `--bg-accent` + `--text-accent` | `--accent-soft` + `--steel` |

Тёмная тема (`[data-theme="dark"]`) в MVP **не используется** — не вводить без отдельного решения продукта.  
Правило пар: фон и текст одной роли, без смешивания и без hex в компонентах.

---

## 3. Компонент — status badge

Инлайн-пилюля справа от связанного текста.

```html
<div class="attr-value-row">
  <span class="attr-value">{текст}</span>
  <span class="badge red">{статус}</span>
  <!-- или: style="background:var(--bg-danger);color:var(--text-danger)" -->
</div>
```

Классы проекта: `.badge.green` / `.badge.red` / `.badge.grey` / `.badge.accent` / `.badge.warning` (см. `clinic.css`).  
`accent` — админ. состояние визита (открыт); `warning` — срочность «сделать сейчас».  
Позиция — `flex; justify-content: space-between`. Нет статуса — не рендерить span.

**Не путать:**
- **status badge** — состояние (read-only)
- **category tag** (`.category-tag`) — классификация без оценки (амбулаторный)
- **tag chip** (`.tag-chip`) — членство в списке + `×`
- **section label** (`.section-label`) — разделитель внутри Encounter
- **page banner** (`.page-banner`) — системный баннер страницы (guest)

Подробности и HTML-скелеты — `ui-design-guide-page-system.md` § «Новые атомы».

---

## 4. Компонент — icon avatar

Круглая подложка 40×40 для сущности (диагноз) или инициалов.

```html
<div class="icon-avatar" aria-hidden="true">
  <i class="ti ti-stethoscope"></i>
</div>
```

```css
.icon-avatar {
  width: 40px; height: 40px; border-radius: 50%;
  background: var(--surface-2); border: 1px solid var(--border);
  display: flex; align-items: center; justify-content: center; flex: none;
}
.icon-avatar .ti { font-size: 20px; color: var(--text-secondary); line-height: 1; }
.icon-avatar.danger { background: var(--bg-danger); border-color: transparent; }
.icon-avatar.danger .ti { color: var(--text-danger); }
```

Пропорция: иконка ≈ половина диаметра (40 → 20).

---

## 5. Компонент — labeled attribute row

```html
<div class="attr-row">
  <p class="attr-label">
    <i class="ti ti-pill" aria-hidden="true"></i>
    Лечение
  </p>
  <div class="attr-value-row">
    <p class="attr-value"><span class="k">Амоксициллин</span> · 500 мг · внутрь</p>
    <span class="badge grey">осознанно</span>
  </div>
</div>
```

Лейбл 12px muted + иконка 13px; значение 14px; статус — отдельный badge справа.

---

## 6. Вложенность сущностей

Родитель = контейнер-заголовок; дети = список с connector rail  
(`border-left: 2px solid var(--border-strong); margin-left: 7px; padding-left: 16px`).

Алерты, связывающие детей (DetectedIssue / несоответствие протоколу) — **внутри** родителя после детей, не отдельной картой страницы.  
Визит (Encounter) — **вне** контейнера диагноза.  
Подробнее: `CLINICAL_HIERARCHY.md`.

---

## 7. Промпт одним куском (Cursor)

```
UI clinic-os. Документы: docs/ui/ui-design-guide.md, docs/ui/STYLE_GUIDE.md,
docs/ui/CLINICAL_HIERARCHY.md. Токены только static/clinic.css (канон или
алиасы --bg-danger/--text-primary/…). Не хардкодить hex из чужих гайдов.

Tabler Icons outline: ti ti-{name}, версия как в patient.html (не @latest).
Таблица: ti-stethoscope, ti-pill, ti-target-arrow, ti-flask, ti-alert-triangle,
ti-calendar. Новые — outline по смыслу.

Компоненты:
1) status badge — pill справа, пара bg-{role}+text-{role} / .badge.*
2) icon avatar — круг 40px, иконка 20px
3) labeled attribute row — лейбл 12px+иконка, значение 14px, статус справа

Структура: parent→children через connector rail; issue внутри диагноза;
визит снаружи. Без левой accent-bar на карточках.
Язык врача: .cursor/rules/clinic-ui-doctor.mdc.
```
