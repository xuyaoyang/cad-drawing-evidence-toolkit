#!/usr/bin/env python3
"""把 V19/V21/V22 定位结果整理为逐台可回查的几何证据与局部 SVG 预览。

脚本只读取 V19 输出、V12 清单、CSV 和 V10/V10.1 JSON，不打开或修改 DWG。
相同物理模板跨多个楼层只生成一张 SVG；逐台记录仍分别写入 CSV/JSON。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CORE_PATH = Path(__file__).with_name("跨视图阻尼器物理设备归一.py")
CORE_SPEC = importlib.util.spec_from_file_location("cad_v12_core_v23", CORE_PATH)
CORE = importlib.util.module_from_spec(CORE_SPEC)
assert CORE_SPEC.loader is not None
sys.modules[CORE_SPEC.name] = CORE
CORE_SPEC.loader.exec_module(CORE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从V19/V21/V22结果生成逐台几何定位证据和局部SVG抽查包"
    )
    parser.add_argument(
        "input_json",
        type=Path,
        help="V19跨DWG证据组.json，或直接的V12输入清单JSON",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--physical-device-csv",
        type=Path,
        default=None,
        help="输入为V12清单时对应的physical_device.csv",
    )
    parser.add_argument("--group-id", default="DIRECT-V12")
    parser.add_argument("--group-status", default="direct_v12_result")
    parser.add_argument(
        "--skip-v24",
        action="store_true",
        help="仅生成V23，不自动生成V24风险抽查包",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(
    path: Path, rows: list[dict[str, Any]], fields: list[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层不是对象：{path}")
    return value


def review_drawing_path(
    source: dict[str, Any], geometry_path: Path | None
) -> str:
    paths: list[Path] = []
    if geometry_path is not None:
        paths.append(geometry_path)
    for field in ("visibility_json", "primitive_geometry_json"):
        value = str(source.get(field) or "")
        if value:
            paths.append(Path(value))
    for path in dict.fromkeys(paths):
        if not path.is_file():
            continue
        try:
            drawing = str(load_json(path).get("drawing") or "")
        except (OSError, ValueError, json.JSONDecodeError):
            drawing = ""
        if drawing:
            return drawing
    return ""


def safe_float(value: Any) -> float | None:
    return CORE.optional_float(value)


def stable_preview_name(group_id: str, template_id: str) -> str:
    digest = hashlib.sha1(
        f"{group_id}|{template_id}".encode("utf-8")
    ).hexdigest()[:10]
    return f"{group_id}-{template_id}-{digest}.svg"


def same_location(
    expected: dict[str, Any],
    x_location: Any,
    y_location: Any,
    tolerance: float = 0.0002,
) -> tuple[bool, str]:
    pairs = (
        ("axis_x_low", x_location.low),
        ("axis_x_high", x_location.high),
        ("axis_y_low", y_location.low),
        ("axis_y_high", y_location.high),
    )
    for field, actual in pairs:
        if str(expected.get(field) or "") != str(actual):
            return False, f"{field}不一致"
    expected_x = safe_float(expected.get("axis_x_fraction"))
    expected_y = safe_float(expected.get("axis_y_fraction"))
    if expected_x is None or abs(expected_x - x_location.fraction) > tolerance:
        return False, "axis_x_fraction不一致"
    if expected_y is None or abs(expected_y - y_location.fraction) > tolerance:
        return False, "axis_y_fraction不一致"
    return True, ""


def scalar_label_handles(
    frame_rows: list[dict[str, str]],
    frame_id: str,
    building_id: str,
    dimension: str,
    label: str,
) -> list[str]:
    if label == "OUT":
        return []
    result: list[str] = []
    for row in frame_rows:
        if str(row.get("frame_id") or "").strip() != frame_id:
            continue
        match = CORE.AXIS_LABEL.fullmatch(str(row.get("text") or "").strip())
        if not match:
            continue
        building, row_label = match.groups()
        if CORE.normalize_building_id(building) != building_id:
            continue
        row_label = row_label.upper()
        if row_label != label:
            continue
        if ("x" if row_label.isdigit() else "y") != dimension:
            continue
        handle = str(row.get("handle") or "")
        if handle:
            result.append(handle)
    return sorted(set(result))


def enrich_scalar_evidence(
    evidence: dict[str, Any],
    frame_rows: list[dict[str, str]],
    frame_id: str,
    building_id: str,
    dimension: str,
) -> dict[str, Any]:
    result = dict(evidence)
    for key in ("low_boundary", "high_boundary"):
        boundary = dict(result.get(key) or {})
        label = str(boundary.get("label") or "")
        boundary["label_handles"] = scalar_label_handles(
            frame_rows, frame_id, building_id, dimension, label
        )
        result[key] = boundary
    return result


def boundary_nodes(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        node
        for key in (
            "low_boundary",
            "high_boundary",
            "outer_boundary",
            "spacing_reference_boundary",
        )
        if isinstance((node := evidence.get(key)), dict)
    ]


def evidence_complete(evidence: dict[str, Any] | None) -> tuple[bool, str]:
    if not evidence:
        return False, "缺定位证据对象"
    kind = str(evidence.get("kind") or "")
    nodes = boundary_nodes(evidence)
    if len(nodes) < 2:
        return False, "边界证据不足两条"
    for node in nodes:
        if not node.get("label_handles"):
            return False, f"轴号{node.get('label') or '?'}缺文字句柄"
        if kind.startswith("line_") and not node.get("geometry_record_keys"):
            return False, f"直轴{node.get('label') or '?'}缺轴线实体句柄"
        if kind.startswith("line_") and not node.get("geometry_match_distances"):
            return False, f"直轴{node.get('label') or '?'}缺轴线匹配距离"
        if kind.startswith("curved_axis"):
            if not node.get("arc_record_key"):
                return False, f"弧轴{node.get('label') or '?'}缺圆弧句柄"
            if not node.get("extension_record_key"):
                return False, f"弧轴{node.get('label') or '?'}缺切向延伸句柄"
            if safe_float(node.get("label_match_distance")) is None:
                return False, f"弧轴{node.get('label') or '?'}缺匹配距离"
    return True, ""


def lookup_candidate(
    candidate_rows: list[dict[str, str]], instance_key: str
) -> dict[str, str] | None:
    return next(
        (
            row
            for row in candidate_rows
            if str(row.get("instance_key") or "") == instance_key
        ),
        None,
    )


def evaluate_template(
    device: dict[str, str],
    source: dict[str, Any],
    frame_rows: list[dict[str, str]],
    geometry_systems: dict[str, Any],
    coordinate_systems: dict[str, Any],
) -> dict[str, Any]:
    x = safe_float(device.get("primary_world_x"))
    y = safe_float(device.get("primary_world_y"))
    frame_id = str(device.get("primary_frame_id") or "")
    building_id = str(device.get("building_id") or "")
    method = str(device.get("location_method") or "")
    if x is None or y is None:
        return {
            "status": "evidence_trace_unresolved",
            "reason": "主视图世界坐标缺失",
        }

    x_location = None
    y_location = None
    if method == "building_axis_grid_geometry":
        system = geometry_systems.get(frame_id, {}).get(building_id)
        if system is None:
            return {
                "status": "evidence_trace_unresolved",
                "reason": "无法重建V22几何轴网",
            }
        x_location = CORE.locate_between_line_axes(x, y, system.x_lines)
        y_location = (
            CORE.locate_between_curved_axes(x, y, system.y_curves)
            if system.y_curves is not None
            else CORE.locate_between_line_axes(x, y, system.y_lines)
        )
    elif method == "building_axis_grid":
        system = coordinate_systems.get(frame_id, {}).get(building_id)
        if system is None:
            return {
                "status": "evidence_trace_unresolved",
                "reason": "无法重建V21正交轴网",
            }
        x_location = CORE.locate_between_axes(x, system["x"])
        y_location = CORE.locate_between_axes(y, system["y"])
        if x_location is not None:
            x_location = CORE.AxisLocation(
                x_location.low,
                x_location.high,
                x_location.fraction,
                enrich_scalar_evidence(
                    x_location.evidence or {},
                    frame_rows,
                    frame_id,
                    building_id,
                    "x",
                ),
            )
        if y_location is not None:
            y_location = CORE.AxisLocation(
                y_location.low,
                y_location.high,
                y_location.fraction,
                enrich_scalar_evidence(
                    y_location.evidence or {},
                    frame_rows,
                    frame_id,
                    building_id,
                    "y",
                ),
            )
    else:
        return {
            "status": "evidence_trace_unresolved",
            "reason": f"不支持的定位方法：{method or '(空)'}",
        }

    if x_location is None or y_location is None:
        return {
            "status": "evidence_trace_unresolved",
            "reason": "设备未唯一落入重建轴间",
        }
    consistent, mismatch = same_location(device, x_location, y_location)
    x_complete, x_reason = evidence_complete(x_location.evidence)
    y_complete, y_reason = evidence_complete(y_location.evidence)
    reasons = [
        value
        for value in (
            "" if consistent else mismatch,
            "" if x_complete else f"X向：{x_reason}",
            "" if y_complete else f"Y向：{y_reason}",
        )
        if value
    ]
    return {
        "status": (
            "evidence_trace_complete"
            if not reasons
            else "evidence_trace_unresolved"
        ),
        "reason": "；".join(reasons),
        "x_location": x_location,
        "y_location": y_location,
        "source": source,
    }


def collect_values(value: Any, key_names: set[str]) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in key_names:
                if isinstance(item, list):
                    result.extend(str(entry) for entry in item if entry)
                elif item:
                    result.append(str(item))
            result.extend(collect_values(item, key_names))
    elif isinstance(value, list):
        for item in value:
            result.extend(collect_values(item, key_names))
    return sorted(set(result))


def arc_points(node: dict[str, Any], count: int = 48) -> list[tuple[float, float]]:
    arc = node.get("arc") or {}
    center = node.get("_family_center") or {}
    cx = safe_float(center.get("x"))
    cy = safe_float(center.get("y"))
    sx = safe_float(arc.get("start_x"))
    sy = safe_float(arc.get("start_y"))
    mx = safe_float(arc.get("mid_x"))
    my = safe_float(arc.get("mid_y"))
    ex = safe_float(arc.get("end_x"))
    ey = safe_float(arc.get("end_y"))
    if None in (cx, cy, sx, sy, mx, my, ex, ey):
        return []
    start = math.atan2(sy - cy, sx - cx)
    mid = math.atan2(my - cy, mx - cx)
    end = math.atan2(ey - cy, ex - cx)
    ccw_span = CORE.ccw_delta(start, end)
    if CORE.ccw_delta(start, mid) <= ccw_span + math.radians(2):
        angles = [start + ccw_span * index / count for index in range(count + 1)]
    else:
        clockwise = CORE.ccw_delta(end, start)
        angles = [
            start - clockwise * index / count for index in range(count + 1)
        ]
    radius = safe_float(node.get("radius"))
    if radius is None:
        return []
    return [(cx + radius * math.cos(a), cy + radius * math.sin(a)) for a in angles]


def evidence_shapes(
    evidence: dict[str, Any], dimension: str
) -> list[dict[str, Any]]:
    shapes: list[dict[str, Any]] = []
    family_center = evidence.get("center") or {}
    tangent = evidence.get("tangent_vector") or {}
    for index, node in enumerate(boundary_nodes(evidence)):
        color = "#2563eb" if dimension == "x" else "#059669"
        line = node.get("line")
        if isinstance(line, dict):
            values = [safe_float(line.get(key)) for key in ("x1", "y1", "x2", "y2")]
            if all(value is not None for value in values):
                shapes.append(
                    {
                        "type": "infinite_line",
                        "values": values,
                        "color": color,
                        "label": str(node.get("label") or ""),
                    }
                )
        arc = node.get("arc")
        if isinstance(arc, dict):
            node = dict(node)
            node["_family_center"] = family_center
            points = arc_points(node)
            if points:
                shapes.append(
                    {
                        "type": "polyline",
                        "points": points,
                        "color": color,
                        "label": str(node.get("label") or ""),
                    }
                )
            tx = safe_float((node.get("transition") or {}).get("x"))
            ty = safe_float((node.get("transition") or {}).get("y"))
            tangent_x = safe_float(tangent.get("x"))
            tangent_y = safe_float(tangent.get("y"))
            s_min = safe_float(node.get("straight_s_min"))
            s_max = safe_float(node.get("straight_s_max"))
            if None not in (tx, ty, tangent_x, tangent_y, s_min, s_max):
                shapes.append(
                    {
                        "type": "line",
                        "values": [
                            tx + tangent_x * s_min,
                            ty + tangent_y * s_min,
                            tx + tangent_x * s_max,
                            ty + tangent_y * s_max,
                        ],
                        "color": color,
                        "label": str(node.get("label") or ""),
                    }
                )
        coordinate = safe_float(node.get("coordinate"))
        if coordinate is not None:
            shapes.append(
                {
                    "type": "coordinate_line",
                    "coordinate": coordinate,
                    "dimension": dimension,
                    "color": color,
                    "label": str(node.get("label") or ""),
                }
            )
        if index >= 1:
            continue
    return shapes


def render_svg(
    path: Path,
    device: dict[str, str],
    x_evidence: dict[str, Any],
    y_evidence: dict[str, Any],
    candidate: dict[str, str] | None,
) -> None:
    x = float(device["primary_world_x"])
    y = float(device["primary_world_y"])
    distances = [
        value
        for evidence in (x_evidence, y_evidence)
        for key in ("distance_to_low", "distance_to_high")
        if (value := safe_float(evidence.get(key))) is not None
    ]
    window = max(2000.0, min(25000.0, sum(distances) * 1.4 if distances else 6000.0))
    min_x, max_x = x - window, x + window
    min_y, max_y = y - window, y + window
    shapes = evidence_shapes(x_evidence, "x") + evidence_shapes(y_evidence, "y")

    width, height = 760.0, 620.0

    def sx(value: float) -> float:
        return (value - min_x) / (max_x - min_x) * width

    def sy(value: float) -> float:
        return height - (value - min_y) / (max_y - min_y) * height

    svg: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(width)}" '
        f'height="{int(height)}" viewBox="0 0 {int(width)} {int(height)}">',
        "<style>text{font-family:Arial,'Microsoft YaHei',sans-serif}"
        ".axis{fill:none;stroke-width:2}.label{font-size:16px;font-weight:700}"
        ".device{fill:#dc2626;stroke:white;stroke-width:2}</style>",
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
    ]
    for shape in shapes:
        color = shape["color"]
        label = html.escape(shape["label"])
        if shape["type"] in {"line", "infinite_line"}:
            x1, y1, x2, y2 = shape["values"]
            label_x, label_y = x1, y1
            if shape["type"] == "infinite_line":
                dx, dy = x2 - x1, y2 - y1
                length = math.hypot(dx, dy) or 1.0
                unit_x, unit_y = dx / length, dy / length
                projection = (x - x1) * unit_x + (y - y1) * unit_y
                label_x = x1 + projection * unit_x
                label_y = y1 + projection * unit_y
                center_x = (x1 + x2) / 2.0
                center_y = (y1 + y2) / 2.0
                x1 = center_x - unit_x * window * 2
                y1 = center_y - unit_y * window * 2
                x2 = center_x + unit_x * window * 2
                y2 = center_y + unit_y * window * 2
            svg.append(
                f'<line class="axis" x1="{sx(x1):.2f}" y1="{sy(y1):.2f}" '
                f'x2="{sx(x2):.2f}" y2="{sy(y2):.2f}" stroke="{color}"/>'
            )
            svg.append(
                f'<text class="label" x="{sx(label_x)+6:.2f}" '
                f'y="{sy(label_y)-6:.2f}" '
                f'fill="{color}">{label}</text>'
            )
        elif shape["type"] == "polyline":
            points = " ".join(
                f"{sx(px):.2f},{sy(py):.2f}" for px, py in shape["points"]
            )
            svg.append(
                f'<polyline class="axis" points="{points}" stroke="{color}"/>'
            )
            label_point = min(
                shape["points"],
                key=lambda point: math.hypot(point[0] - x, point[1] - y),
            )
            svg.append(
                f'<text class="label" x="{sx(label_point[0])+6:.2f}" '
                f'y="{sy(label_point[1])-6:.2f}" '
                f'fill="{color}">{label}</text>'
            )
        elif shape["type"] == "coordinate_line":
            coordinate = shape["coordinate"]
            if shape["dimension"] == "x":
                svg.append(
                    f'<line class="axis" x1="{sx(coordinate):.2f}" y1="0" '
                    f'x2="{sx(coordinate):.2f}" y2="{height}" stroke="{color}"/>'
                )
            else:
                svg.append(
                    f'<line class="axis" x1="0" y1="{sy(coordinate):.2f}" '
                    f'x2="{width}" y2="{sy(coordinate):.2f}" stroke="{color}"/>'
                )
    if candidate:
        values = [
            safe_float(candidate.get(key))
            for key in ("min_x", "min_y", "max_x", "max_y")
        ]
        if all(value is not None for value in values):
            cmin_x, cmin_y, cmax_x, cmax_y = values
            svg.append(
                f'<rect x="{sx(cmin_x):.2f}" y="{sy(cmax_y):.2f}" '
                f'width="{sx(cmax_x)-sx(cmin_x):.2f}" '
                f'height="{sy(cmin_y)-sy(cmax_y):.2f}" '
                'fill="#fca5a5" fill-opacity="0.25" stroke="#dc2626"/>'
            )
    svg.extend(
        [
            f'<circle class="device" cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="7"/>',
            f'<text x="{sx(x)+12:.2f}" y="{sy(y)-10:.2f}" fill="#991b1b" '
            f'font-size="15">{html.escape(device.get("physical_template_id") or "")}</text>',
            '<rect x="12" y="12" width="330" height="72" rx="8" '
            'fill="white" fill-opacity="0.9" stroke="#cbd5e1"/>',
            '<text x="26" y="38" font-size="15" fill="#2563eb">蓝：数字轴边界</text>',
            '<text x="26" y="61" font-size="15" fill="#059669">绿：字母轴边界</text>',
            '<text x="175" y="38" font-size="15" fill="#dc2626">红：阻尼器位置/范围</text>',
            f'<text x="175" y="61" font-size="13" fill="#334155">'
            f'{html.escape(device.get("axis_position_key") or "")}</text>',
            "</svg>",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def build_html(
    output_path: Path,
    templates: list[dict[str, Any]],
    device_count: int,
    unresolved_count: int,
) -> None:
    cards: list[str] = []
    for item in templates:
        floors = "、".join(item["floors"])
        status_class = "ok" if item["evidence_status"] == "evidence_trace_complete" else "bad"
        preview = html.escape(item.get("preview_relative") or "")
        image = (
            f'<a href="{preview}"><img src="{preview}" alt="局部证据预览"></a>'
            if preview
            else '<div class="missing">未生成预览</div>'
        )
        cards.append(
            "<article>"
            f"<h2>{html.escape(item['group_id'])} / "
            f"{html.escape(item['physical_template_id'])}</h2>"
            f'<p><span class="{status_class}">{html.escape(item["evidence_status"])}</span>'
            f"　楼栋 {html.escape(item['building_id'])}　楼层 {html.escape(floors)}</p>"
            f"<p>{html.escape(item['axis_position_key'])}</p>"
            f"<p class=\"reason\">{html.escape(item.get('evidence_reason') or '证据链完整')}</p>"
            f"{image}</article>"
        )
    output_path.write_text(
        """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>V23 阻尼器几何定位人工抽查包</title>
<style>
body{margin:0;background:#eef2f7;color:#172033;font:15px/1.55 Arial,"Microsoft YaHei",sans-serif}
header{padding:24px 32px;background:#172033;color:white}main{padding:24px 32px}
.summary{margin-bottom:20px;padding:16px;background:white;border-radius:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:18px}
article{background:white;border-radius:10px;padding:18px;box-shadow:0 2px 10px #0001}
article h2{margin:0 0 8px;font-size:19px}img{width:100%;border:1px solid #d7deea;background:#f8fafc}
.ok{color:#047857;font-weight:700}.bad{color:#b91c1c;font-weight:700}
.reason{color:#475569}.missing{padding:50px;text-align:center;background:#fee2e2;color:#991b1b}
</style></head><body>
"""
        + f"<header><h1>V23 阻尼器几何定位人工抽查包</h1>"
        f"<p>物理模板 {len(templates)}，逐台记录 {device_count}，未决 {unresolved_count}</p></header>"
        + "<main><section class=\"summary\"><strong>阅读方法：</strong>"
        "蓝线为数字轴边界，绿线为字母轴边界，红点/框为设备。"
        "SVG 是 API 世界坐标证据的局部表达，不是 CAD 原图打印效果。</section>"
        + '<section class="grid">'
        + "\n".join(cards)
        + "</section></main></body></html>\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    input_path = args.input_json.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir = output_dir / "previews"
    payload = load_json(input_path)
    groups = payload.get("groups")
    if not isinstance(groups, list):
        if args.physical_device_csv is None:
            raise ValueError(
                "输入不是V19组表；直接使用V12清单时必须传入"
                " --physical-device-csv"
            )
        direct_device_path = args.physical_device_csv.resolve()
        if not direct_device_path.is_file():
            raise FileNotFoundError(direct_device_path)
        primary_source_id = str(payload.get("primary_source_id") or "")
        primary_source = next(
            (
                value
                for value in payload.get("sources", [])
                if str(value.get("source_id") or "") == primary_source_id
            ),
            {},
        )
        groups = [
            {
                "group_id": args.group_id,
                "scope_key": str(payload.get("project_id") or args.group_id),
                "status": args.group_status,
                "reason": "",
                "manifest_path": str(input_path),
                "physical_device_csv": str(direct_device_path),
                "sources": [
                    {
                        "source_id": primary_source_id,
                        "source_path": str(
                            primary_source.get("candidate_csv") or input_path
                        ),
                    }
                ],
            }
        ]

    device_records: list[dict[str, Any]] = []
    template_records: list[dict[str, Any]] = []
    unresolved_groups: list[dict[str, str]] = []
    for group in groups:
        group_id = str(group.get("group_id") or "")
        manifest_value = str(group.get("manifest_path") or "")
        device_value = str(group.get("physical_device_csv") or "")
        if not manifest_value or not device_value:
            unresolved_groups.append(
                {
                    "group_id": group_id,
                    "status": str(group.get("status") or ""),
                    "reason": str(group.get("reason") or ""),
                }
            )
            continue
        manifest_path = Path(manifest_value)
        device_path = Path(device_value)
        if not manifest_path.is_file() or not device_path.is_file():
            unresolved_groups.append(
                {
                    "group_id": group_id,
                    "status": "v23_input_missing",
                    "reason": "V12清单或物理设备CSV不存在",
                }
            )
            continue
        manifest = load_json(manifest_path)
        primary_source_id = str(manifest.get("primary_source_id") or "")
        source = next(
            (
                value
                for value in manifest.get("sources", [])
                if str(value.get("source_id") or "") == primary_source_id
            ),
            None,
        )
        if not isinstance(source, dict):
            unresolved_groups.append(
                {
                    "group_id": group_id,
                    "status": "v23_primary_source_missing",
                    "reason": "清单缺主来源",
                }
            )
            continue
        frame_path = Path(str(source.get("frame_texts_csv") or ""))
        candidate_path = Path(str(source.get("candidate_csv") or ""))
        geometry_value = str(source.get("primitive_geometry_json") or "")
        geometry_path = Path(geometry_value) if geometry_value else None
        drawing_path = review_drawing_path(source, geometry_path)
        if not frame_path.is_file() or not candidate_path.is_file():
            unresolved_groups.append(
                {
                    "group_id": group_id,
                    "status": "v23_primary_evidence_missing",
                    "reason": "主来源图框文字或候选CSV不存在",
                }
            )
            continue
        frame_rows = read_csv(frame_path)
        candidate_rows = read_csv(candidate_path)
        devices = read_csv(device_path)
        geometry_systems = CORE.build_geometry_axis_systems(
            frame_path, geometry_path
        )
        coordinate_systems = CORE.build_axis_systems(frame_path)
        source_file = next(
            (
                str(value.get("source_path") or "")
                for value in group.get("sources", [])
                if str(value.get("source_id") or "") == primary_source_id
            ),
            "",
        )
        by_template: dict[str, list[dict[str, str]]] = defaultdict(list)
        for device in devices:
            by_template[str(device.get("physical_template_id") or "")].append(device)
        for template_id, template_devices in sorted(by_template.items()):
            representative = template_devices[0]
            evaluation = evaluate_template(
                representative,
                source,
                frame_rows,
                geometry_systems,
                coordinate_systems,
            )
            status = str(evaluation["status"])
            reason = str(evaluation.get("reason") or "")
            x_evidence = (
                evaluation["x_location"].evidence
                if evaluation.get("x_location") is not None
                else {}
            ) or {}
            y_evidence = (
                evaluation["y_location"].evidence
                if evaluation.get("y_location") is not None
                else {}
            ) or {}
            preview_relative = ""
            if evaluation.get("x_location") is not None and evaluation.get(
                "y_location"
            ) is not None:
                preview_name = stable_preview_name(group_id, template_id)
                preview_path = preview_dir / preview_name
                render_svg(
                    preview_path,
                    representative,
                    x_evidence,
                    y_evidence,
                    lookup_candidate(
                        candidate_rows,
                        str(representative.get("primary_instance_key") or ""),
                    ),
                )
                preview_relative = f"previews/{preview_name}"
            label_handles = collect_values(
                [x_evidence, y_evidence], {"label_handles"}
            )
            geometry_handles = collect_values(
                [x_evidence, y_evidence],
                {
                    "geometry_handles",
                    "arc_handle",
                    "extension_handle",
                },
            )
            geometry_record_keys = collect_values(
                [x_evidence, y_evidence],
                {
                    "geometry_record_keys",
                    "arc_record_key",
                    "extension_record_key",
                },
            )
            match_distances = collect_values(
                [x_evidence, y_evidence], {"label_match_distance"}
            )
            geometry_match_distances = collect_values(
                [x_evidence, y_evidence], {"geometry_match_distances"}
            )
            floors = sorted(
                {str(device.get("floor") or "") for device in template_devices}
            )
            template_records.append(
                {
                    "group_id": group_id,
                    "group_status": str(group.get("status") or ""),
                    "primary_source_id": primary_source_id,
                    "physical_template_id": template_id,
                    "building_id": str(representative.get("building_id") or ""),
                    "floors": floors,
                    "axis_position_key": str(
                        representative.get("axis_position_key") or ""
                    ),
                    "primary_instance_key": str(
                        representative.get("primary_instance_key") or ""
                    ),
                    "primary_world_x": str(
                        representative.get("primary_world_x") or ""
                    ),
                    "primary_world_y": str(
                        representative.get("primary_world_y") or ""
                    ),
                    "location_method": str(
                        representative.get("location_method") or ""
                    ),
                    "evidence_status": status,
                    "evidence_reason": reason,
                    "label_handles": label_handles,
                    "geometry_handles": geometry_handles,
                    "geometry_record_keys": geometry_record_keys,
                    "label_match_distances": match_distances,
                    "geometry_match_distances": geometry_match_distances,
                    "x_evidence": x_evidence,
                    "y_evidence": y_evidence,
                    "preview_relative": preview_relative,
                    "source_file": source_file,
                    "review_drawing_path": drawing_path,
                    "manifest_path": str(manifest_path),
                    "frame_texts_csv": str(frame_path),
                    "primitive_geometry_json": (
                        str(geometry_path) if geometry_path else ""
                    ),
                }
            )
            for device in template_devices:
                device_records.append(
                    {
                        "registry_device_id": (
                            f"DL-{group_id}-{device.get('physical_device_id') or ''}"
                        ),
                        "group_id": group_id,
                        "physical_device_id": str(
                            device.get("physical_device_id") or ""
                        ),
                        "physical_template_id": template_id,
                        "building_id": str(device.get("building_id") or ""),
                        "floor": str(device.get("floor") or ""),
                        "axis_position_key": str(
                            device.get("axis_position_key") or ""
                        ),
                        "primary_instance_key": str(
                            device.get("primary_instance_key") or ""
                        ),
                        "primary_world_x": str(
                            device.get("primary_world_x") or ""
                        ),
                        "primary_world_y": str(
                            device.get("primary_world_y") or ""
                        ),
                        "location_method": str(
                            device.get("location_method") or ""
                        ),
                        "evidence_status": status,
                        "evidence_reason": reason,
                        "label_handles": ";".join(label_handles),
                        "geometry_handles": ";".join(geometry_handles),
                        "geometry_record_keys": ";".join(geometry_record_keys),
                        "label_match_distances": ";".join(match_distances),
                        "geometry_match_distances": ";".join(
                            geometry_match_distances
                        ),
                        "preview_relative": preview_relative,
                        "source_file": source_file,
                    }
                )

    status_counts = Counter(
        str(row["evidence_status"]) for row in template_records
    )
    unresolved_template_count = sum(
        1
        for row in template_records
        if row["evidence_status"] != "evidence_trace_complete"
    )
    duplicate_device_id_count = len(device_records) - len(
        {str(row["registry_device_id"]) for row in device_records}
    )
    overall_status = (
        "v23_located_template_evidence_complete"
        if template_records
        and unresolved_template_count == 0
        and duplicate_device_id_count == 0
        else "v23_evidence_package_partial"
    )
    csv_fields = [
        "registry_device_id",
        "group_id",
        "physical_device_id",
        "physical_template_id",
        "building_id",
        "floor",
        "axis_position_key",
        "primary_instance_key",
        "primary_world_x",
        "primary_world_y",
        "location_method",
        "evidence_status",
        "evidence_reason",
        "label_handles",
        "geometry_handles",
        "geometry_record_keys",
        "label_match_distances",
        "geometry_match_distances",
        "preview_relative",
        "source_file",
    ]
    csv_path = output_dir / "V23逐台几何定位证据.csv"
    write_csv(csv_path, device_records, csv_fields)
    json_path = output_dir / "V23逐台几何定位证据.json"
    json_path.write_text(
        json.dumps(
            {
                "version": "V23",
                "status": overall_status,
                "source_input_json": str(input_path),
                "template_count": len(template_records),
                "device_count": len(device_records),
                "unresolved_template_count": unresolved_template_count,
                "duplicate_device_id_count": duplicate_device_id_count,
                "unresolved_group_count": len(unresolved_groups),
                "status_counts": dict(status_counts),
                "templates": template_records,
                "unresolved_groups": unresolved_groups,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    html_path = output_dir / "V23人工抽查索引.html"
    build_html(
        html_path,
        template_records,
        len(device_records),
        unresolved_template_count,
    )
    v24_output_dir = output_dir / "V24风险分层抽查"
    v24_result: dict[str, Any] = {
        "status": "v24_not_run",
        "output_dir": str(v24_output_dir),
    }
    v24_script = Path(__file__).with_name(
        "生成阻尼器风险抽查与中望回查包V24.py"
    )
    if not args.skip_v24:
        if not v24_script.is_file():
            v24_result = {
                "status": "v24_generator_missing",
                "output_dir": str(v24_output_dir),
            }
        else:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(v24_script),
                    str(json_path),
                    "--output-dir",
                    str(v24_output_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            parsed: dict[str, Any] | None = None
            for line in reversed(completed.stdout.splitlines()):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    parsed = value
                    break
            if completed.returncode == 0 and parsed is not None:
                v24_result = parsed
            else:
                v24_result = {
                    "status": "v24_generation_failed",
                    "exit_code": completed.returncode,
                    "stderr": completed.stderr.strip(),
                    "output_dir": str(v24_output_dir),
                }
    md_path = output_dir / "V23人工抽查说明.md"
    md_path.write_text(
        "\n".join(
            [
                "# V23 阻尼器几何定位人工抽查包",
                "",
                "## 结论",
                "",
                f"- 状态：`{overall_status}`。",
                f"- 物理模板：{len(template_records)}。",
                f"- 逐台设备记录：{len(device_records)}。",
                f"- 证据链未决模板：{unresolved_template_count}。",
                f"- 未进入逐台证据的V19组：{len(unresolved_groups)}。",
                f"- 设备ID重复：{duplicate_device_id_count}。",
                "",
                "## 输出",
                "",
                f"- 输入：`{input_path}`",
                f"- `{csv_path}`",
                f"- `{json_path}`",
                f"- `{html_path}`",
                f"- `{preview_dir}`",
                f"- V24状态：`{v24_result.get('status')}`；"
                f"目录：`{v24_output_dir}`",
                "",
                "## 边界",
                "",
                "- SVG由CAD API世界坐标、轴号文字句柄、直线/圆弧/切向延伸句柄生成，"
                "不是DWG原图渲染或打印效果。",
                "- 相同模板跨多个楼层共用一张预览；逐台CSV仍保留每个楼层设备。",
                "- `evidence_trace_complete`只表示定位证据可重算并可回查，"
                "不提升V19的数量、可见性、合同、供货或生产状态。",
                "- 缺句柄、重算不一致、主来源缺失或设备不再唯一落轴间时保持未决。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": overall_status,
                "template_count": len(template_records),
                "device_count": len(device_records),
                "unresolved_template_count": unresolved_template_count,
                "unresolved_group_count": len(unresolved_groups),
                "duplicate_device_id_count": duplicate_device_id_count,
                "csv": str(csv_path),
                "json": str(json_path),
                "html": str(html_path),
                "v24_status": v24_result.get("status"),
                "v24_output_dir": str(v24_output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
