#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP-аудит 10 пациентов + путь врача (один экран #now-action).

  python3 tools/audit_ten.py http://127.0.0.1:5592
"""
from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5578"
ATC_RE = re.compile(r"\bJ\d{2}[A-Z]{2}\d{2}\b")
GAP_RE = re.compile(
    r"\b(not_first_line_abt|missing_cbc|no_abt|hospitalization_indicated|"
    r"icu_indicated|abt_no_effect|bronchodilator_not_indicated|"
    r"course_too_short|diagnosis_unsupported)\b"
)

EXPECT = {
    "Орлов": {"ok": True, "must": ["Соответствует"], "forbid": ["К назначениям"]},
    "Соколов": {
        "ok": False,
        "must": ["не соответствует", "амоксициллин"],
        "forbid": ["К назначениям"],
        "now": "med",
        "suggest": "J01CA04",
    },
    "Морозов": {
        "ok": False,
        "must": ["ОРИТ", "Госпитализировать в ОРИТ"],
        "forbid": ["К госпитализации", "Здесь · без вкладок", "Сейчас по протоколу"],
        "now": "actions",
    },
    "Стационаров": {"ok": True, "must": ["Соответствует"]},
    "Клавуланова": {"ok": False, "must": ["не соответствует", "клавулан"], "now": "med"},
    "Аллергова": {"ok": False, "must": ["не соответствует", "макролид"], "now": "med"},
    "Аспиратов": {"ok": True, "must": ["Соответствует"]},
    "Бронхов": {"ok": False, "must": ["не соответствует"], "now": "med"},
    "Контролёв": {
        "ok": False,
        "must": ["госпитал", "Госпитализировать"],
        "forbid": ["К госпитализации"],
        "now": "actions",
    },
    "Пустова": {
        "ok": False,
        "must": ["Диагноз", "Поставить диагноз"],
        "forbid": ["К диагнозу"],
        "now": "cond",
    },
}


class _NoFollow(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoFollow())
)

FAIL = 0


def check(cond: bool, msg: str) -> None:
    global FAIL
    print(f"  {'OK   ' if cond else 'FAIL '} {msg}")
    if not cond:
        FAIL += 1


def get(path: str) -> str:
    r = urllib.request.urlopen(BASE + path, timeout=30)
    return r.read().decode("utf-8", "replace")


def main() -> int:
    print(f"AUDIT TEN → {BASE}")
    dash = get("/")
    check("Сделать сейчас" in dash, "дашборд: колонка аудита")
    check("С чего начать" in dash, "гостевой баннер")

    pids = re.findall(r'href="/patient/(p-[a-f0-9]+)">\s*([^<\n]+)', dash)
    seen: dict[str, str] = {}
    order: list[str] = []
    for pid, name in pids:
        if pid not in seen:
            seen[pid] = name.strip()
            order.append(pid)
    check(len(order) >= 10, f"пациентов на дашборде: {len(order)}")

    found: set[str] = set()
    for pid in order:
        html = get(f"/patient/{pid}")
        body = html.split("</style>", 1)[-1]
        now_m = re.search(
            r'<section id="now-action"[^>]*>.*?</section>', html, re.S
        )
        now = now_m.group(0) if now_m else ""
        v = now  # один экран = вердикт + действие
        nm = re.search(r"<h1[^>]*>\s*([^<]+)", html)
        full = (nm.group(1).strip() if nm else seen[pid])
        family = full.split()[0]
        found.add(family)
        hl = re.search(r'class="verdict-headline"[^>]*>([^<]+)', v)
        why = re.search(r'class="verdict-why"[^>]*>([^<]+)', v)
        print(
            f"\n{family:14} | {(hl.group(1) if hl else '?'):40.40} | "
            f"{(why.group(1) if why else '?'):48.48}"
        )
        check(bool(now), f"{family}: #now-action")
        check("verdict-panel" in now, f"{family}: verdict-panel")
        # ATC в value= формы допустим — в видимом тексте вердикта нет.
        text_before_form = v.split("<form")[0] if "<form" in v else v
        check(not ATC_RE.search(text_before_form), f"{family}: нет ATC в тексте вердикта")
        check(not GAP_RE.search(text_before_form), f"{family}: нет gap-кодов в тексте вердикта")
        pos_now = body.find('id="now-action"')
        pos_hist = body.find("history-fold")
        check(0 <= pos_now < pos_hist, f"{family}: now-action выше истории")
        hist_m = re.search(r'<details class="history-fold"([^>]*)>', html)
        hist_open = bool(hist_m and "open" in (hist_m.group(1) or ""))
        exp = EXPECT.get(family)
        if not exp:
            check(False, f"{family}: неожиданный пациент")
            continue
        blob = (v + " " + html).lower()
        for s in exp["must"]:
            check(s.lower() in blob, f"{family}: есть «{s}»")
        for s in exp.get("forbid") or []:
            check(s not in v, f"{family}: нет «{s}»")
        if exp["ok"]:
            check("verdict-ok" in v, f"{family}: verdict-ok")
            check(hist_open, f"{family}: история открыта")
        else:
            check("verdict-warn" in v, f"{family}: verdict-warn")
            check(not hist_open, f"{family}: история свёрнута")
            kind = exp.get("now")
            if kind == "med":
                check('id="med-code-now"' in now, f"{family}: med-code-now")
                check('name="replace_abt"' in now, f"{family}: replace_abt")
                sug = exp.get("suggest")
                if sug:
                    check(
                        re.search(rf'value="{sug}"[^>]*selected', now) is not None,
                        f"{family}: {sug} предвыбран",
                    )
            elif kind == "actions":
                check("Госпитализировать" in now, f"{family}: Госпитализировать в now")
            elif kind == "cond":
                check(
                    "Поставить диагноз" in now or "МКБ" in now,
                    f"{family}: форма диагноза в now",
                )

    missing = set(EXPECT) - found
    check(not missing, f"все 10 на дашборде (нет: {missing or '—'})")

    print(f"\nИТОГ audit_ten: {'PASS' if FAIL == 0 else f'FAIL ({FAIL})'}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
