#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Прогревает cap_cache: считает CAP-оценку для каждого пациента один раз,
чтобы дашборд не делал N+1 запросов к БД.

Запуск:
  DATABASE_URL=... PYTHONPATH=. python3 tools/warm_cache.py
"""
import fhir_store as fs
import protocol_cap as pcap


def main():
    fs.init_db()
    pats = fs.get_all_patients()
    n = 0
    for p in pats:
        verdict = pcap.evaluate_cap(p["id"])
        fs.save_cap_cache(p["id"], verdict)
        n += 1
        print(f"  {p['family']} {p['given']}: "
              f"applicable={verdict.get('applicable')} "
              f"severity={verdict.get('severity')} "
              f"compliant={verdict.get('compliant')}")
    print(f"Прогрето кэшей: {n}")


if __name__ == "__main__":
    main()
