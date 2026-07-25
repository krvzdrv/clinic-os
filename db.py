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


def _reset_pg_conn():
    """Сбрасывает singleton Postgres-соединения (оно могло быть закрыто пулером Supabase)."""
    global _pg_conn
    if _pg_conn is not None and not _pg_conn.closed:
        try:
            _pg_conn.close()
        except Exception:
            pass
    _pg_conn = None


def _is_conn_error(exc) -> bool:
    """True, если ошибка связана с разрывом соединения (надо пересоздать и повторить)."""
    name = type(exc).__name__
    return name in ("InterfaceError", "OperationalError", "ConnectionDone", "ConnectionFailure")


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
    for attempt in (1, 2):
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(_adapt_sql(sql), params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            if backend() == "postgres":
                try:
                    conn.rollback()
                except Exception:
                    pass
                if attempt == 1 and _is_conn_error(e):
                    _reset_pg_conn()
                    continue
            raise
        finally:
            _release(conn)


def fetchone(sql: str, params: tuple = ()) -> dict | None:
    for attempt in (1, 2):
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(_adapt_sql(sql), params)
            r = cur.fetchone()
            return dict(r) if r else None
        except Exception as e:
            if backend() == "postgres":
                try:
                    conn.rollback()
                except Exception:
                    pass
                if attempt == 1 and _is_conn_error(e):
                    _reset_pg_conn()
                    continue
            raise
        finally:
            _release(conn)


def execute(sql: str, params: tuple = ()):
    """INSERT/UPDATE/DELETE. Возвращает количество изменённых строк."""
    for attempt in (1, 2):
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(_adapt_sql(sql), params)
            conn.commit()
            return cur.rowcount
        except Exception as e:
            if backend() == "postgres":
                try:
                    conn.rollback()
                except Exception:
                    pass
                if attempt == 1 and _is_conn_error(e):
                    _reset_pg_conn()
                    continue
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

    # Аддитивные миграции для уже существующих БД (новые колонки добавляем, если их нет).
    # На свежей БД CREATE TABLE уже создаст их; здесь — для ранее развёрнутых схем.
    _ensure_column("allergy_intolerance", "reaction_type", "TEXT")
    _ensure_column("medication_request", "route", "TEXT")
    _ensure_column("medication_request", "dose_per_day", "NUMERIC")
    # drug_catalog: поля протокола взрослых (КП №768).
    for col, coltype in (
        ("generic_name", "TEXT"), ("dosage_form", "TEXT"), ("interactions", "TEXT"),
        ("pregnancy", "TEXT"), ("dosage_text", "TEXT"),
        ("dose_note", "TEXT"), ("frequency", "TEXT"),
        ("max_daily_mg", "REAL"), ("protocol_ref", "TEXT"), ("note", "TEXT"),
        ("category", "TEXT"), ("verify_flag", "INTEGER DEFAULT 0"),
    ):
        _ensure_column("drug_catalog", col, coltype)


def _ensure_column(table, column, coltype):
    """Добавляет колонку, если её нет. Идемпотентно для Postgres и SQLite."""
    if _has_column(table, column):
        return
    db_execute_ddl(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def _has_column(table, column):
    if backend() == "sqlite":
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            return any(row[1] == column for row in cur.fetchall())
        finally:
            _release(conn)
    row = fetchone(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
        (table, column),
    )
    return row is not None


def db_execute_ddl(ddl):
    """Исполняет DDL без параметров (для миграций). Не возвращает строк."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(ddl)
        conn.commit()
    finally:
        _release(conn)
