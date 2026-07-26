#!/usr/bin/env python3
"""Загрузка актуальных BPMN clinic-os в Camunda 8 (SaaS / self-managed).

Нужны переменные окружения (или флаги):
  CAMUNDA_BASE_URL   — например https://bru-2.zeebe.camunda.io / http://localhost:8080
  CAMUNDA_CLIENT_ID  — OAuth client id (SaaS) ИЛИ пусто для basic
  CAMUNDA_CLIENT_SECRET
  CAMUNDA_AUTH_URL   — опционально, default: https://login.cloud.camunda.io/oauth/token
  CAMUNDA_AUDIENCE   — опционально, default: zeebe.camunda.io
  CAMUNDA_CLUSTER_ID — для SaaS Operate/Web Modeler deployment API, если используете

Режимы:
  1) Camunda 8 SaaS — Web Modeler / cluster deploy через REST (нужен токен).
  2) Camunda 7 Engine — POST /engine-rest/deployment/create (basic auth).

По умолчанию: Camunda 7-совместимый deploy (самый частый self-hosted).

Примеры:
  CAMUNDA_BASE_URL=http://localhost:8080 \\
  CAMUNDA_USER=demo CAMUNDA_PASSWORD=demo \\
  python3 tools/deploy_bpmn_camunda.py

  CAMUNDA_MODE=c8 CAMUNDA_BASE_URL=... CAMUNDA_CLIENT_ID=... \\
  CAMUNDA_CLIENT_SECRET=... python3 tools/deploy_bpmn_camunda.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BPMN_DIR = REPO / "docs" / "bpmn"
DEFAULT_FILES = [
    BPMN_DIR / "cap-outpatient-mature.bpmn",
    BPMN_DIR / "cap-inpatient-mature.bpmn",
]


def _require(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        raise SystemExit(f"Нет {name}. Задайте в окружении или передайте флагом.")
    return v


def deploy_c7(base: str, user: str, password: str, files: list[Path]) -> None:
    import urllib.request
    import ssl
    from base64 import b64encode

    url = base.rstrip("/") + "/engine-rest/deployment/create"
    boundary = "----ClinicOsBpmnBoundary"
    body = bytearray()

    def add_field(name: str, value: str) -> None:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode() + b"\r\n")

    add_field("deployment-name", "clinic-os-cap")
    add_field("enable-duplicate-filtering", "true")
    add_field("deploy-changed-only", "true")

    for path in files:
        data = path.read_bytes()
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(
            (
                f'Content-Disposition: form-data; name="data"; filename="{path.name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
        )
        body.extend(data + b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(url, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    token = b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")

    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        print(f"OK C7 deploy → {resp.status}")
        print(raw[:2000])


def deploy_c8_zeebe_gateway(files: list[Path]) -> None:
    """Минимальная проверка: для C8 обычно нужен zbctl / camunda CLI.

    Здесь только подсказка — полный SaaS deploy зависит от продукта (Web Modeler vs Zeebe).
    """
    raise SystemExit(
        "Режим c8: поставьте Camunda CLI (`camunda`) или zbctl и выполните deploy вручную,\n"
        "либо дайте CAMUNDA_MODE=c7 с URL engine-rest.\n"
        f"Файлы готовы: {', '.join(p.name for p in files)}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy clinic-os BPMN to Camunda")
    ap.add_argument("--mode", default=os.getenv("CAMUNDA_MODE", "c7"), choices=("c7", "c8"))
    ap.add_argument("--base-url", default=os.getenv("CAMUNDA_BASE_URL", ""))
    ap.add_argument("--user", default=os.getenv("CAMUNDA_USER", "demo"))
    ap.add_argument("--password", default=os.getenv("CAMUNDA_PASSWORD", "demo"))
    ap.add_argument("files", nargs="*", type=Path, help="BPMN paths (default: docs/bpmn/*-mature.bpmn)")
    args = ap.parse_args()

    files = list(args.files) if args.files else list(DEFAULT_FILES)
    for p in files:
        if not p.is_file():
            raise SystemExit(f"Нет файла: {p}")

    print("Deploy files:")
    for p in files:
        print(f"  - {p.relative_to(REPO)} ({p.stat().st_size} bytes)")

    if args.mode == "c7":
        base = args.base_url or _require("CAMUNDA_BASE_URL")
        deploy_c7(base, args.user, args.password, files)
    else:
        deploy_c8_zeebe_gateway(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
