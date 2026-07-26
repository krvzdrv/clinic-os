# UI unify — статус (после page-system v2)

Канон зон и атомов: [`ui-design-guide-page-system.md`](./ui-design-guide-page-system.md).  
Базовые компоненты: [`ui-design-guide.md`](./ui-design-guide.md).  
Токены: [`STYLE_GUIDE.md`](./STYLE_GUIDE.md) + `static/clinic.css`.  
Правила агента: `.cursor/rules/clinic-ui-patterns.mdc`.

## Уже в UI

- Шапка пациента; диагноз-контейнер (avatar 40px + rail + issue внутри)
- Приём / контрольный визит — **один** header (collapsed / expanded); category-tag + ghost «Закрыть» в expanded
- Секции приёма = `.section-header` (icon + title + summary-badge + chevron), без степ-номеров
- Виталы = labeled attribute row; часть членства = `.tag-chip`
- Лейблы форм 12px sentence-case; empty-state «Диагноз не установлен»
- Дашборд: `.page-banner` guest + `.icon-avatar.sm` инициалы
- Tabler: `…/@tabler/icons-webfont@3.31.0/dist/tabler-icons.min.css`

## Остаточный checklist (не дизайн-вопросы)

1. Form labels: иконки + gap 6–8px
2. Confirm-dialog JS: убрать остаточный inline hex → токены
3. Soft: `.episode-rail` / `.diag-sub` не раздувать в третий card-язык

Исторический бриф с открытыми вопросами зон 1–5 **закрыт ответом** в page-system; не переспрашивать дизайн с нуля.
