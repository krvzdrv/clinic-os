# CDS Signaling — сигнал врачу, осознанный override, непрерывная переоценка

> **SSOT политики сигналов.** Процессные шаги — в `process_registry.yaml`; статусы ресурсов — в `STATUS_SEMANTICS.md`. Этот документ фиксирует *поведение системы* при отклонениях от протокола: что показываем, когда останавливаем, как сохраняем решение врача и как пересчитываем картину.

Связанный код: `protocol_cap.evaluate_cap`, `protocol_cap.evaluate_abt_choice`, `cds_service`, `app.py` (order-sign + refresh), `protocol_verdict.verdict_for_ui`.  
UI path при сохранении АБТ: `app._medication_order_verdict` (тот же dual-check, что и `cds_service.cds_order_sign` для Hooks-карточки).

---

## 0) Три правила (не нарушать)

1. **Сигналим всегда.** После любой клинической записи система пересчитывает
   все applicable протоколы (`protocol_dispatch.refresh_protocol_cache`) и
   пишет **primary** в `cap_cache` (для дашборда). Карточка пациента показывает
   полный список через `patient_verdicts`. Не ждём «кнопки оценить».
2. **Врач может принять своё решение.** Soft-stop и hard-stop на `order-sign` **не запрещают** назначение навсегда: требуется явный акт согласия и **письменное обоснование** (для soft — чекбокс + причина; для hard — причина без чекбокса).
3. **Override видим.** Осознанное назначение вопреки CDS **не делает** эпизод «зелёным». В данных остаётся `medication_request.cds_override=1`, строка в `cds_override_log`, в UI — маркер «осознанно», в вердикте — отклонение с пометкой о подтверждении. Метрика качества считает это несоответствием.

---

## 1) Два горизонта проверки

| Горизонт | Когда | Что | Результат |
|----------|--------|-----|-----------|
| **Prospective (order-sign)** | Врач выбирает/подписывает препарат *до* сохранения | `drug_service` + `evaluate_abt_choice` / `evaluate_iron_choice` | info / **soft-stop (warning)** / **hard-stop** |
| **Prospective (finish encounter)** | Врач закрывает амбулаторный/контрольный приём *до* `finished` | gap `hospitalization_indicated` из `patient_assessments` | **soft-stop** (confirm+ack+причина → `cds_override_log`) |
| **Continuous (patient-view)** | После любой клинической записи + при открытии карты | полный `evaluate_cap` → `verdict_for_ui` | headline / reason / CTA / checks |

Prospective **не заменяет** continuous: даже после подтверждённого override continuous снова покажет отклонение — уже с флагом осознанности.

---

## 2) Order-sign: soft-stop и hard-stop

### 2.1 Уровни

| Уровень | Когда | UX | Поля формы |
|---------|--------|-----|------------|
| **SOFT-STOP** (`severity=warning`) | АБТ не по протоколу (`not_first_line_abt`), неоптимальная осторожность drug_service | Чекбокс согласия **и** обязательная текстовая причина — одного чекбокса недостаточно | `confirm=1`, `ack=1`, `override_reason` (непустой) |
| **HARD-STOP** (`severity=hard-stop`) | Аллергия, противопоказание, опасное взаимодействие | Обязательная текстовая причина; чекбокс не нужен | `confirm=1`, `override_reason` (непустой) |

Причина — не формальность: без неё запись в `cds_override_log` при аудите протокола
не объясняет, почему врач отклонился (только «что» отклонилось). Немотивированный
override так же бесполезен для контроля качества, как его отсутствие.

`evaluate_abt_choice` отдаёт **warning** (soft). Hard — только из `drug_service` (аллергия и т.п.).

### 2.2 Поведение

| Действие врача | Система |
|----------------|---------|
| Назначение при soft/hard без подтверждения | **Не сохраняет**. Ответ: `need_confirm=true`, `level=soft\|hard`, тексты CDS. |
| Soft: confirm+ack без причины | **400**, назначение не сохраняется (`error`: «Укажите причину отклонения от протокола»). |
| Soft: чекбокс + причина → «Назначить всё равно» | Сохраняет с `cds_override=1`; пишет `cds_override_log` (`severity=soft-stop`, `reason`). |
| Hard: confirm без причины | **400**, назначение не сохраняется. |
| Hard: confirm + причина | Сохраняет с `cds_override=1`; лог `hard-stop` + `reason`. |
| Препарат по протоколу без warning/hard | Сохраняет без dialog; `cds_override=0`. |

### 2.3 Данные

| Место | Смысл |
|-------|--------|
| `medication_request.cds_override` | `1` — врач подтвердил soft/hard; `0` — обычное |
| `medication_request.cds_override_detail` | Сводка сообщений CDS на момент confirm |
| `cds_override_log` | Append-only: кто/когда (timestamp), severity, category, issue, reason врача. **Нет** update/delete API |

---

## 3) Continuous: когда пересчитываем

После успешной записи/изменения любого из:

- `observation`, `clinical_flag` (в т.ч. общее состояние), `condition_`
- `medication_request` (назначение / stop)
- `allergy_intolerance`
- `service_request`, `diagnostic_report`
- `encounter` (создание / finish / admit / discharge)
- plan/goal (care_plan, follow-up, evaluate goal)

Алгоритм:

1. Сбросить кэш пациента (`clear_pid_cache`).
2. `protocol_dispatch.refresh_protocol_cache(pid)` —
   `patient_assessments` → `pick_primary_assessment` → `save_cap_cache(..., protocol_id)`.
4. В UI: при AJAX-записи, меняющей картину протокола — `reload`, чтобы `#now-action` / status-strip не врали.

Свободный текст анамнеза (`clinical_flag.category='anamnesis'`) протоколом **не** оценивается — пересчёт можно пропустить или выполнить no-op для единообразия.

---

## 4) Триаж-панель

Issues по диагнозам агрегируются в `#triage-panel` наверху карты (см. `UI_PROCESS_MAP.md` / CLINICAL_HIERARCHY). CDS-карточка остаётся вложенной в Condition; триаж только навигирует к диагнозу.

---

## 5) Связанные документы

| Документ | Роль |
|----------|------|
| `process_registry.yaml` → `cds_policy` | Машинный якорь политики |
| `STATUS_SEMANTICS.md` §4 | Семантика `cds_override` |
| `docs/agents/verdict-contract.md` | Поля вердикта для UI |
