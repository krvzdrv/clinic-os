#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Подготовить БД к демо: только ДемоА/Б/В + каталог препаратов + cap_cache.

Использование:
  DATABASE_URL=... python3 tools/prepare_demo_db.py            # облако
  CLINIC_DB=clinic-qg-run.db python3 tools/prepare_demo_db.py  # локальный sqlite
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

clinic_db = os.environ.get("CLINIC_DB")
if clinic_db and not os.environ.get("FORCE_DATABASE_URL"):
    os.environ.pop("DATABASE_URL", None)

import db  # noqa: E402

if clinic_db and not os.environ.get("DATABASE_URL"):
    db.DB_PATH = os.path.abspath(clinic_db)

import fhir_store as fs  # noqa: E402
import protocol_cap as pcap  # noqa: E402


def main() -> int:
    print(f"target backend={db.backend()} path={getattr(db, 'DB_PATH', None)}")
    fs.init_db()
    patients = fs.get_all_patients()
    print(f"patients_before={len(patients)}")
    for p in list(patients):
        fs.delete_patient(p["id"])
    fs.clear_pid_cache()

    from _seed_data import seed_all

    seed_all()

    # Каталог в том же процессе / той же БД (не subprocess — иначе уходит в другую DB).
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "seed_drug_catalog", os.path.join(REPO, "tools", "seed_drug_catalog.py")
    )
    mod = importlib.util.module_from_spec(spec)
    # Уже привязанный db.DB_PATH должен сохраниться; модуль сида не перетрёт Postgres.
    argv = sys.argv[:]
    sys.argv = ["seed_drug_catalog.py"]
    try:
        spec.loader.exec_module(mod)
        mod.main()
    finally:
        sys.argv = argv

    n_drugs = len(fs.get_drug_catalog())
    print(f"drug_catalog={n_drugs}")
    if n_drugs < 10:
        print("FAIL: drug_catalog too small")
        return 1

    for p in fs.get_all_patients():
        cap = pcap.evaluate_cap(p["id"])
        fs.save_cap_cache(p["id"], cap)
        print(
            f"  {p['family']}: applicable={cap.get('applicable')} "
            f"compliant={cap.get('compliant')} severity={cap.get('severity')}"
        )

    print(f"OK demo ready: {[p['family'] for p in fs.get_all_patients()]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
