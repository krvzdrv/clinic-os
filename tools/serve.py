#!/usr/bin/env python
"""Запускает Flask-приложение как демон (двойной fork), чтобы оно пережило
завершение породившего shell-вызова в песочнице. Логи — в /tmp/clinic-os.log."""
import os
import sys
import subprocess

ENV_FILE = ".env"
LOG = "/tmp/clinic-os.log"
PORT = os.getenv("PORT", "5566")


def main():
    # Загружаем .env в окружение
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    os.environ["FLASK_DEBUG"] = "0"

    # Двойной fork для полного отсоединения от управляющего терминала
    if os.fork() > 0:
        return
    os.setsid()
    if os.fork() > 0:
        os._exit(0)

    # Перенаправляем stdio в лог
    sys.stdout.flush()
    sys.stderr.flush()
    with open(LOG, "w") as f:
        os.dup2(f.fileno(), 1)
        os.dup2(f.fileno(), 2)
    # stdin из /dev/null
    fd = os.open("/dev/null", os.O_RDONLY)
    os.dup2(fd, 0)

    subprocess.Popen([sys.executable, "app.py"], cwd=os.getcwd(), env=os.environ)
    # Порождённый процесс унаследует отсоединённые дескрипторы и останется жить


if __name__ == "__main__":
    main()
