#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Visual gate — ловит наложения «баблов» и регрессии вёрстки на карте пациента.

Два слоя:

  1) Геометрия DOM (обязательный, стабильный, без эталонов):
     в шапке диагноза (.dx-head) и строке истории (#dx-history .visit-row)
     название / дата / бейдж / шеврон не пересекаются; нет горизонтального
     overflow. Именно этот слой ловит «длинный статус наехал на дату».

  2) Скриншот-сравнение (опционально, если есть Pillow):
     ключевые блоки → tools/visual_baselines/*.png
     при расхождении выше порога — FAIL.
     Обновить эталоны: python3 tools/visual_gate.py --update

Запуск (сам поднимает временную SQLite + Flask + Playwright):
  python3 tools/visual_gate.py
  python3 tools/visual_gate.py --update

Если нет playwright — exit 2 (как button_audit). Pillow не обязателен:
без него слой 2 пропускается с WARN, слой 1 всё равно работает.

Когда гонять: тронули templates/**, static/clinic.css, или правили UI-статусы.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

BASELINES = REPO / "tools" / "visual_baselines"
OUT_DIR = REPO / "tools" / "visual_out"
def _pick_port() -> int:
    if os.environ.get("VISUAL_GATE_PORT"):
        return int(os.environ["VISUAL_GATE_PORT"])
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


PORT = _pick_port()
# Допуск пиксельного RMSE (0…100). UI на Retina/шрифтах чуть плавает — 4 достаточно
# строго для «баблы наехали», но не сыпется от сглаживания.
RMSE_MAX = float(os.environ.get("VISUAL_RMSE_MAX", "4.0"))

os.environ.pop("DATABASE_URL", None)
os.environ["DEMO_MODE"] = "1"
os.environ["FLASK_DEBUG"] = "0"

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False  # noqa: E731

import db  # noqa: E402

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
db.DB_PATH = os.path.abspath(_TMP.name)
os.environ["CLINIC_DB"] = db.DB_PATH

import fhir_store as fs  # noqa: E402
fs.init_db()

PASS = FAIL = WARN = 0


def ok(msg: str) -> None:
    global PASS
    print(f"  OK    {msg}")
    PASS += 1


def fail(msg: str) -> None:
    global FAIL
    print(f"  FAIL  {msg}")
    FAIL += 1


def warn(msg: str) -> None:
    global WARN
    print(f"  WARN  {msg}")
    WARN += 1


def _seed() -> dict[str, str]:
    import contextlib
    import importlib.util
    import io

    path = REPO / "tools" / "seed_ten.py"
    spec = importlib.util.spec_from_file_location("seed_ten_visual", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["seed_ten_visual"] = mod
    spec.loader.exec_module(mod)
    mod._clear_clinical()
    dr = fs.add_practitioner("Терапевт", "Анна", "терапия")
    stories = mod.seed_ten(dr)
    # _ensure_drugs печатает каталог — глушим шум гейта
    with contextlib.redirect_stdout(io.StringIO()):
        mod._ensure_drugs()
    import protocol_dispatch as pdisp
    for pid, _name, _story in stories:
        pdisp.refresh_protocol_cache(pid)
    return {name: pid for pid, name, _ in stories}


def _start_server():
    """Werkzeug make_server в daemon-потоке — надёжнее app.run() для gate."""
    from app import app
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", PORT, app, threaded=True)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    import urllib.error
    import urllib.request

    deadline = time.time() + 20
    last_err = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/", timeout=1)
            return server
        except urllib.error.HTTPError:
            return server
        except Exception as e:
            last_err = e
            time.sleep(0.25)
    raise RuntimeError(f"Flask не поднялся на :{PORT} ({last_err})")


# JS: пересечения и overflow в строках диагнозов
_GEOM_JS = """
() => {
  const hits = [];
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { t: r.top, l: r.left, b: r.bottom, r: r.right, w: r.width, h: r.height };
  };
  const overlap = (a, b) => !(a.r <= b.l + 0.5 || a.l >= b.r - 0.5 || a.b <= b.t + 0.5 || a.t >= b.b - 0.5);
  const visible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 1 && r.height > 1;
  };

  document.querySelectorAll('details.dx-card > summary.dx-head').forEach((head, i) => {
    const title = head.querySelector('.dx-title');
    const end = head.querySelector('.dx-end');
    if (visible(title) && visible(end) && overlap(box(title), box(end))) {
      hits.push(`dx-head[${i}]: название пересекается с датой/бейджем`);
    }
    if (end) {
      const parts = [...end.children].filter(visible);
      for (let a = 0; a < parts.length; a++) {
        for (let b = a + 1; b < parts.length; b++) {
          // шевроны-близнецы (right/down) — один скрыт CSS, visible() отфильтрует
          if (overlap(box(parts[a]), box(parts[b]))) {
            hits.push(`dx-head[${i}]: ${parts[a].className||parts[a].tagName} ∩ ${parts[b].className||parts[b].tagName}`);
          }
        }
      }
    }
    if (head.scrollWidth > head.clientWidth + 2) {
      hits.push(`dx-head[${i}]: горизонтальный overflow (${head.scrollWidth}>${head.clientWidth})`);
    }
  });

  document.querySelectorAll('#dx-history .visit-row').forEach((row, i) => {
    const parts = [...row.querySelectorAll(':scope > .vd, :scope > .vs, :scope > .badge')].filter(visible);
    for (let a = 0; a < parts.length; a++) {
      for (let b = a + 1; b < parts.length; b++) {
        if (overlap(box(parts[a]), box(parts[b]))) {
          hits.push(`history-row[${i}]: ${parts[a].className} ∩ ${parts[b].className}`);
        }
      }
    }
    if (row.scrollWidth > row.clientWidth + 2) {
      hits.push(`history-row[${i}]: горизонтальный overflow`);
    }
  });

  // Свёрнутые секции визита: бейдж не должен резаться, пока в строке есть
  // свободное место (типичный баг max-width:16em при пустой середине).
  document.querySelectorAll('details.fstep:not([open]) > summary.section-header').forEach((head, i) => {
    const badge = head.querySelector('.section-badge, .section-placeholder');
    const right = head.querySelector('.section-header__right');
    if (!visible(badge) || !right) return;
    const clipped = badge.scrollWidth > badge.clientWidth + 2;
    const free = head.clientWidth - head.scrollWidth; // <0 если сам head переполнен
    // Если текст обрезан ellipsis, а справа/в середине ещё ≥40px «воздуха» — FAIL
    const hb = box(head);
    const bb = box(badge);
    const gapBeforeBadge = bb.l - hb.l - 80; // грубо: иконка+заголовок ~80px
    if (clipped && gapBeforeBadge > 120) {
      hits.push(`fstep[${i}] «${(head.querySelector('.section-header__title')||{}).textContent||'?'}»: бейдж обрезан при свободном месте`);
    }
  });

  return hits;
}
"""


def _rmse(a_path: Path, b_path: Path) -> float:
    """Среднеквадратичная ошибка по пикселям 0…100 (процент от 255)."""
    from PIL import Image, ImageChops, ImageStat

    a = Image.open(a_path).convert("RGB")
    b = Image.open(b_path).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    # mean of RMS across channels
    rms = sum(stat.rms) / max(len(stat.rms), 1)
    return (rms / 255.0) * 100.0


def _shot(page, selector: str, name: str, update: bool) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BASELINES.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{name}.png"
    base = BASELINES / f"{name}.png"
    loc = page.locator(selector).first
    try:
        n = loc.count()
    except Exception:
        n = 0
    if n == 0:
        fail(f"скрин {name}: нет селектора {selector}")
        return
    try:
        if not loc.is_visible(timeout=1500):
            warn(f"скрин {name}: элемент не видим — пропуск")
            return
        loc.scroll_into_view_if_needed(timeout=3000)
        page.wait_for_timeout(80)
        loc.screenshot(path=str(out), timeout=5000)
    except Exception as e:
        fail(f"скрин {name}: {str(e)[:120]}")
        return
    if update:
        base.write_bytes(out.read_bytes())
        ok(f"эталон обновлён: {base.relative_to(REPO)}")
        return
    if not base.exists():
        warn(f"нет эталона {base.relative_to(REPO)} — создайте: --update")
        return
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        warn(f"Pillow нет — пропуск сравнения {name} (есть {out.name})")
        return
    err = _rmse(base, out)
    if err <= RMSE_MAX:
        ok(f"скрин {name}: RMSE={err:.2f} ≤ {RMSE_MAX}")
    else:
        fail(f"скрин {name}: RMSE={err:.2f} > {RMSE_MAX} (см. {out.relative_to(REPO)})")


def main() -> int:
    ap = argparse.ArgumentParser(description="Visual gate clinic-os")
    ap.add_argument("--update", action="store_true", help="перезаписать visual_baselines")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("need: pip install playwright && playwright install chrome")
        return 2

    print("=" * 70)
    print("VISUAL GATE — геометрия + скриншоты")
    print(f"DB: {db.DB_PATH}")
    print(f"URL: http://127.0.0.1:{PORT}/")
    print("=" * 70)

    print("\n[1] Сид")
    by_name = _seed()
    # Контролёв: активные + история (длинные названия, бейджи, даты)
    pid = by_name.get("Контролёв") or by_name.get("Аллергова") or next(iter(by_name.values()))
    ok(f"пациент для проверки: {pid}")

    print("\n[2] Сервер")
    _start_server()
    ok(f"Flask :{PORT}")

    print("\n[3] Геометрия (наложения / overflow)")
    with sync_playwright() as p:
        # channel=chrome как в button_audit; fallback на bundled chromium
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(f"http://127.0.0.1:{PORT}/patient/{pid}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(200)

        # Раскрыть историю диагнозов — там как раз длинные статусы
        page.evaluate(
            """() => {
              const h = document.querySelector('#dx-history');
              if (h) h.open = true;
            }"""
        )
        page.wait_for_timeout(100)

        hits = page.evaluate(_GEOM_JS)
        if not hits:
            ok("нет пересечений бейдж/дата/название и нет overflow")
        else:
            for h in hits:
                fail(h)

        print("\n[4] Скриншоты ключевых блоков")
        _shot(page, "#conditions-list", "conditions-list", args.update)
        # Видимый CDS: сначала вложенный под диагнозом, иначе верхний #now-action
        cds = page.locator(".dx-card[open] .dx-issue.cds, .dx-card .dx-issue.cds:visible, #now-action:visible")
        if cds.count() and cds.first.is_visible():
            _shot(page, ".dx-card .dx-issue.cds:visible, #now-action:visible", "cds-panel", args.update)
        else:
            # Раскроем первый активный диагноз с вердиктом
            page.evaluate(
                """() => {
                  const d = document.querySelector('details.dx-card');
                  if (d) d.open = true;
                }"""
            )
            page.wait_for_timeout(80)
            if page.locator(".dx-issue.cds, #now-action").count():
                _shot(page, "details.dx-card[open]", "cds-panel", args.update)
            else:
                warn("нет CDS-блока на этой карте — пропуск cds-panel")

        # Узкий viewport — ловит наложения, которые на 1280 не видны
        print("\n[5] Геометрия на узком экране (900px)")
        page.set_viewport_size({"width": 900, "height": 900})
        page.wait_for_timeout(100)
        hits_n = page.evaluate(_GEOM_JS)
        if not hits_n:
            ok("900px: нет пересечений / overflow")
        else:
            for h in hits_n:
                fail(f"900px: {h}")
        _shot(page, "#conditions-list", "conditions-list-900", args.update)

        browser.close()

    print("\n" + "=" * 70)
    print(f"ИТОГ visual_gate: {PASS} ok, {FAIL} fail, {WARN} warn")
    print("=" * 70)
    if FAIL:
        print("\nПодсказка: если упал только скрин после осознанной правки UI —")
        print("  python3 tools/visual_gate.py --update")
        print("и закоммитьте tools/visual_baselines/.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
