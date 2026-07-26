#!/usr/bin/env python3
"""Быстрый визуально-текстовый QA блоков UI после правок.

Запуск (сервер уже на PORT, по умолчанию 5601):
  DATABASE_URL= PORT=5601 python3 tools/ui_block_qa.py

Не мутирует БД (в отличие от doctor_gate POST-сценариев).
"""
from __future__ import annotations

import os
import re
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("need playwright: pip install playwright && playwright install chrome")
    sys.exit(2)

BASE = f"http://127.0.0.1:{os.environ.get('PORT', '5601')}"
PAT_INITIAL = re.compile(r"\b[А-ЯЁ]\.$")


def main() -> int:
    issues: list[tuple[str, str, str]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 900})

        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        names = page.eval_on_selector_all(
            "table tbody tr td:first-child a", "els => els.map(e => e.textContent.trim())"
        )
        for n in names:
            if PAT_INITIAL.search(n):
                issues.append(("FAIL", "dashboard/fio", f"отчество обрезано: {n}"))

        steps = page.eval_on_selector_all(
            "table tbody tr",
            """els => els.map(r => {
              const t = r.querySelectorAll('td');
              return (t[4] && t[4].innerText || '').trim();
            })""",
        )
        for s in steps:
            if s and s != "—" and s[0].islower():
                issues.append(("FAIL", "dashboard/next", f"с маленькой: {s[:70]}"))
            if "лаб.:" in s or "инструмент.:" in s:
                issues.append(("FAIL", "dashboard/next", f"сокращения: {s}"))
            if " — " in s:
                left, _, right = s.partition("—")
                for w in left.split():
                    if len(w) > 5 and w.lower() in right.lower():
                        issues.append(("FAIL", "dashboard/next", f"дубль: {s[:80]}"))
                        break

        guest = page.locator(".guest-banner")
        if guest.count():
            gt = guest.inner_text()
            if PAT_INITIAL.search(gt) and "Иванович" not in gt:
                issues.append(("FAIL", "guest", "обрезанное ФИО"))
            if "Открыть карту" not in gt and "Открыть" not in gt:
                issues.append(("WARN", "guest", "нет одной CTA"))
        else:
            issues.append(("FAIL", "guest", "нет баннера"))

        # Соколов
        href = page.evaluate(
            """() => {
              const a = [...document.querySelectorAll('a')].find(x => /Соколов/.test(x.textContent||''));
              return a ? a.getAttribute('href') : null;
            }"""
        )
        if not href:
            issues.append(("FAIL", "sokolov", "нет ссылки на дашборде"))
        else:
            page.goto(BASE + href, wait_until="domcontentloaded")
            cds_vis = page.evaluate(
                """() => {
                  const el = document.querySelector('#now-action');
                  if (!el) return '';
                  const c = el.cloneNode(true);
                  c.querySelectorAll('select, option, script, style').forEach(n => n.remove());
                  return c.innerText.replace(/\\s+/g, ' ').trim();
                }"""
            )
            if not cds_vis:
                issues.append(("FAIL", "cds", "нет #now-action"))
            if "Старт АБТ" in cds_vis or "цефтриаксон — цефтриаксон" in cds_vis.lower():
                issues.append(("FAIL", "cds", "сырой текст дозы/АБТ"))
            if "J01" in cds_vis:
                issues.append(("FAIL", "cds", "ATC в видимом тексте"))
            more = page.evaluate(
                "() => document.querySelector('#now-action details.cds-more > summary')?.innerText || ''"
            )
            if more and re.fullmatch(r"Ещё\s+\d+\s*", more.strip()):
                issues.append(("FAIL", "cds-more", f"голое число: {more!r}"))

            fold = page.locator("details.history-fold > summary").inner_text()
            if re.search(r"·\s*\d+\s*$", fold.strip()):
                issues.append(("FAIL", "fold", f"голое число приёмов: {fold!r}"))
            if "приём" not in fold.lower():
                issues.append(("WARN", "fold", f"нет слова «приём»: {fold!r}"))

            page.evaluate(
                "() => document.querySelectorAll('details.fstep').forEach(d => { d.open = true; })"
            )
            diag = page.evaluate(
                "() => document.querySelector('#flow-diag .fstep-h .summary')?.innerText || ''"
            )
            if "лаб.:" in diag or "инструмент.:" in diag:
                issues.append(("FAIL", "diag", f"сокращения: {diag}"))
            if re.search(r"Лаборатория\s+\d+", diag or ""):
                issues.append(("FAIL", "diag", f"счётчик вместо содержимого: {diag}"))

            sizes = page.evaluate(
                """() => [...document.querySelectorAll('#flow-diag button.chip-x')]
                  .filter(b => b.offsetParent)
                  .slice(0, 10)
                  .map(b => {
                    const r = b.getBoundingClientRect();
                    return [Math.round(r.width), Math.round(r.height)];
                  })"""
            )
            for w, h in sizes:
                if w > 28 or h > 28:
                    issues.append(("FAIL", "chip-x", f"{w}×{h}"))

            forms = page.evaluate(
                """() => {
                  const adds = [...document.querySelectorAll('#flow-diag .add-panel')];
                  const open = adds.filter(a => a.open).length;
                  const vis = [...document.querySelectorAll('#flow-diag .fform')].filter(f => {
                    const d = f.closest('details.add-panel');
                    return d ? d.open : !!f.offsetParent;
                  }).length;
                  return {panels: adds.length, open, vis};
                }"""
            )
            if forms["open"] == 0 and forms["vis"] > 0:
                issues.append(("FAIL", "diag-forms", "формы видны при закрытых +"))

        browser.close()

    fails = [i for i in issues if i[0] == "FAIL"]
    warns = [i for i in issues if i[0] == "WARN"]
    print(f"ui_block_qa @ {BASE}")
    for sev, block, msg in issues:
        print(f"  {sev:4} [{block}] {msg}")
    print(f"итог: {len(fails)} fail, {len(warns)} warn")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
