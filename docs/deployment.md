# Деплой на PythonAnywhere (бесплатно, 15 минут)

## Что получите

Рабочий сайт по адресу `https://твоё-имя.pythonanywhere.com` с HTTPS,
дашбордом качества, картами пациентов и CDS-подсказками. 0 ₽.

## Шаг 1. Регистрация

1. Открой https://www.pythonanywhere.com/registration/register/beginner/
2. Зарегистрируйся (бесплатный тариф "Beginner").
3. Придумай username — он станет частью URL: `username.pythonanywhere.com`.

## Шаг 2. Загрузка кода

### Вариант А: через Git (рекомендуется)

1. Залей репозиторий на GitHub (приватный — бесплатно).
2. На PythonAnywhere открой **Dashboard → Files**.
3. Открой **Consoles → Bash**.
4. Выполни:
```bash
git clone https://github.com/твой-логин/hypertension-cds-demo.git ~/hypertension-cds-demo
```

### Вариант Б: через загрузку файлов

1. На PythonAnywhere открой **Dashboard → Files**.
2. Загрузи все файлы проекта в папку `~/hypertension-cds-demo/`.

## Шаг 3. Установка зависимостей

В **Consoles → Bash** выполни:
```bash
cd ~/hypertension-cds-demo
pip3 install --user -r requirements.txt
```

## Шаг 4. Создание веб-приложения

1. Открой **Dashboard → Web**.
2. Нажми **Add a new web app**.
3. Выбери **Manual configuration** (в самом низу).
4. Выбери **Python 3.10** (или новее, что доступно).
5. Нажми **Next**.

## Шаг 5. Настройка WSGI

1. На странице **Web** найди секцию **Code** → **WSGI configuration file**.
2. Открой файл (ссылка вида `/var/www/твойлогин_pythonanywhere_com_wsgi.py`).
3. **Удали всё содержимое** и вставь:
```python
import sys
import os

project_home = '/home/твойлогин/hypertension-cds-demo'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

from wsgi import application
```
4. Замени `твойлогин` на свой username.
5. Сохрани (кнопка вверху справа).

## Шаг 6. Настройка путей

На странице **Web** в секции **Code**:
- **Source code:** `/home/твойлогин/hypertension-cds-demo`
- **Working directory:** `/home/твойлогин/hypertension-cds-demo`

## Шаг 7. Перезапуск

На странице **Web** нажми зелёную кнопку **Reload**.

## Шаг 8. Проверка

Открой `https://твойлогин.pythonanywhere.com` в браузере.
Должен появиться дашборд с 10 тестовыми пациентами.

## Бэкап данных

SQLite-файл `clinic.db` лежит в папке проекта. На бесплатном тарифе
он **постоянный** (не сбрасывается). Но для надёжности — настрой копирование:

В **Consoles → Bash** создай скрипт `~/backup.sh`:
```bash
#!/bin/bash
cp ~/hypertension-cds-demo/clinic.db ~/bp_backup_$(date +%Y%m%d).db
# Храним последние 7 бэкапов
ls -t ~/bp_backup_*.db | tail -n +8 | xargs rm -f
```

Добавь в **Dashboard → Tasks** (бесплатно — 1 задача в день):
- Команда: `bash ~/backup.sh`
- Частота: раз в день.

## Ограничения бесплатного тарифа

- 100 CPU-секунд в день (хватит на ~50–100 визитов).
- 512 МБ диска (хватит на тысячи пациентов).
- Нельзя ходить во внешние интернеты из кода (нам не нужно).
- Один веб-приложение.

Если упрёшься в лимиты — платный тариф от $5/мес, но для MVP бесплатного достаточно.

## Если что-то не работает

1. Открой **Dashboard → Web** → **Error log** (ссылка вверху страницы).
2. Последние ошибки — там.
3. Частые проблемы:
   - **ImportError** — не выполнен `pip3 install --user -r requirements.txt`.
   - **500 Internal Server Error** — ошибка в WSGI-файле, проверь путь к проекту.
   - **Template not found** — неправильно указан Source code в Web.
