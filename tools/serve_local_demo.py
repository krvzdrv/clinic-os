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

    # Пустой каталог = «кнопки назначений не работают» — дозаполняем в эту же БД.
    if len(fs.get_drug_catalog()) < 10:
        os.environ["CLINIC_DB"] = db.DB_PATH
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "seed_drug_catalog", os.path.join(REPO, "tools", "seed_drug_catalog.py")
        )
        mod = importlib.util.module_from_spec(spec)
        argv = sys.argv[:]
        sys.argv = ["seed_drug_catalog.py"]
        try:
            spec.loader.exec_module(mod)
            mod.main()
        finally:
            sys.argv = argv

    if not fs.get_all_patients():
        from _seed_data import seed_all

        seed_all()

    for p in fs.get_all_patients():
        fs.save_cap_cache(p["id"], pcap.evaluate_cap(p["id"]))

    port = int(os.environ.get("PORT", "5578"))
    print(
        f"SQLite={db.DB_PATH} backend={db.backend()} "
        f"patients={len(fs.get_all_patients())} "
        f"drugs={len(fs.get_drug_catalog())} "
        f"families={[p['family'] for p in fs.get_all_patients()]}"
    )
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
