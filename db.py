"""
Сервис подключения к БД — единственный модуль, работающий с драйвером БД.

Все остальные слои (fhir_store, rules_engine, cds_service) ходят в базу
только через функции этого модуля. Это даёт две вещи:

1. Переключение хранилища одной переменной окружения:
   - DATABASE_URL задана → работаем с Postgres (Supabase).
   - не задана → локальный SQLite-файл (для разработки/демо без сети).

2. Единый интерфейс fetchone / fetchall / execute с плейсхолдерами %s.
   В SQL пишем %s (стиль Postgres); для SQLite адаптер переводит %s → ?.

Постоянное хранилище для прод-сценария — Supabase Postgres (бесплатно 500 МБ,
управляемые бэкапы). SQLite оставлен только как локальный fallback.

Важная деталь производительности: подключение к облачному пулеру Supabase
(Session pooler, Канада) — это TCP+TLS+авторизация на каждое соединение,
~1–3 с. Поэтому для Postgres мы переиспользуем ОДНО постоянное соединение
на процесс, а не открываем новое на каждый запрос. Для однопользовательского
демо/прототипа этого достаточно.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "clinic.db")

_SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "schema.sql")

# Постоянное соединение к Postgres (переиспользуется между вызовами).
_pg_conn = None


def backend() -> str:
    """Какое хранилище сейчас используется: 'postgres' или 'sqlite'."""
    return "postgres" if os.getenv("DATABASE_URL") else "sqlite"


def _get_conn():
    """Возвращает соединение. Для Postgres — переиспользуемое singleton."""
    if backend() == "postgres":
        global _pg_conn
        import psycopg2  # зависимость нужна только для прод-режима
        from psycopg2.extras import RealDictCursor
        if _pg_conn is None or _pg_conn.closed:
            _pg_conn = psycopg2.connect(
                os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor
            )
            _pg_conn.autocommit = False
        return _pg_conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _release(conn):
    """Закрываем только локальные SQLite-соединения. Postgres — держим открытым."""
    if backend() == "sqlite":
        conn.close()


def _adapt_sql(sql: str) -> str:
    """Приводит плейсхолдеры к формату текущего драйвера."""
    if backend() == "sqlite":
        # SQLite: %s → ? , а %% (экранирование psycopg2) → одиночный %
        return sql.replace("%s", "?").replace("%%", "%")
    return sql


def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_adapt_sql(sql), params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    except Exception:
        if backend() == "postgres":
            conn.rollback()
        raise
    finally:
        _release(conn)


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_adapt_sql(sql), params)
        r = cur.fetchone()
        return dict(r) if r else None
    except Exception:
        if backend() == "postgres":
            conn.rollback()
        raise
    finally:
        _release(conn)


def execute(sql: str, params: tuple = ()):
    """INSERT/UPDATE/DELETE. Возвращает количество изменённых строк."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_adapt_sql(sql), params)
        conn.commit()
        return cur.rowcount
    except Exception:
        if backend() == "postgres":
            conn.rollback()
        raise
    finally:
        _release(conn)


def init_schema():
    """Создаёт таблицы, если их нет. Идемпотентно — безопасно звать при каждом запуске."""
    with open(_SCHEMA_FILE, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = _get_conn()
    try:
        cur = conn.cursor()
        if backend() == "sqlite":
            cur.executescript(schema_sql)
        else:
            cur.execute(schema_sql)
        conn.commit()
    finally:
        _release(conn)
