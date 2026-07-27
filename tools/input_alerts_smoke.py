#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke: ввод всех основных видов данных + алерты нарушения протокола в UI/API."""
from __future__ import annotations

import os
import re
import sys
import tempfile

os.environ.pop("DATABASE_URL", None)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import db  # noqa: E402

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
db.DB_PATH = _TMP.name

import fhir_store as fs  # noqa: E402
from app import app  # noqa: E402

fs.init_db()
PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK    {msg}")
    else:
        FAIL += 1
        print(f"  FAIL  {msg}")


def ajax(client, path, data):
    return client.post(
        path,
        data=data,
        headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
    )


def main():
    print("=" * 70)
    print("INPUT + PROTOCOL ALERTS SMOKE")
    print("=" * 70)
    client = app.test_client()

    r = client.post(
        "/patient/new",
        data={
            "family": "Смоук",
            "given": "Тест",
            "patronymic": "Протоколович",
            "gender": "male",
            "birth_date": "1980-01-15",
        },
        follow_redirects=False,
    )
    loc = r.headers.get("Location") or ""
    m = re.search(r"/patient/(p-[a-f0-9]+)", loc)
    ok(bool(m), f"создан пациент ({loc})")
    if not m:
        return
    pid = m.group(1)

    r = ajax(
        client,
        f"/patient/{pid}/encounter",
        {"class": "ambulatory", "start": "2026-07-27", "complaint": "Кашель 3 дня"},
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True or r.status_code in (200, 302), f"приём+жалоба ({j or r.status_code})")
    encs = fs.get_encounters(pid)
    ok(bool(encs), "encounter в БД")
    eid = encs[0]["id"] if encs else None

    r = ajax(client, f"/patient/{pid}/anamnesis", {"encounter_id": eid, "text": "Лихорадка, слабость"})
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"анамнез ({j})")

    r = ajax(
        client,
        f"/patient/{pid}/observation",
        {"encounter_id": eid, "code": "8310-5", "value_numeric": "38.5", "date": "2026-07-27"},
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"витал t=38.5 ({j})")

    r = ajax(
        client,
        f"/patient/{pid}/observation",
        {"encounter_id": eid, "code": "59408-5", "value_numeric": "88", "date": "2026-07-27"},
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"витал SpO2=88 ({j})")

    r = ajax(client, f"/patient/{pid}/flag", {"encounter_id": eid, "key": "local_signs"})
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"клинический признак local_signs ({j})")

    r = ajax(
        client,
        f"/patient/{pid}/general_condition",
        {"encounter_id": eid, "key": "moderate"},
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"общее состояние ({j})")

    r = ajax(
        client,
        f"/patient/{pid}/condition",
        {"encounter_id": eid, "code": "J18.9", "display": "Пневмония неуточненная", "onset_date": "2026-07-27"},
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"диагноз J18.9 ({j})")

    r = ajax(
        client,
        f"/patient/{pid}/observation",
        {"encounter_id": eid, "code": "6690-2", "value_numeric": "14", "date": "2026-07-27"},
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"лаб WBC ({j})")

    r = ajax(
        client,
        f"/patient/{pid}/report",
        {"encounter_id": eid, "code": "CXR", "date": "2026-07-27"},
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"инструментальный CXR ({j})")

    # Неверный АБТ → soft/hard CDS
    r = ajax(
        client,
        f"/patient/{pid}/medication",
        {
            "encounter_id": eid,
            "code": "J01FA10",
            "display": "Азитромицин",
            "dose": "500 мг",
            "frequency": "1 раз в сутки",
            "route": "oral",
            "med_date": "2026-07-27",
            "period_end": "2026-08-01",
        },
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("need_confirm") is True, f"алерт CDS need_confirm на неверный АБТ ({j.get('level')}, {j.get('cds', [{}])[0].get('message', '')[:80]})")
    ok(
        any("протоколу" in (c.get("message") or "").lower() or "ожидается" in (c.get("message") or "").lower()
            for c in (j.get("cds") or [])),
        "текст алерта про протокол/ожидание",
    )

    # Force-save with confirm to see page verdict
    r = ajax(
        client,
        f"/patient/{pid}/medication",
        {
            "encounter_id": eid,
            "code": "J01FA10",
            "display": "Азитромицин",
            "dose": "500 мг",
            "frequency": "1 раз в сутки",
            "route": "oral",
            "med_date": "2026-07-27",
            "period_end": "2026-08-01",
            "confirm": "1",
            "ack": "1",
            "override_reason": "smoke test",
        },
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is True, f"АБТ сохранён после confirm ({j})")

    html = client.get(f"/patient/{pid}").data.decode("utf-8", "replace")
    ok("Клинические признаки" in html, "UI: заголовок «Клинические признаки»")
    ok("Данные физического обследования" not in html, "UI: нет старого заголовка осмотра")
    ok("Влажные хрипы" in html, "UI: короткая подпись флага")
    ok("now-action" in html or "verdict-panel" in html, "UI: блок вердикта/CDS")
    ok(
        ("Не соответствует" in html) or ("Нужно действие" in html) or ("not_first_line" in html)
        or ("Амоксициллин" in html) or ("протоколу" in html.lower()),
        "UI: видимый алерт/подсказка нарушения протокола",
    )
    ok('optgroup label="Локальные"' in html or "Локальные" in html, "UI: группа Локальные")
    ok("_fitDictionaryControls" in html and "keepOpen" in html, "UI: fit + keepOpen в JS")

    # Абсурдный витал → ошибка валидации
    r = ajax(
        client,
        f"/patient/{pid}/observation",
        {"encounter_id": eid, "code": "8310-5", "value_numeric": "100", "date": "2026-07-27"},
    )
    j = r.get_json(silent=True) or {}
    ok(j.get("ok") is False and ("Допустимо" in str(j) or j.get("error")), f"валидация t=100 ({j})")

    print("=" * 70)
    print(f"ИТОГ: {PASS} ok, {FAIL} fail")
    print("=" * 70)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
