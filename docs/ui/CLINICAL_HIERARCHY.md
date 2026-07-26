# Промпт для агента: clinical hierarchy UI (clinic-os)

Скопируй блок **«PROMPT»** ниже в чат другому агенту при правках UI карточки пациента / диагноза / CDS.  
Сначала (или вместе) приложи [`STYLE_GUIDE.md`](./STYLE_GUIDE.md) — токены и семантика ролей.  
Cursor: `.cursor/rules/clinic-ui-patterns.mdc` на `templates/**` / `static/**`.

Связано: `clinic-ui-doctor.mdc` (язык врача), `static/clinic.css`, `CDS_SIGNALING.md`.

---

## PROMPT

```
Ты правишь UI clinic-os (Flask + Jinja, static/clinic.css).

Цель: воспроизвести паттерн «диагноз-контейнер → вложенные лечение/цель/замечания»
и «labeled attribute row» (микро-лейбл с иконкой + значение + бейдж справа).
Не изобретай новую визуальную систему.

### Токены
Только static/clinic.css — см. docs/ui/STYLE_GUIDE.md.
Канон: --ink/--faint/--cream/--surface/--line; success --green*; danger --red*.
Алиасы ок: --text-primary, --bg-danger, --surface-0/1/2 (уже объявлены).
Не хардкодить hex. Ролевые пары фон+текст не смешивать.
Запрещено: purple gradients, terracotta marketing, dark SaaS glow,
левые accent-bar на карточках.

### Паттерн 1 — Nested clinical hierarchy + connector rail
- Диагноз (Condition) = контейнер, не строка таблицы.
- Заголовок = **icon avatar 40px** (`ti-stethoscope`) + название 14–15px weight 500
  + код МКБ тусклым рядом + статус-бейдж справа (flex, margin-left:auto).
  Тот же parent-header атом, что у Encounter (`ti-calendar-event`), но бейдж
  диагноза = green/grey (клиника), визита = accent/grey (админ) —
  см. `ui-design-guide-page-system.md`.
- Внутри — дети (лечение, цель терапии) с вертикальным connector rail слева:
  border-left: 2px solid var(--line); margin-left: ~7px; padding-left: ~16px.
  Смысл FHIR: MedicationRequest.reasonReference и Goal.addresses → один Condition.
- Несколько диагнозов = несколько контейнеров, у каждого свои дети.
- Encounter / карта приёма — ОТДЕЛЬНО, вне контейнера диагноза
  (приём разовый; followup = «Контрольный визит»; диагноз и цель живут дольше).
  Один header приёма, не два.
- Condition и Encounter — независимые списки; связь через `encounter_reason`
  (reasonReference), не «диагноз внутри визита».
- `#triage-panel` сверху страницы агрегирует issues со всех активных диагнозов;
  CDS-карточка остаётся вложенной в Condition.
- DetectedIssue / «Несоответствие протоколу» — ВНУТРИ контейнера диагноза,
  после лечения и цели; не отдельная карта на уровне всей страницы.
- «Ещё N замечаний» формулировать «по этому диагнозу».

### Паттерн 2 — Labeled attribute row (это «ярлычки», которые должны выглядеть круто)
Каждый дочерний атрибут:

<div class="attr-row">
  <p class="attr-label">  <!-- 12px, color: var(--faint); margin: 0 0 2px -->
    <i class="ti ti-{icon}" aria-hidden="true"></i>  <!-- 13px, тот же цвет -->
    {Название атрибута}   <!-- напр. «Лечение», «Цель терапии» -->
  </p>
  <div class="attr-value-row">  <!-- display:flex; align-items:center;
                                    justify-content:space-between; gap:8px -->
    <p class="attr-value">  <!-- 14px, margin:0 -->
      <span style="font-weight:500">{ключевое}</span> · {детали}
    </p>
    <!-- статус ТОЛЬКО если есть; иначе не рендерить span -->
    <span class="badge red|green|grey">{статус}</span>
  </div>
</div>

Три обязательных решения (не ломать):
1) Лейбл тише и меньше значения (12 vs 14) — глаз читает «подпись → факт».
2) Иконка в лейбле, не в значении; приглушённая; по смыслу поля
   (ti-pill, ti-target-arrow, ti-calendar, ti-flask, ti-stethoscope).
3) Статус — пилюльный бейдж справа, никогда внутри предложения.
   Фон+текст одной роли: .badge.green / .badge.red / .badge.grey
   (пары --*-bg + --* из clinic.css). Никогда чёрный на цветном.

Термин: «Цель терапии» (не «Цель / план»).

### Паттерн 3 — Алерт внутри родителя
- Один акцент: рамка var(--red-line), опционально фон --red-bg.
- Лейбл роли (короткий, можно uppercase 11–12px --red) →
  действие 14px weight 500 → CTA (primary danger / ghost secondary).
- Override «осознанно» = badge.grey; не делать эпизод зелёным.

### Антипаттерны
- Левая цветная accent-bar на всю секцию/карточку (запрещено).
  Connector rail — только у списка детей внутри диагноза.
- Дублировать одни данные в двух местах блока.
- Сырые ATC, LOINC, gap-коды (not_first_line_abt) в UI врача.
- Карточки ради карточек; алерт-сосед диагноза, если он вывод из диагноза+лечения.

### Перед сдачей
1. Связь parent→child видна из вложенности/коннектора, не из порядка карточек.
2. Нет пустых badge-слотов.
3. Только токены :root.
4. Приём отдельно от диагноза.
```

---

## Краткая шпаргалка (человеку)

| Элемент | Как |
|---------|-----|
| Диагноз | Контейнер + заголовок + бейдж статуса |
| Лечение / цель | Connector rail + labeled row |
| Несоответствие | Внутри диагноза, красная рамка + CTA |
| Ещё замечания | «по этому диагнозу» |
| Приём | Снаружи диагноза |
| Статус | Бейдж справа, пара цветов роли |
| Полоса слева на карточке | Нет |
| Connector у детей | Да |
