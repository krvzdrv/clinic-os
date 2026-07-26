# Деплой на Render (бесплатно, ~10 минут)

Приложение хостится на Render, данные — в Supabase Postgres.
По итогу — публичная ссылка `https://clinic-os-xxxx.onrender.com` с HTTPS,
дашбордом, картами пациентов и CDS-подсказками. 0 ₽.

## Что уже подготовлено в репозитории

- `requirements.txt` — зависимости + `gunicorn` (продакшн-WSGI).
- `Procfile` — команда старта: `gunicorn app:app -b 0.0.0.0:$PORT --workers 2`.
- `render.yaml` — Blueprint (Render сам создаст сервис по нему).
- Приложение читает `DATABASE_URL` и `PORT` из окружения.
- Схема БД создаётся автоматически при старте (`fs.init_db()`).

## Шаг 1. Залить репозиторий на GitHub

```bash
git add -A
git commit -m "Supabase backend + Render deploy config"
git push origin master
```

`.env` в `.gitignore` — он НЕ уйдёт на GitHub. Секрет задаётся в панели Render.

## Шаг 2. Регистрация на Render

1. Открой https://render.com/ → **Get Started**.
2. Зарегистрируйся через GitHub (так проще подключить репо).

## Шаг 3. Создать Web Service

1. **New +** → **Blueprint** (если в корне репо есть `render.yaml`) — Render сам
   предложит создать сервис из `render.yaml`. Либо **New + → Web Service** вручную.
2. Выбери репозиторий `clinic-os`.
3. Если вручную (без Blueprint), укажи:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app -b 0.0.0.0:${PORT:-5000} --workers 2 --access-logfile -`
   - **Instance Type:** Free
4. **Create Web Service.**

## Шаг 4. Задать DATABASE_URL (секрет)

1. В созданном сервисе открой **Environment**.
2. **Add Environment Variable:**
   - **Key:** `DATABASE_URL`
   - **Value:** (вставь строку подключения из Supabase — та же, что в локальном `.env`,
     формат `postgresql://...supabase.co:6543/...`)
3. **Save Changes.** Render пересоберёт и запустит сервис.

> Строка подключения Supabase: **Project Settings → Database → Connection string → URI**.
> Используй pooler-адрес (порт 6543), не прямой (5432).

## Шаг 5. Заселить демо-данные (один раз)

На Render Free нет SSH-консоли, поэтому засеваем локально — данные в Supabase
общие, Render их увидит сразу:

```bash
# локально, с DATABASE_URL из .env
DATABASE_URL=... python3 tools/seed_ten.py
# эквивалент: python3 tools/prepare_demo_db.py
# warm_cache уже вызывается внутри seed_ten
```


## Шаг 6. Проверка

Открой ссылку из Render (вверху сервиса, вида `https://clinic-os-xxxx.onrender.com`).
Должен появиться дашборд с 10 пациентами. Первые запросы могут быть медленнее
(прогрев), дальше — быстро благодаря кэшу.

## Ограничения бесплатного тарифа Render

- Сервис «засыпает» через 15 мин без обращений. Первый запрос после сна —
  ~30–50 с (холодный старт). `db.py` автоматически переподключается к Supabase
  после сна (retry-логика).
- 750 часов инстансов/мес (хватит на 1 постоянный сервис).
- 512 МБ RAM (2 воркера gunicorn помещаются).

Если хождения будут частые и холодный старт мешает — платный тариф от $7/мес
(Startter: не спит). Для демо free достаточно.

## Если что-то не работает

1. В сервисе Render открой **Logs** (вкладка).
2. Частые проблемы:
   - **Application failed to bind $PORT** — проверь Start Command (должен биндить `0.0.0.0:$PORT`).
   - **500 / OperationalError** — не задан `DATABASE_URL` или неверный формат.
   - **Медленно** — после первого открытия карты пациента оценка кэшируется
     (`cap_cache`), дашборд становится быстрым. Если совсем пусто — выполни
     `tools/warm_cache.py` (Шаг 5).
3. Локально можно отладить тот же запуск:
   ```bash
   DATABASE_URL=... PORT=5574 FLASK_DEBUG=0 gunicorn app:app -b 0.0.0.0:5574 --workers 2
   ```
