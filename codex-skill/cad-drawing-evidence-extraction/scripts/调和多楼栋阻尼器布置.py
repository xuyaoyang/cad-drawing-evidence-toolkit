#!/usr/bin/env python3
"""调和同一 DWG 内多楼栋、不同符号表达方式的阻尼器布置数量。

输入均为只读导出：
- V5：楼层布置图标题与楼栋图签文字；
- V6：根容器、嵌套实例路径及定义文字；
- V7：逐个 VFD 文字的世界方向；
- V10：圆、填充、直线、多段线的世界几何。

本脚本不会修改 DWG。它只输出设计布置数量候选，不形成供货或生产放行结论。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable


FLOOR_TEXT = r"(?:地下[一二三四五六七八九十百\d]+层|负[一二三四五六七八九十百\d]+层|[一二三四五六七八九十百\d]+(?:[~～至—\-][一二三四五六七八九十百\d]+)?层|屋面层|机房层)"
PLAN_TITLE = re.compile(
    rf"(?P<floor>{FLOOR_TEXT}).{{0,16}}(?:阻尼器|减震器|消能器|耗能器)平面(?:布置)?图",
    re.IGNORECASE,
)
BUILDING_LABEL = re.compile(
    r"(?P<building>\d+#[^#，,；;（）()]{0,28}?(?:综合楼|医疗楼|门诊|住院楼|办公楼|教学楼|业务楼))"
)
EXACT_MARKER = re.compile(r"^(?:L?VFD|F?BRB|MYD|XNQD|VAD)$", re.IGNORECASE)
CHINESE_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


@dataclass
class Plan:
    building: str
    floor: str
    title: str
    x: float
    y: float
    handle: str
    layer: str
    building_handle: str
    building_layer: str
    building_distance: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-json", type=Path, required=True)
    parser.add_argument("--symbols-json", type=Path, required=True)
    parser.add_argument("--oriented-texts", type=Path, required=True)
    parser.add_argument("--primitive-geometry", type=Path, required=True)
    parser.add_argument("--existing-floor-layout", type=Path)
    parser.add_argument("--source-matrix", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", default="多楼栋阻尼器布置")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\\[A-Za-z]+(?:\d+(?:\.\d+)?)?;", "", text)
    text = re.sub(r"\\[^;{}]*;", "", text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", "", text)


def floor_number(value: str) -> int:
    token = value.replace("层", "")
    token = re.split(r"[~～至—\-]", token)[0]
    if token.isdigit():
        return int(token)
    if token.startswith("地下") or token.startswith("负"):
        inner = token.replace("地下", "").replace("负", "")
        return -chinese_number(inner)
    return chinese_number(token)


def chinese_number(value: str) -> int:
    if value in CHINESE_DIGITS:
        return CHINESE_DIGITS[value]
    if "十" not in value:
        return 999
    left, _, right = value.partition("十")
    tens = CHINESE_DIGITS.get(left, 1) if left else 1
    ones = CHINESE_DIGITS.get(right, 0) if right else 0
    return tens * 10 + ones


def distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def record_center(record: dict[str, Any]) -> tuple[float, float]:
    if record.get("bounds_valid"):
        return (
            (float(record.get("min_x") or 0.0) + float(record.get("max_x") or 0.0)) / 2.0,
            (float(record.get("min_y") or 0.0) + float(record.get("max_y") or 0.0)) / 2.0,
        )
    return float(record.get("x") or 0.0), float(record.get("y") or 0.0)


def read_plans(text_records: list[dict[str, Any]]) -> list[Plan]:
    titles: list[dict[str, Any]] = []
    labels: list[dict[str, Any]] = []
    seen_titles: set[tuple[str, int, int]] = set()
    seen_labels: set[tuple[str, int, int]] = set()

    for record in text_records:
        text = normalize_text(record.get("text"))
        title_match = PLAN_TITLE.search(text)
        if title_match:
            key = (
                title_match.group("floor"),
                round(float(record.get("x") or 0.0)),
                round(float(record.get("y") or 0.0)),
            )
            if key not in seen_titles:
                seen_titles.add(key)
                titles.append(
                    {
                        "floor": title_match.group("floor"),
                        "title": text,
                        "x": float(record.get("x") or 0.0),
                        "y": float(record.get("y") or 0.0),
                        "handle": str(record.get("handle") or ""),
                        "layer": str(record.get("layer") or ""),
                    }
                )

        building_match = BUILDING_LABEL.search(text)
        if building_match:
            building = building_match.group("building")
            key = (
                building,
                round(float(record.get("x") or 0.0)),
                round(float(record.get("y") or 0.0)),
            )
            if key not in seen_labels:
                seen_labels.add(key)
                labels.append(
                    {
                        "building": building,
                        "x": float(record.get("x") or 0.0),
                        "y": float(record.get("y") or 0.0),
                        "handle": str(record.get("handle") or ""),
                        "layer": str(record.get("layer") or ""),
                    }
                )

    candidates: list[Plan] = []
    for title in titles:
        if not labels:
            continue
        label = min(
            labels,
            key=lambda item: distance(
                item["x"], item["y"], title["x"], title["y"]
            ),
        )
        candidates.append(
            Plan(
                building=label["building"],
                floor=title["floor"],
                title=title["title"],
                x=title["x"],
                y=title["y"],
                handle=title["handle"],
                layer=title["layer"],
                building_handle=label["handle"],
                building_layer=label["layer"],
                building_distance=distance(
                    label["x"], label["y"], title["x"], title["y"]
                ),
            )
        )

    # 同一图纸中常同时出现图内标题和图签标题。若标题与其最近的楼栋
    # 标签位于同一图层，优先把它视为图签标题；几何归属优先使用位于
    # 其他图层的图内标题。该判断只利用当前图的结构关系，不写死图层名。
    canonical: dict[tuple[str, str], Plan] = {}
    for plan in candidates:
        key = (plan.building, plan.floor)
        current = canonical.get(key)
        plan_is_title_block = (
            bool(plan.layer)
            and bool(plan.building_layer)
            and plan.layer.casefold() == plan.building_layer.casefold()
        )
        current_is_title_block = (
            bool(current.layer)
            and bool(current.building_layer)
            and current.layer.casefold() == current.building_layer.casefold()
            if current is not None
            else True
        )
        if (
            current is None
            or (current_is_title_block and not plan_is_title_block)
            or (
                current_is_title_block == plan_is_title_block
                and plan.building_distance < current.building_distance
            )
        ):
            canonical[key] = plan
    return sorted(
        canonical.values(),
        key=lambda item: (item.building, floor_number(item.floor), item.y, item.x),
    )


def assign_building(
    x: float, y: float, plans: list[Plan]
) -> str:
    plan = min(plans, key=lambda item: distance(x, y, item.x, item.y))
    return plan.building


def exact_marker_count(record: dict[str, Any]) -> int:
    values = record.get("definition_texts")
    if not isinstance(values, list):
        return 0
    return sum(bool(EXACT_MARKER.fullmatch(normalize_text(value))) for value in values)


def marker_direction(record: dict[str, Any]) -> str:
    axis_x = float(record.get("world_axis_x") or 0.0)
    axis_y = float(record.get("world_axis_y") or 0.0)
    return "X" if abs(axis_x) >= abs(axis_y) else "Y"


def read_marker_directions(
    oriented_records: list[dict[str, Any]],
) -> dict[str, Counter[str]]:
    result: dict[str, Counter[str]] = defaultdict(Counter)
    for record in oriented_records:
        if not EXACT_MARKER.fullmatch(normalize_text(record.get("text"))):
            continue
        record_key = str(record.get("record_key") or "")
        root_handle = record_key.split("/", 1)[0]
        if root_handle == "direct":
            continue
        result[root_handle][marker_direction(record)] += 1
    return result


def read_marker_containers(
    symbol_records: list[dict[str, Any]],
    oriented_records: list[dict[str, Any]],
    plans: list[Plan],
) -> list[dict[str, Any]]:
    roots = [
        record
        for record in symbol_records
        if not str(record.get("parent_instance_key") or "")
    ]
    children_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in symbol_records:
        if record.get("parent_instance_key"):
            children_by_root[str(record.get("root_instance_handle") or "")].append(record)

    directions_by_root = read_marker_directions(oriented_records)
    raw: list[dict[str, Any]] = []
    for root in roots:
        root_handle = str(root.get("instance_handle") or "")
        marker_children = [
            child
            for child in children_by_root.get(root_handle, [])
            if exact_marker_count(child) > 0
        ]
        direct_count = exact_marker_count(root)
        marker_count = len(marker_children) + direct_count
        if marker_count == 0:
            continue

        x, y = record_center(root)
        building = assign_building(x, y, plans)
        child_centers = tuple(
            sorted(
                (
                    round(record_center(child)[0], 1),
                    round(record_center(child)[1], 1),
                )
                for child in marker_children
            )
        )
        if child_centers:
            fingerprint: Any = child_centers
        else:
            fingerprint = (
                round(float(root.get("min_x") or x), 1),
                round(float(root.get("min_y") or y), 1),
                round(float(root.get("max_x") or x), 1),
                round(float(root.get("max_y") or y), 1),
                marker_count,
            )
        direction_counts = directions_by_root.get(root_handle, Counter())
        raw.append(
            {
                "building": building,
                "floor": "",
                "root_handle": root_handle,
                "definition_handle": str(root.get("definition_handle") or ""),
                "block_name": str(root.get("block_name") or ""),
                "marker_count": marker_count,
                "x_quantity": direction_counts.get("X", 0),
                "y_quantity": direction_counts.get("Y", 0),
                "center_x": x,
                "center_y": y,
                "fingerprint": fingerprint,
                "duplicate_root_handles": [root_handle],
            }
        )

    # 只有世界坐标子实例指纹相同才视为重叠展示；同一块定义在同一楼层
    # 的两个不同翼区仍会保留为两个容器。
    deduplicated: list[dict[str, Any]] = []
    by_fingerprint: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for row in raw:
        key = (row["building"], row["definition_handle"], row["fingerprint"])
        existing = by_fingerprint.get(key)
        if existing is not None:
            existing["duplicate_root_handles"].append(row["root_handle"])
            continue
        by_fingerprint[key] = row
        deduplicated.append(row)

    # 一个楼栋的唯一容器数和楼层图数相等时，按 CAD 中纵向展示顺序
    # 一一对应，避免容器位于两张标题中线附近时被“最近标题”误分楼层。
    rows_by_building: dict[str, list[dict[str, Any]]] = defaultdict(list)
    plans_by_building: dict[str, list[Plan]] = defaultdict(list)
    for row in deduplicated:
        rows_by_building[row["building"]].append(row)
    for plan in plans:
        plans_by_building[plan.building].append(plan)

    for building, rows in rows_by_building.items():
        building_plans = plans_by_building.get(building, [])
        rows.sort(key=lambda item: item["center_y"], reverse=True)
        building_plans.sort(key=lambda item: item.y, reverse=True)
        if len(rows) == len(building_plans) and rows:
            for row, plan in zip(rows, building_plans):
                row["floor"] = plan.floor
                row["assignment_basis"] = (
                    f"楼栋内{len(rows)}个唯一容器与{len(building_plans)}张楼层布置图"
                    "按纵向顺序一一对应"
                )
                row["title_handle"] = plan.handle
                row["building_handle"] = plan.building_handle
        else:
            for row in rows:
                plan = min(
                    building_plans,
                    key=lambda item: distance(
                        row["center_x"], row["center_y"], item.x, item.y
                    ),
                )
                row["floor"] = plan.floor
                row["assignment_basis"] = "容器中心与同楼栋楼层标题的二维最近邻"
                row["title_handle"] = plan.handle
                row["building_handle"] = plan.building_handle
    return deduplicated


def same_geometry_bounds(
    first: dict[str, Any], second: dict[str, Any], tolerance: float
) -> bool:
    return (
        abs(float(first["center_x"]) - float(second["center_x"])) <= tolerance
        and abs(float(first["center_y"]) - float(second["center_y"])) <= tolerance
        and abs(float(first["width"]) - float(second["width"])) <= tolerance
        and abs(float(first["height"]) - float(second["height"])) <= tolerance
    )


def find_crossed_rectangles(
    primitive_records: list[dict[str, Any]],
    containers: list[dict[str, Any]],
    plans: list[Plan],
) -> list[dict[str, Any]]:
    lines_by_context: dict[tuple[str, str, str, str], list[dict[str, Any]]] = (
        defaultdict(list)
    )
    for record in primitive_records:
        if record.get("entity_type") != "Line" or not record.get("bounds_valid"):
            continue
        context = (
            str(record.get("origin") or ""),
            str(record.get("root_instance_handle") or ""),
            str(record.get("block_path") or ""),
            str(record.get("layer") or ""),
        )
        lines_by_context[context].append(record)

    candidates: list[dict[str, Any]] = []
    for polyline in primitive_records:
        if (
            polyline.get("entity_type") != "Polyline"
            or not polyline.get("closed")
            or int(polyline.get("vertex_count") or 0) != 4
            or not polyline.get("bounds_valid")
        ):
            continue
        width = float(polyline.get("width") or 0.0)
        height = float(polyline.get("height") or 0.0)
        major = max(width, height)
        minor = min(width, height)
        if minor <= 0.0 or not 7.0 <= major / minor <= 13.0:
            continue

        context = (
            str(polyline.get("origin") or ""),
            str(polyline.get("root_instance_handle") or ""),
            str(polyline.get("block_path") or ""),
            str(polyline.get("layer") or ""),
        )
        tolerance = max(2.0, minor * 0.02)
        matching_lines = [
            line
            for line in lines_by_context.get(context, [])
            if same_geometry_bounds(polyline, line, tolerance)
        ]
        slopes: list[float] = []
        for line in matching_lines:
            delta_x = float(line.get("end_x") or 0.0) - float(
                line.get("start_x") or 0.0
            )
            delta_y = float(line.get("end_y") or 0.0) - float(
                line.get("start_y") or 0.0
            )
            if abs(delta_x) < 1e-12:
                continue
            slopes.append(delta_y / delta_x)
        if not (any(slope > 0.0 for slope in slopes) and any(slope < 0.0 for slope in slopes)):
            continue

        x = float(polyline["center_x"])
        y = float(polyline["center_y"])
        building = assign_building(x, y, plans)
        building_plans = [plan for plan in plans if plan.building == building]
        plan = min(
            building_plans,
            key=lambda item: distance(x, y, item.x, item.y),
        )
        candidates.append(
            {
                "record_key": str(polyline.get("record_key") or ""),
                "handle": str(polyline.get("handle") or ""),
                "root_handle": str(polyline.get("root_instance_handle") or ""),
                "origin": str(polyline.get("origin") or ""),
                "block_path": str(polyline.get("block_path") or ""),
                "layer": str(polyline.get("layer") or ""),
                "building": building,
                "floor": plan.floor,
                "title_handle": plan.handle,
                "building_handle": plan.building_handle,
                "center_x": x,
                "center_y": y,
                "width": width,
                "height": height,
                "direction": "X" if width > height else "Y",
                "diagonal_line_handles": ";".join(
                    sorted(str(line.get("handle") or "") for line in matching_lines)
                ),
            }
        )

    container_roots = {row["root_handle"] for row in containers}
    seed_candidates = [
        row for row in candidates if row["root_handle"] in container_roots
    ]
    if not seed_candidates:
        return []
    template_major = median(
        max(row["width"], row["height"]) for row in seed_candidates
    )
    template_minor = median(
        min(row["width"], row["height"]) for row in seed_candidates
    )

    accepted: list[dict[str, Any]] = []
    for row in candidates:
        major = max(row["width"], row["height"])
        minor = min(row["width"], row["height"])
        major_error = abs(major - template_major) / template_major
        minor_error = abs(minor - template_minor) / template_minor
        if major_error > 0.02 or minor_error > 0.02:
            continue
        row["template_major"] = template_major
        row["template_minor"] = template_minor
        row["major_relative_error"] = major_error
        row["minor_relative_error"] = minor_error
        row["seed_status"] = (
            "text_labeled_seed"
            if row["root_handle"] in container_roots
            else "unlabeled_geometry_match"
        )
        accepted.append(row)
    return accepted


def reconcile_plans(
    plans: list[Plan],
    containers: list[dict[str, Any]],
    geometry: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    containers_by_plan: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    geometry_by_plan: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in containers:
        containers_by_plan[(row["building"], row["floor"])].append(row)
    for row in geometry:
        geometry_by_plan[(row["building"], row["floor"])].append(row)

    rows: list[dict[str, Any]] = []
    for plan in plans:
        key = (plan.building, plan.floor)
        container_rows = containers_by_plan.get(key, [])
        geometry_rows = geometry_by_plan.get(key, [])
        container_count = sum(row["marker_count"] for row in container_rows)
        container_x = sum(row["x_quantity"] for row in container_rows)
        container_y = sum(row["y_quantity"] for row in container_rows)
        geometry_count = len(geometry_rows)
        geometry_x = sum(row["direction"] == "X" for row in geometry_rows)
        geometry_y = sum(row["direction"] == "Y" for row in geometry_rows)

        if container_count and geometry_count:
            if (
                container_count == geometry_count
                and container_x == geometry_x
                and container_y == geometry_y
            ):
                status = "text_and_geometry_consistent"
                selected = geometry_count
                selected_x = geometry_x
                selected_y = geometry_y
            else:
                status = "text_geometry_conflict"
                selected = 0
                selected_x = 0
                selected_y = 0
        elif container_count:
            status = "text_instance_consistent"
            selected = container_count
            selected_x = container_x
            selected_y = container_y
        elif geometry_count:
            status = "seeded_unlabeled_geometry_consistent"
            selected = geometry_count
            selected_x = geometry_x
            selected_y = geometry_y
        else:
            continue

        rows.append(
            {
                "building": plan.building,
                "floor": plan.floor,
                "status": status,
                "selected_quantity": selected,
                "x_quantity": selected_x,
                "y_quantity": selected_y,
                "container_quantity": container_count,
                "geometry_quantity": geometry_count,
                "container_root_handles": ";".join(
                    sorted(row["root_handle"] for row in container_rows)
                ),
                "duplicate_root_handles": ";".join(
                    sorted(
                        handle
                        for row in container_rows
                        for handle in row["duplicate_root_handles"]
                        if handle != row["root_handle"]
                    )
                ),
                "geometry_handles": ";".join(
                    sorted(row["handle"] for row in geometry_rows)
                ),
                "title_handle": plan.handle,
                "building_handle": plan.building_handle,
                "assignment_basis": ";".join(
                    sorted(
                        {
                            str(row.get("assignment_basis") or "")
                            for row in container_rows
                            if row.get("assignment_basis")
                        }
                    )
                )
                or "几何候选与同楼栋楼层标题二维最近邻",
            }
        )
    return sorted(
        rows,
        key=lambda row: (row["building"], floor_number(row["floor"])),
    )


def read_existing_floor_layout(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "building": "1#门急诊医技住院综合楼",
                "floor": row.get("floor", ""),
                "status": row.get("status", ""),
                "selected_quantity": int(row.get("expanded_quantity_candidate") or 0),
                "x_quantity": int(row.get("expanded_x_quantity") or 0),
                "y_quantity": int(row.get("expanded_y_quantity") or 0),
                "container_quantity": int(row.get("placement_count") or 0),
                "geometry_quantity": 0,
                "container_root_handles": row.get("canonical_root_handle", ""),
                "duplicate_root_handles": "",
                "geometry_handles": "",
                "title_handle": row.get("title_handle", ""),
                "building_handle": "",
                "assignment_basis": row.get("dedupe_basis", ""),
            }
        )
    return result


def expected_quantities(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result: dict[str, int] = {}
    for row in rows:
        source = str(row.get("来源") or "")
        building = str(row.get("楼栋/范围") or "")
        quantity = str(row.get("数量") or "").strip()
        if "最新深化候选" not in source or not quantity.isdigit():
            continue
        match = BUILDING_LABEL.search(building)
        if match:
            result[match.group("building")] = int(quantity)
    return result


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[list[str]]) -> str:
    return "\n".join(
        [
            "| " + " | ".join(rows[0]) + " |",
            "| " + " | ".join("---" for _ in rows[0]) + " |",
            *["| " + " | ".join(row) + " |" for row in rows[1:]],
        ]
    )


def write_report(
    path: Path,
    drawing: str,
    rows: list[dict[str, Any]],
    geometry: list[dict[str, Any]],
    expected: dict[str, int],
    primitive_meta: dict[str, Any],
) -> None:
    totals: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        totals[row["building"]]["quantity"] += int(row["selected_quantity"])
        totals[row["building"]]["X"] += int(row["x_quantity"])
        totals[row["building"]]["Y"] += int(row["y_quantity"])

    table = [["楼栋", "楼层", "证据状态", "数量", "X向", "Y向", "证据句柄"]]
    for row in rows:
        evidence = "；".join(
            value
            for value in [
                f"容器 {row['container_root_handles']}" if row["container_root_handles"] else "",
                f"几何 {row['geometry_handles']}" if row["geometry_handles"] else "",
                f"标题 {row['title_handle']}",
            ]
            if value
        )
        table.append(
            [
                row["building"],
                row["floor"],
                row["status"],
                str(row["selected_quantity"]),
                str(row["x_quantity"]),
                str(row["y_quantity"]),
                evidence,
            ]
        )

    total_table = [["楼栋", "自动布置候选", "X向", "Y向", "深化表", "调和状态"]]
    for building, values in sorted(totals.items()):
        expected_quantity = expected.get(building)
        total_table.append(
            [
                building,
                str(values["quantity"]),
                str(values["X"]),
                str(values["Y"]),
                str(expected_quantity) if expected_quantity is not None else "未输入",
                (
                    "layout_and_table_consistent"
                    if expected_quantity == values["quantity"]
                    else "table_not_available_or_mismatch"
                ),
            ]
        )

    unlabeled = [
        row for row in geometry if row["seed_status"] == "unlabeled_geometry_match"
    ]
    duplicate_rows = [
        row for row in rows if str(row.get("duplicate_root_handles") or "")
    ]
    unlabeled_groups: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in unlabeled:
        group = unlabeled_groups[(row["building"], row["floor"])]
        group["quantity"] += 1
        group[row["direction"]] += 1
    template_major = median(
        [float(row["template_major"]) for row in geometry]
    ) if geometry else 0.0
    template_minor = median(
        [float(row["template_minor"]) for row in geometry]
    ) if geometry else 0.0
    evidence_lines: list[str] = []
    if duplicate_rows:
        duplicate_text = "；".join(
            f"{row['building']}{row['floor']}排除重复根句柄 {row['duplicate_root_handles']}"
            for row in duplicate_rows
        )
        evidence_lines.append(
            f"- 完全重叠副本：按世界子实例坐标指纹去重；{duplicate_text}。"
            "定义相同但世界位置不同的实例仍分别保留。\n"
        )
    if unlabeled_groups:
        group_text = "；".join(
            f"{building}{floor}命中{values['quantity']}个"
            f"（X向{values['X']}、Y向{values['Y']}）"
            for (building, floor), values in sorted(unlabeled_groups.items())
        )
        evidence_lines.append(
            "- 非文字化符号：从同图带设备文字的实例建立几何种子，"
            f"种子长短边中位数约 {template_major:g}×{template_minor:g}；"
            f"{group_text}。方向取矩形长轴，并由带文字样本验证。\n"
        )
    if not evidence_lines:
        evidence_lines.append("- 本次没有需要使用的重叠去重或非文字化几何补充证据。\n")
    text = [
        "# 多楼栋阻尼器布置数量闭合\n\n",
        "## 输入与方法\n\n",
        f"- 原图临时副本：`{drawing}`；原始 OneDrive DWG 未修改、未保存。\n",
        "- 导出通道：中望 CAD API V5/V6/V7/V10。\n",
        f"- V10 几何记录：{primitive_meta.get('primitive_record_count', 0)}；",
        f"范围不可用 {primitive_meta.get('bounds_unavailable_count', 0)}；",
        f"跳过失效/特殊对象 {primitive_meta.get('skipped_object_error_count', 0)}。\n",
        "- 数量口径：同一楼层中，文字实例和几何指纹同时命中时只计一次。\n\n",
        "## 逐层结果\n\n",
        markdown_table(table),
        "\n\n## 楼栋调和\n\n",
        markdown_table(total_table),
        "\n\n## 关键自动识别证据\n\n",
        *evidence_lines,
        "\n",
        "## 边界与结论\n\n",
        "- 本结果是设计布置数量候选，不是合同最终数量、实际供货数量或生产放行数量。\n",
        "- 本脚本不自动解决合同、设计变更和性能参数冲突；存在未关闭的生产相关冲突时必须保持 `not_issued`。\n",
        "- 若其他项目没有可标记的几何种子，或图形相似候选不能由楼层/楼栋/参数表闭合，算法必须安全停止。\n",
    ]
    path.write_text("".join(text), encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    text_source = read_json(args.text_json)
    symbol_source = read_json(args.symbols_json)
    oriented_source = read_json(args.oriented_texts)
    primitive_source = read_json(args.primitive_geometry)

    plans = read_plans(text_source["records"])
    containers = read_marker_containers(
        symbol_source["records"], oriented_source["records"], plans
    )
    geometry = find_crossed_rectangles(
        primitive_source["records"], containers, plans
    )
    rows = read_existing_floor_layout(args.existing_floor_layout)
    rows.extend(reconcile_plans(plans, containers, geometry))
    expected = expected_quantities(args.source_matrix)

    row_fields = [
        "building",
        "floor",
        "status",
        "selected_quantity",
        "x_quantity",
        "y_quantity",
        "container_quantity",
        "geometry_quantity",
        "container_root_handles",
        "duplicate_root_handles",
        "geometry_handles",
        "title_handle",
        "building_handle",
        "assignment_basis",
    ]
    geometry_fields = [
        "building",
        "floor",
        "seed_status",
        "direction",
        "handle",
        "record_key",
        "root_handle",
        "origin",
        "block_path",
        "layer",
        "center_x",
        "center_y",
        "width",
        "height",
        "diagonal_line_handles",
        "template_major",
        "template_minor",
        "major_relative_error",
        "minor_relative_error",
        "title_handle",
        "building_handle",
    ]
    rows_path = args.output_dir / f"{args.prefix}.多楼栋布置闭合.csv"
    geometry_path = args.output_dir / f"{args.prefix}.未标文字号几何证据.csv"
    report_path = args.output_dir / f"{args.prefix}.多楼栋数量闭合.md"
    write_csv(rows_path, rows, row_fields)
    write_csv(geometry_path, geometry, geometry_fields)
    write_report(
        report_path,
        str(text_source.get("drawing") or ""),
        rows,
        geometry,
        expected,
        primitive_source,
    )

    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[row["building"]] += int(row["selected_quantity"])
    print(
        json.dumps(
            {
                "plans": len(plans),
                "containers": len(containers),
                "geometry_matches": len(geometry),
                "building_totals": dict(totals),
                "rows_csv": str(rows_path),
                "geometry_csv": str(geometry_path),
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
