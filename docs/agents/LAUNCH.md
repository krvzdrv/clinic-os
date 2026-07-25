# Ворота запуска воркеров

Документация и промпты спринта 1 **готовы**.  
Статус: спринт 1 воркеров **завершён**; `verdict` подключён в `app.py`. Merge/сид облака — у architect.

## Когда можно запускать

Только после явной команды владельца продукта / `clinic-architect`: **«запускай»**.

До этого:

1. Просмотреть брифы и [`PROMPTS.md`](PROMPTS.md)
2. При необходимости поправить ТЗ
3. Подготовить worktree/ветки:
   - `agent/clinic-protocol/sprint1-verdict`
   - `agent/clinic-ui/sprint1-verdict-panel`
   - `agent/clinic-qa/sprint1-demo-patients`

## Порядок

1. Запустить `clinic-protocol` (контракт + реестр).
2. После готовности verdict (или параллельно UI по контракту) — `clinic-ui`.
3. `clinic-qa` — сиды и чеклист (может идти параллельно с UI, если не ждёт текстов вердикта).
4. Merge: **protocol → ui → qa** (интегрирует `clinic-architect`).

## После команды «запускай»

Запущено (изолированные worktree, без коммитов воркеров):

| Агент | Ветка | ID |
|---|---|---|
| clinic-protocol | `agent/clinic-protocol/sprint1-verdict` | `8df4de57-200a-4343-80a6-b230a60935f7` |
| clinic-ui | `agent/clinic-ui/sprint1-verdict-panel` | `0e52b929-ee00-4896-8644-0e1926525a36` |
| clinic-qa | `agent/clinic-qa/sprint1-demo-patients` | `f5621eaa-f67a-4909-9643-5514db450781` |

База для worktree: коммит WIP на `master` (`chore: WIP base for sprint-1 parallel agents`).

Merge после готовности: **protocol → ui → qa** (делает `clinic-architect`).
