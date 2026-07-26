#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Прогревает cap_cache: все applicable протоколы → primary на пациента,
чтобы дашборд не делал N+1 evaluate_*.

Запуск:
  DATABASE_URL=... PYTHONPATH=. python3 tools/warm_cache.py
"""
import fhir_store as fs
import protocol_dispatch as pdisp


def main():
    fs.init_db()
    pats = fs.get_all_patients()
    n = 0
    for p in pats:
        items = pdisp.refresh_protocol_cache(p["id"])
        cache = fs.get_cap_cache(p["id"]) or {}
        ids = [i.get("protocol_id") for i in items]
        print(
            f"  {p['family']} {p['given']}: "
            f"protocols={ids or ['—']} "
            f"primary={cache.get('protocol_id') or '—'} "
            f"applicable={cache.get('applicable')} "
            f"compliant={cache.get('compliant')} "
            f"next={(cache.get('next_step') or '')[:50]!r}"
        )
        n += 1
    print(f"Прогрето кэшей: {n}")


if __name__ == "__main__":
    main()
