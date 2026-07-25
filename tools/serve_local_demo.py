#!/usr/bin/env python3
"""Локальный сервер на SQLite clinic-qg-run.db (игнорирует DATABASE_URL из .env)."""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.pop("DATABASE_URL", None)

# Патч до импорта app: и module, и уже связанные имена
import dotenv
dotenv.load_dotenv = lambda *a, **k: False  # noqa: E731

import db
db.DB_PATH = os.path.join(REPO, "clinic-qg-run.db")

import fhir_store as fs
fs.init_db()

from app import app

# app.py делает `from dotenv import load_dotenv` — реальный load мог уже сработать.
# Принудительно остаёмся на SQLite.
os.environ.pop("DATABASE_URL", None)
db._reset_pg_conn()
assert db.backend() == "sqlite", db.backend()


@app.before_request
def _force_sqlite():
    if os.getenv("DATABASE_URL"):
        os.environ.pop("DATABASE_URL", None)
        db._reset_pg_conn()


if __name__ == "__main__":
    import protocol_cap as pcap

    for p in fs.get_all_patients():
        fs.save_cap_cache(p["id"], pcap.evaluate_cap(p["id"]))

    port = int(os.environ.get("PORT", "5578"))
    print(
        f"SQLite={db.DB_PATH} backend={db.backend()} "
        f"patients={len(fs.get_all_patients())} "
        f"families={[p['family'] for p in fs.get_all_patients()]}"
    )
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
