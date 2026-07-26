# Style guide — clinic-os (для агентов)

Reference: прикладывай к задаче на UI вместе с  
[`ui-design-guide.md`](./ui-design-guide.md) (компоненты) и [`CLINICAL_HIERARCHY.md`](./CLINICAL_HIERARCHY.md) (иерархия).  
Cursor: `.cursor/rules/clinic-ui-patterns.mdc`. Токены: `static/clinic.css`.

---

## 0) Что переносится, а что нет

| | |
|--|--|
| **Не копировать буквально** | Токены чужих чат-виджетов (`--surface-1` из чужой системы) с *их* hex. У нас свои значения. |
| **Переносится** | Архитектура: CSS custom properties, роли (success/danger/warning), пары фон+текст, labeled rows, Tabler Icons, connector rail. |

Агент пишет код **под токены clinic-os**. Семантические имена (`--text-danger`) — алиасы на наш канон; они уже объявлены в `clinic.css`.

---

## 1) Канон токенов (graphite ward)

Палитра: тёплый камень + угольные кнопки + приглушённый steel.  
**Не:** teal-SaaS, terracotta/cream-marketing, кислотный blue-500, purple glow, dark-mode «AI SaaS».

### Поверхности и текст

| Роль | Канон | Семантический алиас |
|------|-------|---------------------|
| Фон страницы | `--cream` | `--surface-0` |
| Карточка / белая плоскость | `--surface` | `--surface-1` |
| Приглушённая панель | `--tint` | `--surface-2` |
| Текст основной | `--ink` | `--text-primary` |
| Текст вторичный | `--soft` | `--text-secondary` |
| Текст тусклый / лейблы | `--faint` | `--text-muted` |
| Граница | `--line` | `--border`, `--border-strong` |

### Семантические пары (всегда вместе)

| Роль | Фон | Текст | Линия |
|------|-----|-------|-------|
| success | `--green-bg` / `--bg-success` | `--green` / `--text-success` | `--green-line` / `--border-success` |
| danger | `--red-bg` / `--bg-danger` | `--red` / `--text-danger` | `--red-line` / `--border-danger` |
| warning | `--bg-warning` | `--text-warning` | `--border-warning` |
| neutral | `--tint` | `--faint` | `--line` |

**Правило:** никогда не мешать роли (`background: var(--bg-danger); color: var(--text-success)` — запрещено).  
Никогда чёрный/ink на цветном фоне бейджа — только парный `--text-{role}`.

### Действие и типографика

| | |
|--|--|
| Кнопка primary | `--accent` / `--accent-hover` (graphite-navy) |
| Шрифт | `--font` → IBM Plex Sans |
| Радиус | `--radius` → 8px (бейджи в иерархии — 6px) |

Новые hex в шаблонах **не вводить**. Нужен новый смысл — сначала алиас в `:root`, потом использование.

---

## 2) Иконки

- Набор: [Tabler Icons](https://tabler-icons.io/) (outline), webfont `ti ti-*`.
- В карточке пациента уже подключён CDN (см. `templates/patient.html`).
- Иконка в **лейбле** атрибута (~13px, цвет `--text-muted`), не в значении.
- По смыслу: `ti-stethoscope` диагноз · `ti-pill` лечение · `ti-target-arrow` цель · `ti-calendar` даты · `ti-flask` лаб.

---

## 3) Компонентные паттерны (кратко)

Полный промпт: [`CLINICAL_HIERARCHY.md`](./CLINICAL_HIERARCHY.md).

1. **Nested hierarchy + connector rail** — диагноз-контейнер; дети с `border-left: 2px solid var(--border)`; визит снаружи.
2. **Labeled attribute row** — лейбл 12px muted + иконка; значение 14px; статус — pill справа (`justify-content: space-between`), не в тексте.
3. **Issue внутри родителя** — DetectedIssue / несоответствие протоколу внутри диагноза; акцент `border: 2px solid var(--border-danger)`.
4. **Бейджи** — классы `.badge.green|red|grey` или явные `background: var(--bg-*); color: var(--text-*)`.

**Запрещено:** левая цветная полоса на всю карточку (accent bar). Connector rail — только у списка детей.

---

## 4) Промпт «приложи к задаче» (копипаст)

```
UI clinic-os. Читай docs/ui/ui-design-guide.md, STYLE_GUIDE.md, CLINICAL_HIERARCHY.md.
Токены только static/clinic.css. Канон или алиасы (--bg-danger, --text-primary).
Не хардкодь hex. Пары фон+текст одной роли.
Компоненты: status badge, icon avatar 40px, labeled attribute row (Tabler).
Структура: nested diagnosis + connector rail; issue внутри; визит снаружи.
Без левой accent-bar. Язык врача: clinic-ui-doctor.mdc.
```

---

## 5) Карта документов

| Файл | Зачем |
|------|--------|
| `docs/ui/ui-design-guide.md` | Компоненты: badge, icon avatar, labeled row + промпт |
| `docs/ui/ui-design-guide-page-system.md` | Зоны страницы: Encounter≠Condition, chip≠badge; **статус внедрения** — сверять с таблицей там, не с устаревшими «открытыми вопросами» UNIFY |
| `docs/ui/STYLE_GUIDE.md` (этот) | Токены clinic-os, что портабельно |
| `docs/ui/CLINICAL_HIERARCHY.md` | Nested diagnosis / connector rail |
| `static/clinic.css` | SSOT значений |
| `.cursor/rules/clinic-ui-patterns.mdc` | Автоподхват в Cursor |
| `.cursor/rules/clinic-ui-doctor.mdc` | Поведение/язык UI врача |
