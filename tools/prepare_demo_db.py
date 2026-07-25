#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Подготовить БД к демо: только ДемоА/Б/В + каталог препаратов + cap_cache.

Использование:
  DATABASE_URL=... python3 tools/prepare_demo_db.py          # облако
  CLINIC_DB=clinic-qg-run.db python3 tools/prepare_demo_db.py  # локальный sqlite
"""
from __future__ import annotations

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# Явный sqlite-файл: не трогаем облако.
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
    print(f"backend={db.backend()} patients_before={len(patients)}")
    for p in list(patients):
        fs.delete_patient(p["id"])
    fs.clear_pid_cache()

    from _seed_data import seed_all

    # seed_all пропускает, если есть пациенты — мы уже очистили
    seed_all()

    # каталог АБТ
    try:
        import subprocess

        env = os.environ.copy()
        subprocess.check_call(
            [sys.executable, os.path.join(REPO, "tools", "seed_drug_catalog.py")],
            cwd=REPO,
            env=env,
        )
    except Exception as e:
        print("WARN drug catalog:", e)

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
