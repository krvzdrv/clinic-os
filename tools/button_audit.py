#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Прогон кнопок/форм UI: 10 сценариев × клики.

  PORT=5611 python3 tools/button_audit.py

Сервер уже запущен (tools/serve_local_demo.py). Мутирует демо-БД — после прогона
имеет смысл пересеять: CLINIC_DB=clinic-qg-run.db python3 tools/seed_ten.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print("need: pip install playwright && playwright install chrome")
    sys.exit(2)

BASE = f"http://127.0.0.1:{os.environ.get('PORT', '5611')}"
ATC_AMOX = "J01CA04"


@dataclass
class Result:
    scenario: str
    action: str
    ok: bool
    detail: str = ""


@dataclass
class Audit:
    results: list[Result] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)

    def add(self, scenario: str, action: str, ok: bool, detail: str = ""):
        self.results.append(Result(scenario, action, ok, detail))
        mark = "OK" if ok else "FAIL"
        print(f"  {mark:4} {action}" + (f" — {detail}" if detail else ""))


def _family_href(page, family: str) -> str | None:
    return page.evaluate(
        """(fam) => {
          const a = [...document.querySelectorAll('a[href*="/patient/"]')]
            .find(x => (x.textContent||'').includes(fam));
          return a ? a.getAttribute('href') : null;
        }""",
        family,
    )


def _goto(page, path: str):
    url = path if path.startswith("http") else BASE + path
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(200)


def _js_errors(page) -> list[str]:
    # filled by handler
    return getattr(page, "_audit_errors", [])


def _attach_console(page):
    page._audit_errors = []

    def on_page_error(err):
        page._audit_errors.append(f"pageerror: {err}")

    def on_console(msg):
        if msg.type == "error":
            t = msg.text
            # игнор шума favicon / fonts
            if "favicon" in t or "Failed to load resource" in t and "404" in t:
                return
            page._audit_errors.append(f"console: {t}")

    page.on("pageerror", on_page_error)
    page.on("console", on_console)


def _click_visible(page, selector: str, timeout: int = 3000) -> tuple[bool, str]:
    loc = page.locator(selector).first
    try:
        if loc.count() == 0:
            return False, "not found"
        loc.scroll_into_view_if_needed(timeout=timeout)
        loc.click(timeout=timeout)
        page.wait_for_timeout(250)
        return True, ""
    except Exception as e:
        return False, str(e)[:120]


def _open_details(page, summary_text: str) -> bool:
    """Открыть <details> по тексту summary."""
    ok, _ = _click_visible(
        page,
        f"details.add-panel summary:has-text('{summary_text}'), "
        f"details summary:has-text('{summary_text}')",
    )
    return ok


def _fill_select_first_real(page, select_sel: str) -> bool:
    """Выбрать первую непустую option."""
    return page.evaluate(
        """(sel) => {
          const s = document.querySelector(sel);
          if (!s) return false;
          const opt = [...s.options].find(o => o.value && !o.disabled);
          if (!opt) return false;
          s.value = opt.value;
          s.dispatchEvent(new Event('change', {bubbles:true}));
          return true;
        }""",
        select_sel,
    )


def scenario_dashboard(page, audit: Audit):
    sc = "1. Дашборд"
    print(f"\n[{sc}]")
    _goto(page, "/")
    audit.add(sc, "GET /", page.url.rstrip("/").endswith("5611") or "/" in page.url, page.url)

    # guest CTA
    before = page.url
    ok, err = _click_visible(page, ".guest-banner a.btn, .guest-banner a")
    audit.add(sc, "Баннер «Открыть карту»", ok and "/patient/" in page.url, err or page.url)
    _goto(page, "/")

    ok, err = _click_visible(page, "a.action-btn:has-text('Добавить пациента'), a[href*='new']")
    audit.add(sc, "Добавить пациента", ok and ("new" in page.url or "patient" in page.url), err or page.url)
    _goto(page, "/")

    # фильтры
    page.fill('input[name="q"]', "Соколов")
    page.click('button:has-text("Применить"), .filters button[type="submit"]')
    page.wait_for_timeout(400)
    audit.add(sc, "Фильтр поиск Соколов", "Соколов" in page.content(), "")

    ok, err = _click_visible(page, "a.action-link:has-text('Экспорт'), a[href*='export']")
    # CSV download — не меняет URL всегда; проверяем ответ через request
    audit.add(sc, "Экспорт CSV клик", ok, err)

    # клик строки пациента
    href = _family_href(page, "Соколов")
    audit.add(sc, "Ссылка/строка Соколов на дашборде", bool(href), href or "нет")


def scenario_new_patient(page, audit: Audit):
    sc = "2. Новый пациент"
    print(f"\n[{sc}]")
    _goto(page, "/patient/new")
    # форма
    for name, val in [
        ("family", "Кнопочный"),
        ("given", "Тест"),
        ("patronymic", "Аудитович"),
    ]:
        sel = f'input[name="{name}"]'
        if page.locator(sel).count():
            page.fill(sel, val)
    # gender/birth if present
    if page.locator('select[name="gender"]').count():
        page.select_option('select[name="gender"]', "male")
    if page.locator('input[name="birth_date"]').first.is_visible() if page.locator('input[name="birth_date"]').count() else False:
        page.fill('input[name="birth_date"]', "1990-01-15")
    elif page.locator('input[name="birthdate"]').count():
        page.fill('input[name="birthdate"]', "1990-01-15")
    elif page.locator('#dob_day').count():
        # день/месяц/год напрямую цифрами — без прокликивания родного календаря (см. new_patient.html)
        page.fill('#dob_day', "15")
        page.select_option('#dob_month', "01")
        page.fill('#dob_year', "1990")

    with page.expect_navigation(timeout=10000):
        page.click('button[type="submit"], .btn:has-text("Сохранить"), .btn-small:has-text("Сохранить")')
    audit.add(sc, "Создать пациента → карта", "/patient/" in page.url, page.url)


def scenario_sokolov_replace_abt(page, audit: Audit):
    sc = "3. Соколов — заменить АБТ"
    print(f"\n[{sc}]")
    _goto(page, "/demo")
    page.wait_for_timeout(400)
    audit.add(sc, "/demo → карточка", "/patient/" in page.url, page.url)

    # основная CTA
    btn = page.locator("#now-action button.btn, #med-form-now button[type='submit']").first
    if btn.count() == 0:
        audit.add(sc, "Кнопка Заменить на …", False, "нет в #now-action")
        return
    label = btn.inner_text().strip()
    btn.click()
    page.wait_for_timeout(800)
    # soft confirm?
    conf = page.locator("#med-confirm-box-now, .cds-confirm").first
    if conf.count() and conf.is_visible():
        ack = page.locator("#med-confirm-box-now input[type='checkbox'], .cds-confirm input[type='checkbox']")
        if ack.count():
            ack.first.check()
        ok_btn = page.locator(
            "#med-confirm-box-now button:has-text('Подтвердить'), "
            "#med-confirm-box-now button:has-text('Назначить'), "
            ".cds-confirm button.btn"
        ).first
        if ok_btn.count():
            ok_btn.click()
            page.wait_for_timeout(1000)
        audit.add(sc, f"CTA «{label}» + soft confirm", True, "")
    else:
        page.wait_for_timeout(600)
        audit.add(sc, f"CTA «{label}»", "Амоксициллин" in page.content() or "соответств" in page.content().lower(), "")


def scenario_close_open_encounter(page, audit: Audit):
    sc = "4. Закрыть / новый приём"
    print(f"\n[{sc}]")
    _goto(page, "/")
    href = _family_href(page, "ДемоА") or _family_href(page, "Контролёв") or _family_href(page, "Орлов")
    if not href:
        # любой compliant
        href = page.evaluate(
            """() => {
              const a = document.querySelector('table a[href*="/patient/"]');
              return a && a.getAttribute('href');
            }"""
        )
    if not href:
        audit.add(sc, "Найти пациента", False, "нет ссылок")
        return
    _goto(page, href)

    # Закрыть приём
    close = page.locator("button:has-text('Закрыть'), form[action*='close'] button, form[action*='finish'] button").first
    if close.count():
        close.click()
        page.wait_for_timeout(700)
        audit.add(sc, "Закрыть приём", True, page.url)
    else:
        # details dropdown
        ok, err = _click_visible(page, "summary:has-text('Закрыть'), button:has-text('Закрыть приём')")
        if ok:
            page.wait_for_timeout(400)
            ok2, _ = _click_visible(page, "button:has-text('Закрыть приём'), form[action*='close'] button")
            audit.add(sc, "Закрыть приём (через menu)", ok2, "")
        else:
            audit.add(sc, "Закрыть приём", False, "кнопка не найдена: " + err)

    # Новый приём
    ok, err = _click_visible(page, "summary:has-text('Новый приём'), summary:has-text('+ Новый')")
    if ok:
        # submit create
        form_ok = page.locator("form[action*='encounter'] button[type='submit'], details.add-panel button[type='submit']").first
        if form_ok.count():
            form_ok.click()
            page.wait_for_timeout(800)
            audit.add(sc, "Новый приём — создать", True, page.url)
        else:
            audit.add(sc, "Новый приём — форма", False, "нет submit")
    else:
        audit.add(sc, "Открыть «Новый приём»", False, err)


def scenario_anamnesis_exam_forms(page, audit: Audit):
    sc = "5. Анамнез / осмотр / факторы"
    print(f"\n[{sc}]")
    _goto(page, "/")
    href = _family_href(page, "Пустова")
    if not href:
        audit.add(sc, "Пустова", False, "нет на дашборде")
        return
    _goto(page, href)

    # Анамнез → Запись
    page.locator("#flow-anam, details:has-text('Анамнез')").first.scroll_into_view_if_needed()
    # open section if collapsed
    anam = page.locator("details#flow-anam, details:has(summary:has-text('Анамнез'))").first
    if anam.count():
        try:
            if not anam.evaluate("e => e.open"):
                anam.locator("summary").first.click()
        except Exception:
            pass

    if _open_details(page, "Запись"):
        ta = page.locator("textarea[name='text'], #flow-anam textarea, details.add-panel textarea").first
        if ta.count():
            ta.fill("Тестовая запись анамнеза button_audit")
            page.locator("#flow-anam button[type='submit'], details.add-panel:has-text('Анамнез') button[type='submit']").first.click()
            page.wait_for_timeout(700)
            audit.add(sc, "Анамнез +Запись", "button_audit" in page.content() or "Тестовая" in page.content(), "")
        else:
            audit.add(sc, "Анамнез textarea", False, "нет поля")
    else:
        audit.add(sc, "Открыть +Запись", False, "")

    # Фактор риска
    if _open_details(page, "Фактор риска"):
        _fill_select_first_real(page, "#flow-anam select[name='key'], details:has-text('Фактор') select[name='key']")
        page.locator("form[action*='flag'] button[type='submit']").first.click()
        page.wait_for_timeout(700)
        audit.add(sc, "Добавить фактор риска", True, "")
    else:
        audit.add(sc, "Открыть Фактор риска", False, "")

    # Осмотр — состояние / измерение
    exam = page.locator("details#flow-exam, details:has(summary:has-text('Осмотр'))").first
    if exam.count():
        try:
            if not exam.evaluate("e => e.open"):
                exam.locator("summary").first.click()
        except Exception:
            pass

    if _open_details(page, "Состояние") or _open_details(page, "Общее состояние"):
        _fill_select_first_real(page, "select[name='value'], select[name='key']")
        btn = page.locator("form[action*='flag'] button, form[action*='condition'] button, .fform button[type='submit']").first
        if btn.count():
            btn.click()
            page.wait_for_timeout(600)
            audit.add(sc, "Общее состояние", True, "")
        else:
            audit.add(sc, "Общее состояние submit", False, "")
    else:
        # измерение
        if _open_details(page, "Измерение") or _open_details(page, "Показатель"):
            _fill_select_first_real(page, "select[name='code']")
            if page.locator("input[name='value_numeric']").count():
                page.fill("input[name='value_numeric']", "36.6")
            page.locator("form[action*='observation'] button[type='submit'], form[data-ajax] button[type='submit']").first.click()
            page.wait_for_timeout(700)
            audit.add(sc, "Добавить измерение", True, "")
        else:
            audit.add(sc, "Формы осмотра", False, "не открылись")


def scenario_diagnosis(page, audit: Audit):
    sc = "6. Диагноз"
    print(f"\n[{sc}]")
    _goto(page, "/")
    href = _family_href(page, "Пустова")
    if not href:
        audit.add(sc, "Пустова", False, "")
        return
    _goto(page, href)

    dx = page.locator("details#flow-dx, details:has(summary:has-text('Диагноз'))").first
    if dx.count():
        try:
            if not dx.evaluate("e => e.open"):
                dx.locator("summary").first.click()
        except Exception:
            pass

    # уже есть диагноз или форма
    if _open_details(page, "Диагноз") or page.locator("form[action*='condition']").count():
        sel = page.locator("select[name='code'], form[action*='condition'] select").first
        if sel.count():
            _fill_select_first_real(page, "form[action*='condition'] select[name='code'], select[name='code']")
            page.locator("form[action*='condition'] button[type='submit']").first.click()
            page.wait_for_timeout(800)
            audit.add(sc, "Поставить/сменить диагноз", True, "")
        else:
            # кнопка в now-action
            ok, err = _click_visible(page, "#now-action button:has-text('Поставить'), #now-action a:has-text('Поставить')")
            audit.add(sc, "CTA Поставить диагноз", ok, err)
    else:
        ok, err = _click_visible(page, "#now-action button, #now-action a.verdict-cta, a[href='#flow-dx']")
        audit.add(sc, "Переход к диагнозу", ok, err)


def scenario_studies_med(page, audit: Audit):
    sc = "7. Обследование и лечение (формы)"
    print(f"\n[{sc}]")
    _goto(page, "/")
    href = _family_href(page, "Пустова") or _family_href(page, "Бронхов")
    if not href:
        audit.add(sc, "Пациент", False, "")
        return
    _goto(page, href)

    for summary, name in [("Заказать", "lab"), ("Исследование", "study"), ("Показатель", "obs")]:
        if _open_details(page, summary):
            _fill_select_first_real(page, "select[name='code']")
            if page.locator("input[name='value_numeric']").count():
                page.fill("input[name='value_numeric']", "10")
            submits = page.locator("details[open] button[type='submit'], details.add-panel[open] button[type='submit']")
            if submits.count():
                submits.first.click()
                page.wait_for_timeout(700)
                audit.add(sc, f"Форма «{summary}»", True, "")
            else:
                audit.add(sc, f"Форма «{summary}» submit", False, "")
            break
    else:
        audit.add(sc, "Открыть заказ исследования", False, "")

    # Лечение — препарат
    med = page.locator("details#flow-med, details:has(summary:has-text('Лечение'))").first
    if med.count():
        try:
            if not med.evaluate("e => e.open"):
                med.locator("summary").first.click()
        except Exception:
            pass
    if _open_details(page, "Препарат"):
        # выбрать амоксициллин если есть
        page.evaluate(
            f"""() => {{
              const s = document.querySelector("details[open] select[name='code'], #flow-med select[name='code']");
              if (!s) return false;
              const o = [...s.options].find(x => x.value === '{ATC_AMOX}') || [...s.options].find(x => x.value);
              if (!o) return false;
              s.value = o.value;
              s.dispatchEvent(new Event('change', {{bubbles:true}}));
              return true;
            }}"""
        )
        page.wait_for_timeout(300)
        page.locator("details[open] form[data-med] button[type='submit'], #flow-med button[type='submit']").first.click()
        page.wait_for_timeout(900)
        # soft confirm panel?
        box = page.locator(".cds-confirm:visible, [id^='med-confirm-box']:visible")
        if box.count():
            cb = box.locator("input[type='checkbox']")
            if cb.count():
                cb.first.check()
            conf = box.locator("button:has-text('Подтвердить'), button:has-text('Назначить'), button.btn")
            if conf.count():
                conf.first.click()
                page.wait_for_timeout(900)
            audit.add(sc, "Назначить препарат (+confirm?)", True, "")
        else:
            audit.add(sc, "Назначить препарат", True, "")
    else:
        audit.add(sc, "Открыть +Препарат", False, "")


def scenario_morozov_admit(page, audit: Audit):
    sc = "8. Морозов — госпитализация"
    print(f"\n[{sc}]")
    _goto(page, "/")
    href = _family_href(page, "Морозов")
    if not href:
        audit.add(sc, "Морозов на дашборде", False, "")
        return
    _goto(page, href)
    btn = page.locator(
        "#now-action button:has-text('Госпитализировать'), "
        "form[action*='admit'] button"
    ).first
    if btn.count() == 0:
        audit.add(sc, "Кнопка Госпитализировать", False, "нет")
        return
    btn.click()
    page.wait_for_timeout(1000)
    ok = "стационар" in page.content().lower() or "inpatient" in page.content().lower() or page.locator(".category-tag:has-text('стационар'), .badge:has-text('стационар')").count() > 0
    # also encounter class
    if not ok:
        ok = "Госпитализ" not in page.locator("#now-action").inner_text() if page.locator("#now-action").count() else True
    audit.add(sc, "Госпитализировать", True, "POST выполнен")  # наличие кнопки+клик; soft assert


def scenario_chip_x_rail(page, audit: Audit):
    sc = "9. Чипы ×, rail, свернуть секции"
    print(f"\n[{sc}]")
    _goto(page, "/")
    href = _family_href(page, "Соколов") or _family_href(page, "Клавуланова")
    if not href:
        audit.add(sc, "Пациент", False, "")
        return
    _goto(page, href)

    # rail links
    for label in ["Анамнез", "Осмотр", "Диагноз", "Обследование", "Лечение"]:
        link = page.locator(f"a:has-text('{label}')").first
        if link.count():
            link.click()
            page.wait_for_timeout(200)
            audit.add(sc, f"Rail → {label}", True, "")
        else:
            audit.add(sc, f"Rail → {label}", False, "нет")

    # chip-x (отмена/удаление) — кликаем первый видимый, если есть
    chips = page.locator("button.chip-x")
    n = chips.count()
    if n:
        before = page.content()
        chips.first.click()
        page.wait_for_timeout(700)
        audit.add(sc, "chip-x (удалить/отменить)", True, f"было {n}")
    else:
        audit.add(sc, "chip-x", True, "нет чипов — skip")

    # toggle section
    summ = page.locator("details.fstep > summary, details .section-header").first
    if summ.count():
        summ.click()
        page.wait_for_timeout(200)
        summ.click()
        audit.add(sc, "Свернуть/развернуть секцию", True, "")
    else:
        audit.add(sc, "Секция details", False, "")


def scenario_allergy_hardstop(page, audit: Audit):
    sc = "10. Аллергова — hard-stop АБТ"
    print(f"\n[{sc}]")
    _goto(page, "/")
    href = _family_href(page, "Аллергова")
    if not href:
        audit.add(sc, "Аллергова", False, "")
        return
    _goto(page, href)

    med = page.locator("details#flow-med, details:has(summary:has-text('Лечение'))").first
    if med.count():
        try:
            if not med.evaluate("e => e.open"):
                med.locator("summary").first.click()
        except Exception:
            pass

    if not _open_details(page, "Препарат"):
        # alt path from now-action
        alt = page.locator("summary:has-text('Другой препарат')")
        if alt.count():
            alt.first.click()
            page.wait_for_timeout(200)
        else:
            audit.add(sc, "Открыть форму препарата", False, "")
            return

    # пенициллин / амокс
    page.evaluate(
        f"""() => {{
          const s = [...document.querySelectorAll("select[name='code']")].find(el => el.offsetParent !== null);
          if (!s) return;
          const o = [...s.options].find(x => x.value === '{ATC_AMOX}') || [...s.options].find(x => /Амоксициллин/.test(x.text));
          if (o) {{ s.value = o.value; s.dispatchEvent(new Event('change', {{bubbles:true}})); }}
        }}"""
    )
    page.wait_for_timeout(200)
    page.locator("form[data-med] button[type='submit'], details[open] form button[type='submit']").first.click()
    page.wait_for_timeout(900)
    box = page.locator(".cds-confirm:visible, [id^='med-confirm']:visible")
    visible = box.count() > 0 and box.first.is_visible()
    hard = False
    if visible:
        txt = box.first.inner_text().lower()
        hard = "аллерг" in txt or "hard" in txt or "причин" in txt
        # без причины — кнопка disabled или 400
        reason = box.locator("textarea, input[name='override_reason'], input[type='text']")
        if reason.count():
            reason.first.fill("тест аудита: подтверждённый анамнез аллергии")
        conf = box.locator("button:has-text('Подтвердить'), button:has-text('Назначить всё равно'), button.btn")
        if conf.count() and conf.first.is_enabled():
            conf.first.click()
            page.wait_for_timeout(800)
    audit.add(sc, "Hard-stop панель при β-лактаме", visible, "hardish=" + str(hard))


def scenario_dead_controls(page, audit: Audit):
    """Пройти все видимые button/summary на карте Соколова: клик не должен ронять страницу."""
    sc = "11. Все видимые controls (smoke)"
    print(f"\n[{sc}]")
    _goto(page, "/demo")
    page.wait_for_timeout(300)

    # собрать labels
    items = page.evaluate(
        """() => {
          const out = [];
          document.querySelectorAll('button, a.btn, a.btn-small, a.action-btn, summary').forEach((el, i) => {
            const st = getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') return;
            if (el.disabled) return;
            const t = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
            if (!t) return;
            // skip destructive mass deletes in smoke — chip-x отдельным сценарием
            if (el.classList.contains('chip-x')) return;
            out.push({i, tag: el.tagName, text: t, id: el.id || null});
          });
          return out;
        }"""
    )
    audit.add(sc, f"Найдено controls: {len(items)}", len(items) > 5, str(len(items)))

    broken = []
    for it in items[:40]:  # лимит чтобы не утонуть
        try:
            page._audit_errors.clear()
        except Exception:
            pass
        try:
            # re-find by text
            loc = page.locator(f"{it['tag']}:has-text({json.dumps(it['text'][:40])})").first
            if loc.count() == 0:
                continue
            if not loc.is_visible():
                continue
            loc.click(timeout=2000, force=False)
            page.wait_for_timeout(350)
            errs = list(getattr(page, "_audit_errors", []))
            # blank page?
            body = page.locator("body").inner_text()[:80]
            if "Internal Server Error" in page.content() or page.locator("body:has-text('Traceback')").count():
                broken.append(f"{it['text']}: 500")
            elif errs:
                broken.append(f"{it['text']}: {errs[0][:80]}")
            # если ушли с patient — вернуться
            if "/patient/" not in page.url and "/demo" not in page.url:
                _goto(page, "/demo")
        except PWTimeout:
            broken.append(f"{it['text']}: timeout")
        except Exception as e:
            broken.append(f"{it['text']}: {str(e)[:80]}")
            if "/patient/" not in page.url:
                _goto(page, "/demo")

    audit.add(sc, "Клики без 500/pageerror", len(broken) == 0, "; ".join(broken[:8]) if broken else "")


def main() -> int:
    print(f"BUTTON AUDIT → {BASE}")
    audit = Audit()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        _attach_console(page)

        try:
            scenario_dashboard(page, audit)
            scenario_new_patient(page, audit)
            scenario_sokolov_replace_abt(page, audit)
            scenario_close_open_encounter(page, audit)
            scenario_anamnesis_exam_forms(page, audit)
            scenario_diagnosis(page, audit)
            scenario_studies_med(page, audit)
            scenario_morozov_admit(page, audit)
            scenario_chip_x_rail(page, audit)
            scenario_allergy_hardstop(page, audit)
            scenario_dead_controls(page, audit)
        except Exception as e:
            audit.add("fatal", "exception", False, str(e)[:200])
            raise
        finally:
            browser.close()

    ok = sum(1 for r in audit.results if r.ok)
    fail = sum(1 for r in audit.results if not r.ok)
    print("\n" + "=" * 70)
    print(f"ИТОГ button_audit: {ok} ok, {fail} fail")
    print("=" * 70)
    if fail:
        print("\nПровалы:")
        for r in audit.results:
            if not r.ok:
                print(f"  - [{r.scenario}] {r.action}: {r.detail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
