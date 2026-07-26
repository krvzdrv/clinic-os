#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Style gate — статические проверки текста/UI без БД и сервера (быстро, offline).

Ловит классы ошибок, которые раньше находил только пользователь глазами:
  1. Захардкоженный клинический текст в .py (description= у целей/планов и т.п.),
     написанный как предложение (точка в конце, запятая между разными фактами),
     хотя рендерится в .attr-value — там уже другое правило (см. STYLE_GUIDE §4.5):
     факты через « · », без точки.
  2. Инлайновые style="height/font-size/padding/border-radius" на кнопках
     (.btn/.btn-small/.action-btn) в шаблонах — обходят static/clinic.css
     как единственный источник размеров и снова разъезжаются по проекту.
  3. Сырой английский статус (clinical_status/verification_status и т.п.),
     напечатанный в шаблоне без перевода на русский (без .get(...)/фильтра рядом).
  4. Страница с <form method="POST"> без общей защиты от повторной отправки
     (templates/_double_submit_guard.html) — новая форма/страница не должна
     оставаться незащищённой просто потому, что про неё забыли.

Запуск (перед сдачей любой задачи, трогающей templates/**, protocol_*.py,
*_service.py, cds_service.py, terminology.py):
  python3 tools/style_gate.py

Ничего не пишет в БД, не поднимает сервер — безопасно гонять в CI/pre-commit.
"""
from __future__ import annotations

import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAIL = 0
OK = 0


def fail(msg: str) -> None:
    global FAIL
    print(f"  FAIL  {msg}")
    FAIL += 1


def ok(msg: str) -> None:
    global OK
    print(f"  OK    {msg}")
    OK += 1


def _py_files():
    for name in os.listdir(REPO):
        if name.endswith(".py") and os.path.isfile(os.path.join(REPO, name)):
            yield os.path.join(REPO, name)


def _html_files():
    tdir = os.path.join(REPO, "templates")
    for name in os.listdir(tdir):
        if name.endswith(".html"):
            yield os.path.join(tdir, name)


# ---------------------------------------------------------------------------
# 1) Захардкоженный «клинический» текст = предложение вместо фрагмента-значения
# ---------------------------------------------------------------------------

DESCRIPTION_KW_RE = re.compile(r'description\s*=\s*"([^"]*)"')
METRIC_TOKEN_RE = re.compile(r"\d+(\.\d+)?\s?(?:°C|%|мг|мл|мм|уд/мин|/мин)")


def check_attr_value_sentences():
    print("\n[1] Клинический текст в .py — фрагмент, не предложение (STYLE_GUIDE §4.5)")
    found_any = False
    for path in _py_files():
        text = open(path, encoding="utf-8").read()
        for m in DESCRIPTION_KW_RE.finditer(text):
            s = m.group(1)
            line = text[: m.start()].count("\n") + 1
            where = f"{os.path.basename(path)}:{line}"
            found_any = True
            if s.rstrip().endswith((".", "!", "?")):
                fail(f"{where}: description оканчивается точкой (значение — не предложение): {s!r}")
                continue
            metrics = METRIC_TOKEN_RE.findall(s)
            if len(metrics) >= 2 and "," in s:
                fail(f"{where}: несколько фактов через запятую, нужен « · »: {s!r}")
                continue
            ok(f"{where}: description без точки/запятой между фактами")
    if not found_any:
        ok("не найдено ни одного description= в .py (нечего проверять)")


# ---------------------------------------------------------------------------
# 2) Инлайновые размеры кнопок мимо clinic.css
# ---------------------------------------------------------------------------

BTN_TAG_RE = re.compile(r"<[a-zA-Z]+\s[^>]*>")
BTN_CLASS_RE = re.compile(r'class="[^"]*\b(btn|btn-small|action-btn)\b[^"]*"')
FORBIDDEN_STYLE_RE = re.compile(r'style="[^"]*(height\s*:|font-size\s*:|padding\s*:|border-radius\s*:)')


def check_button_inline_style():
    print("\n[2] Кнопки — размеры только из static/clinic.css, без inline-переопределений")
    any_bad = False
    for path in _html_files():
        text = open(path, encoding="utf-8").read()
        for m in BTN_TAG_RE.finditer(text):
            tag = m.group(0)
            if BTN_CLASS_RE.search(tag) and FORBIDDEN_STYLE_RE.search(tag):
                line = text[: m.start()].count("\n") + 1
                fail(f"{os.path.basename(path)}:{line}: inline style на .btn/.action-btn обходит clinic.css: {tag[:120]!r}")
                any_bad = True
    if not any_bad:
        ok("нет inline height/font-size/padding/border-radius на .btn/.btn-small/.action-btn")


# ---------------------------------------------------------------------------
# 3) Сырой enum-статус без перевода на русский
# ---------------------------------------------------------------------------

RAW_STATUS_RE = re.compile(
    r"\{\{\s*[\w\.]+\.(clinical_status|verification_status)\s*(?:or\s*'[^']*')?\s*\}\}"
)


def check_raw_status_leak():
    print("\n[3] Статус диагноза/факта — только через русский перевод, не сырым полем")
    any_bad = False
    for path in _html_files():
        text = open(path, encoding="utf-8").read()
        for m in RAW_STATUS_RE.finditer(text):
            line = text[: m.start()].count("\n") + 1
            fail(f"{os.path.basename(path)}:{line}: сырой статус напечатан без перевода: {m.group(0)!r}")
            any_bad = True
    if not any_bad:
        ok("нет прямого вывода clinical_status/verification_status без перевода")


# ---------------------------------------------------------------------------
# 4) Каждая страница с формой — под общей защитой от повторной отправки
# ---------------------------------------------------------------------------

FORM_POST_RE = re.compile(r'<form\b[^>]*method="POST"', re.I)
GUARD_INCLUDE_RE = re.compile(r'_double_submit_guard\.html')


def check_double_submit_guard_coverage():
    print("\n[4] Страницы с POST-формой подключают общий partial защиты от повторной отправки")
    any_bad = False
    for path in _html_files():
        name = os.path.basename(path)
        if name.startswith("_"):
            continue  # partial сам не форма-страница
        text = open(path, encoding="utf-8").read()
        if FORM_POST_RE.search(text) and not GUARD_INCLUDE_RE.search(text):
            fail(f"{name}: есть <form method=\"POST\">, но не подключён _double_submit_guard.html")
            any_bad = True
    if not any_bad:
        ok("все страницы с POST-формой подключают _double_submit_guard.html")


# ---------------------------------------------------------------------------
# 5) «Слипшийся» слэш между клиническими терминами — X/Y читается неоднозначно
#    (AND? OR? синоним?) и на практике расползается по написанию (было:
#    «двусторонняя/многодолевая» в одном файле и «двустороннее/многоочаговое»
#    в другом — тот же клинический флаг, два текста). Разрешено только для
#    коротких доменных аббревиатур (в/в, р/сут, мг/кг) — порог в 3+ буквы с
#    каждой стороны их не задевает.
# ---------------------------------------------------------------------------

SLASH_WORDS_RE = re.compile(r"[а-яА-ЯёЁ]{3,}/[а-яА-ЯёЁ]{3,}")
TRIPLE_QUOTE_RE = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')
STRING_LIT_RE = re.compile(r'"([^"\\]|\\.)*"')

# Только файлы, где текст в строковых литералах реально показывается врачу
# (docstring/комментарии — не в счёт, поэтому вырезаются отдельно).
DOCTOR_TEXT_FILES = [
    "protocol_cap.py", "protocol_anemia.py", "protocol_rules.py",
    "protocol_rules_ida.py", "rules_engine.py", "cds_service.py",
    "care_plan_service.py", "protocol_verdict.py", "drug_service.py",
    "terminology.py",
]


def check_slash_between_words():
    print("\n[5] Врачебный текст — без «слипшегося» X/Y (см. STYLE_GUIDE §4.5): или « / », или «или»/«, »")
    any_bad = False
    for name in DOCTOR_TEXT_FILES:
        path = os.path.join(REPO, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read()
        text_no_docstrings = TRIPLE_QUOTE_RE.sub("", text)
        for lit in STRING_LIT_RE.finditer(text_no_docstrings):
            s = lit.group(0)
            if SLASH_WORDS_RE.search(s):
                line_no = text_no_docstrings[: lit.start()].count("\n") + 1
                fail(f"{name}:~{line_no}: слэш между словами без пробелов — {s[:100]!r}")
                any_bad = True
    if not any_bad:
        ok("нет X/Y без пробелов в строковых литералах врачебного текста")


def main() -> int:
    print("=" * 70)
    print("STYLE GATE — clinic-os (offline, без БД/сервера)")
    print("=" * 70)
    check_attr_value_sentences()
    check_button_inline_style()
    check_raw_status_leak()
    check_double_submit_guard_coverage()
    check_slash_between_words()
    print("\n" + "=" * 70)
    print(f"ИТОГ style_gate: {OK} ok, {FAIL} fail")
    print("=" * 70)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
