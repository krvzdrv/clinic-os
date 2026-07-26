#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живой аудит UI: дашборд + 3 демо (read-only) + формы на throwaway."""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5578"
ATC_RE = re.compile(r"\bJ\d{2}[A-Z]{2}\d{2}\b")
GAP_RE = re.compile(
    r"\b(not_first_line_abt|missing_cbc|no_abt|hospitalization_indicated|"
    r"icu_indicated|cxr_indicated)\b"
)
FAIL = 0


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoFollow())
)


def get(path):
    r = urllib.request.urlopen(BASE + path, timeout=20)
    return r.status, r.read().decode("utf-8", "replace")


def post(path, data):
    body = urllib.parse.urlencode(data or {}).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, r.headers.get("Location", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", "") if e.headers else "", e.read().decode(
            "utf-8", "replace"
        )


def check(cond, msg):
    global FAIL
    print(f"  {'OK   ' if cond else 'FAIL '} {msg}")
    if not cond:
        FAIL += 1


def verdict(html):
    m = re.search(r'<section id="now-action"[^>]*>.*?</section>', html, re.S)
    return m.group(0) if m else ""


def verdict_text(html_block: str) -> str:
    """Текст вердикта без <form> (ATC в option value допустим)."""
    return html_block.split("<form")[0] if "<form" in html_block else html_block


def pid_of(dash, name):
    m = re.search(rf'href="/patient/(p-[a-f0-9]+)">\s*{name}\b', dash)
    return m.group(1) if m else None


def main():
    print(f"LIVE UI AUDIT → {BASE}")
    st, dash = get("/")
    check(st == 200, "dashboard")
    check("Пациенты" in dash, "заголовок списка")
    check("Сделать сейчас" in dash, "dashboard колонка аудита")
    for name in ("Орлов", "Соколов", "Морозов"):
        check(name in dash, f"dashboard has {name}")

    try:
        urllib.request.urlopen(BASE + "/demo", timeout=20)
        check(False, "/demo should redirect")
    except urllib.error.HTTPError as e:
        check(e.code in (302, 303), f"/demo → {e.code}")
        loc = e.headers.get("Location", "")
        check("/patient/" in loc, f"/demo Location={loc}")

    pids = {n: pid_of(dash, n) for n in ("Орлов", "Соколов", "Морозов")}
    check(all(pids.values()), f"pids={pids}")

    st, a = get(f"/patient/{pids['Орлов']}")
    va = verdict(a)
    ta = verdict_text(va)
    check("verdict-ok" in va or "Соответствует" in va, "Орлов соответствует")
    check("не соответствует" not in va.lower(), "Орлов без warn headline")
    check("Назначить Амоксициллин" not in ta, "Орлов next_step не «назначить АБТ»")
    check(not ATC_RE.search(ta), "Орлов verdict no ATC")
    check(not GAP_RE.search(ta), "Орлов verdict no gap")
    check("К назначениям" not in va, "Орлов без CTA на fix")

    st, b = get(f"/patient/{pids['Соколов']}")
    vb = verdict(b)
    tb = verdict_text(vb)
    check(
        "Антибиотик не соответствует" in vb or "Есть отклонения" in vb,
        "Соколов клинический headline",
    )
    check("Азитромицин" in b or "азитромицин" in b.lower(), "Соколов видит азитромицин")
    check("клавулан" not in tb.lower(), "Соколов не амокс/клав")
    check('id="now-action"' in b, "один экран #now-action")
    check('id="med-code-now"' in b, "форма назначений в #now-action")
    check('name="replace_abt"' in b, "replace_abt (одна кнопка замены)")
    check("Сейчас:" in b and "Азитромицин" in b, "виден текущий неверный АБТ")
    check("Заменить на Амоксициллин" in b or "Заменить АБТ" in b, "кнопка замены АБТ")
    check(
        re.search(r'value="J01CA04"[^>]*selected', b) is not None,
        "Амоксициллин предвыбран",
    )
    check("ЧД 32" not in verdict_text(vb) and "лейкоцит" not in verdict_text(vb).lower(),
          "в подсказке нет стены виталов")
    check("Здесь · без вкладок" not in b, "нет дев-подписи «без вкладок»")
    check("К назначениям" not in tb, "нет лишнего CTA (форма уже на экране)")
    check(not ATC_RE.search(tb), "Соколов verdict no ATC")
    check(not GAP_RE.search(tb), "Соколов verdict no gap")
    # Каталог в форме назначений
    med_opts = re.findall(
        r'<select name="code" id="med-code-now"[^>]*>(.*?)</select>', b, re.S
    )
    med_html = med_opts[0] if med_opts else ""
    check("J01CA04" in med_html and "Амоксициллин" in med_html, "в форме есть Амоксициллин")
    check("J01FA10" in med_html and "Азитромицин" in med_html, "в форме есть Азитромицин")
    check(med_html.count("<option") >= 10, f"достаточно препаратов в select ({med_html.count('<option')})")

    st, api_b = get(f"/api/protocol-cap/{pids['Соколов']}")
    data_b = json.loads(api_b)
    check(data_b.get("compliant") is False, "api Соколов compliant=False")
    gap_codes = [g.get("code") for g in data_b.get("gaps") or []]
    check("not_first_line_abt" in gap_codes, f"api gap ABT: {gap_codes}")

    st, c = get(f"/patient/{pids['Морозов']}")
    vc = verdict(c)
    tc = verdict_text(vc)
    check(
        "Показана госпитализация" in vc
        or "Показан перевод в ОРИТ" in vc
        or "Есть отклонения" in vc,
        "Морозов клинический headline",
    )
    check("госпитал" in vc.lower() or "орит" in vc.lower(), "Морозов госпитализация")
    check(
        "цефтриаксон" in vc.lower()
        or "цефалоспорин" in vc.lower()
        or "антибиот" in vc.lower()
        or "орит" in vc.lower(),
        "Морозов клиническая подсказка",
    )
    check("Госпитализировать" in vc, "Морозов Госпитализировать в #now-action")
    check("Госпитализировать в ОРИТ" in vc, "Морозов CTA совпадает с ОРИТ")
    check("cds-critical" in vc or "Критично" in vc, "Морозов tier критично")
    check("ЧД 32" not in tc and "лейкоцит" not in tc.lower(), "Морозов без стены виталов в CDS")
    check("Здесь · без вкладок" not in c and "Сейчас по протоколу" not in c,
          "Морозов без старого шумного UI")
    check(not ATC_RE.search(tc), "Морозов verdict no ATC")
    check(not GAP_RE.search(tc), "Морозов verdict no gap")

    # Write tests on throwaway — demos untouched
    st, loc, _ = post("/patient/new", {
        "family": "AuditТест",
        "given": "Формы",
        "patronymic": "",
        "gender": "male",
        "birth_date": "1980-01-01",
    })
    m = re.search(r"/patient/(p-[a-f0-9]+)", loc or "")
    pid = m.group(1) if m else ""
    check(bool(pid), f"throwaway pid={pid}")
    if pid:
        st, card = get(f"/patient/{pid}")
        eid_m = re.search(r'name="encounter_id"[^>]*value="(e-[a-f0-9]+)"', card)
        eid = eid_m.group(1) if eid_m else ""
        if not eid:
            post(f"/patient/{pid}/encounter", {
                "class": "ambulatory", "date": "2026-07-25", "reason": "audit",
            })
            st, card = get(f"/patient/{pid}")
            eid_m = re.search(r'value="(e-[a-f0-9]+)"', card)
            eid = eid_m.group(1) if eid_m else ""
        check(bool(eid), f"throwaway eid={eid}")
        marker = "LIVE-AUDIT-ANAM"
        st, _, _ = post(f"/patient/{pid}/anamnesis", {"encounter_id": eid, "text": marker})
        check(st in (302, 303), f"anamnesis {st}")
        st, after = get(f"/patient/{pid}")
        check(marker in after, "anamnesis persisted")
        st, _, _ = post(f"/patient/{pid}/observation", {
            "encounter_id": eid, "code": "8867-4", "value_numeric": "77", "date": "2026-07-25",
        })
        st, after = get(f"/patient/{pid}")
        check("77" in after, "observation persisted")
        body_enc = urllib.parse.urlencode({
            "encounter_id": eid, "code": "8310-5", "value_numeric": "36.8", "date": "2026-07-25",
        }).encode()
        req = urllib.request.Request(
            BASE + f"/patient/{pid}/observation", data=body_enc, method="POST",
            headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        )
        try:
            r = urllib.request.urlopen(req, timeout=20)
            ajax = json.loads(r.read().decode())
            check(ajax.get("ok") is True, "AJAX observation ok")
        except Exception as e:
            check(False, f"AJAX observation: {e}")

        post(f"/patient/{pid}/delete", {})
        st, dash2 = get("/")
        check("AuditТест" not in dash2, "throwaway cleaned")

    # demos still intact after write tests
    st, dash3 = get("/")
    check("Соколов" in dash3 and "Орлов" in dash3 and "Морозов" in dash3, "demos intact")
    st, b_final = get(f"/patient/{pids['Соколов']}")
    check("Азитромицин" in b_final, "Соколов still has wrong ABT for guest")
    vf = verdict(b_final)
    check(
        "verdict-warn" in vf
        or "не соответствует" in vf.lower()
        or "Есть отклонения" in vf,
        "Соколов still non-compliant",
    )

    print(f"\nИТОГ live_ui_audit: {'PASS' if FAIL == 0 else f'{FAIL} FAIL'}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
