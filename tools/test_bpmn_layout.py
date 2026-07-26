#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Геометрия и канон mature BPMN (docs/processes/BPMN_PRACTICES.md + замечания по схемам).

Проверяет все docs/bpmn/*mature*.bpmn:
  1) XML + каждый sequenceFlow/association имеет DI-edge, каждый узел — shape
  2) в task/subProcess ровно один входящий поток (сходимость — через XOR-merge)
  3) нет default= на exclusiveGateway (косая «поломка»)
  4) нет swimlanes
  5) концы рёбер прикреплены к фигурам source/target (не «поток в воздухе»)
  6) boundary не по центру низа подпроцесса (там [+])
  7) в name/annotation нет техжаргона (версия, .bpmn, handoff, …)
  8) в name нет разговорного/канцелярского сленга («Картина ясна», «Диагноз или…»)
     и UI/HTTP-жаргона (hard-stop, order-sign, чекбокс, 400)

Запуск: python3 tools/test_bpmn_layout.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
BPMN_DIR = ROOT / "docs" / "bpmn"
ATTACH_TOL = 12.0  # px: waypoint должен касаться bounds фигуры
DIAGNOSIS_WORDS_CARE = (
    "пневмония",
    "анемия",
    "железодефицит",
    "ждя",
    "мкб",
    "j18",
    "j12",
)
FORBIDDEN_NAME = (
    "версия",
    "version",
    "handoff",
    "подпроцесс",
    "двойной клик",
    ".bpmn",
    ".py",
    "bpmn_task",
    "process_id",
    "hard-stop",
    "soft-stop",
    "order-sign",
    "чекбокс",
    "картина ясна",
    "диагноз или",
    "диагноз(ы)",
)
ACTIVITY_TAGS = frozenset(
    {"task", "subProcess", "userTask", "serviceTask", "callActivity", "scriptTask"}
)
NODE_TAGS = ACTIVITY_TAGS | {
    "startEvent",
    "endEvent",
    "exclusiveGateway",
    "parallelGateway",
    "inclusiveGateway",
    "eventBasedGateway",
    "boundaryEvent",
    "intermediateCatchEvent",
    "intermediateThrowEvent",
    "textAnnotation",
}


def loc(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def dist_point_to_rect(px: float, py: float, rect) -> float:
    x, y, w, h = rect
    cx = min(max(px, x), x + w)
    cy = min(max(py, y), y + h)
    if x <= px <= x + w and y <= py <= y + h:
        return 0.0
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def load(path: Path):
    root = ET.parse(path).getroot()
    shapes = {}
    edges = {}
    for el in root.iter():
        be = el.get("bpmnElement")
        if not be:
            continue
        tag = loc(el.tag)
        if tag == "BPMNShape":
            b = next(c for c in el if loc(c.tag) == "Bounds")
            shapes[be] = (
                float(b.get("x")),
                float(b.get("y")),
                float(b.get("width")),
                float(b.get("height")),
            )
        elif tag == "BPMNEdge":
            wps = [
                (float(c.get("x")), float(c.get("y")))
                for c in el
                if loc(c.tag) == "waypoint"
            ]
            edges[be] = wps
    return root, shapes, edges


def check_file(path: Path) -> list[str]:
    fails: list[str] = []
    try:
        root, shapes, edges = load(path)
    except ET.ParseError as e:
        return [f"{path.name}: XML parse error: {e}"]

    flows = []
    assocs = []
    nodes = {}
    for el in root.iter():
        tag = loc(el.tag)
        eid = el.get("id")
        if not eid:
            continue
        if tag == "sequenceFlow":
            flows.append(el)
        elif tag == "association":
            assocs.append(el)
        elif tag in NODE_TAGS:
            nodes[eid] = el
        if tag in ("lane", "laneSet"):
            fails.append(f"{path.name}: swimlane запрещён ({eid})")
        if tag == "exclusiveGateway" and el.get("default"):
            fails.append(
                f"{path.name}: default= на {eid} — маркер косой черты запрещён"
            )

    for el in flows + assocs:
        fid = el.get("id")
        if fid not in edges:
            fails.append(f"{path.name}: нет DI у потока {fid}")
        elif len(edges[fid]) < 2:
            fails.append(f"{path.name}: у {fid} меньше 2 waypoint")

    for eid, el in nodes.items():
        if eid not in shapes:
            fails.append(f"{path.name}: нет DI-shape у {eid}")

    for be in list(shapes) + list(edges):
        if be not in {el.get("id") for el in flows + assocs} and be not in nodes:
            # DI может ссылаться только на model elements — orphan
            if be not in nodes and all(el.get("id") != be for el in flows + assocs):
                fails.append(f"{path.name}: orphan DI bpmnElement={be}")

    for el in nodes.values():
        tag = loc(el.tag)
        if tag not in ACTIVITY_TAGS:
            continue
        ins = [c.text for c in el if loc(c.tag) == "incoming"]
        if len(ins) > 1:
            fails.append(
                f"{path.name}: у {el.get('id')} ({el.get('name')}) "
                f"{len(ins)} входящих — нужен XOR-merge"
            )

    for el in flows + assocs:
        fid = el.get("id")
        src = el.get("sourceRef")
        tgt = el.get("targetRef")
        wps = edges.get(fid)
        if not wps or len(wps) < 2:
            continue
        sr, tr = shapes.get(src), shapes.get(tgt)
        if not sr:
            fails.append(f"{path.name}: {fid}: нет shape у source {src}")
            continue
        if not tr:
            fails.append(f"{path.name}: {fid}: нет shape у target {tgt}")
            continue
        ds = dist_point_to_rect(wps[0][0], wps[0][1], sr)
        dt = dist_point_to_rect(wps[-1][0], wps[-1][1], tr)
        if ds > ATTACH_TOL:
            fails.append(
                f"{path.name}: поток в воздухе {fid}: start→{src} dist={ds:.1f}px "
                f"(start={wps[0]})"
            )
        if dt > ATTACH_TOL:
            fails.append(
                f"{path.name}: поток в воздухе {fid}: end→{tgt} dist={dt:.1f}px "
                f"(end={wps[-1]})"
            )

    # boundary не по центру низа host subprocess
    for el in root.iter():
        if loc(el.tag) != "boundaryEvent":
            continue
        bid = el.get("id")
        host = el.get("attachedToRef")
        br, hr = shapes.get(bid), shapes.get(host)
        if not br or not hr:
            continue
        bx, by, bw, bh = br
        hx, hy, hw, hh = hr
        bcx, bcy = bx + bw / 2, by + bh / 2
        host_bottom_y = hy + hh
        host_cx = hx + hw / 2
        on_bottom = abs(bcy - host_bottom_y) <= 24
        near_center_x = abs(bcx - host_cx) <= hw * 0.18
        if on_bottom and near_center_x:
            fails.append(
                f"{path.name}: boundary {bid} на центре низа {host} (зона [+])"
            )

    for el in root.iter():
        tag = loc(el.tag)
        text = ""
        if tag in NODE_TAGS:
            text = (el.get("name") or "") + " "
        if tag == "text":
            text = (el.text or "") + " "
        if tag == "textAnnotation":
            for c in el:
                if loc(c.tag) == "text" and c.text:
                    text += c.text + " "
        low = text.lower()
        for bad in FORBIDDEN_NAME:
            if bad in low:
                fails.append(
                    f"{path.name}: техжаргон «{bad}» в подписи "
                    f"({el.get('id') or tag}): {text.strip()[:80]}"
                )
                break

    # Длинный крюк влево за оба конца — «поток под первичным приёмом»
    for el in flows:
        fid = el.get("id")
        src_id, tgt_id = el.get("sourceRef"), el.get("targetRef")
        wps = edges.get(fid) or []
        sr, tr = shapes.get(src_id), shapes.get(tgt_id)
        if not wps or not sr or not tr:
            continue
        min_end = min(sr[0], tr[0])
        for i, (px, py) in enumerate(wps):
            if px < min_end - 80:
                fails.append(
                    f"{path.name}: {fid}: waypoint[{i}] уходит влево "
                    f"на {min_end - px:.0f}px за source/target (паутина)"
                )
                break

    # Процессы ведения care_*: никаких конкретных диагнозов в name
    if "care-" in path.name:
        for el in root.iter():
            name = (el.get("name") or "").lower()
            if not name:
                continue
            for bad in DIAGNOSIS_WORDS_CARE:
                if bad in name:
                    fails.append(
                        f"{path.name}: в процессе ведения запрещён диагноз «{bad}» "
                        f"в name у {el.get('id')}: {el.get('name')}"
                    )
                    break


    return fails


def main() -> int:
    files = sorted(BPMN_DIR.glob("*mature*.bpmn"))
    if not files:
        print("FAIL: нет docs/bpmn/*mature*.bpmn")
        return 1
    all_fails: list[str] = []
    for path in files:
        fails = check_file(path)
        if fails:
            print(f"[FAIL] {path.name}: {len(fails)}")
            for f in fails:
                print(f"  - {f}")
            all_fails.extend(fails)
        else:
            print(f"[ OK ] {path.name}")
    print()
    if all_fails:
        print(f"Итого FAIL: {len(all_fails)}")
        return 1
    print(f"Итого OK: {len(files)} файлов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
