#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Подготовить БД к демо: 10 пациентов (seed_ten) + каталог ЛС + cap_cache.

Использование:
  DATABASE_URL=... python3 tools/prepare_demo_db.py            # облако
  CLINIC_DB=clinic-qg-run.db python3 tools/prepare_demo_db.py  # локальный sqlite
"""
from __future__ import annotations

import os
import runpy
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main() -> int:
    # Тот же вход, что и ручной seed_ten (очистка + 10 сценариев + drugs + warm).
    path = os.path.join(REPO, "tools", "seed_ten.py")
    argv = sys.argv[:]
    sys.argv = ["seed_ten.py"]
    try:
        runpy.run_path(path, run_name="__main__")
    finally:
        sys.argv = argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
