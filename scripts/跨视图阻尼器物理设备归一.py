#!/usr/bin/env python3
"""把结构主平面和梁/柱/墙/板/深化图中的设备出现归一到物理模板位置。

脚本只消费已经导出的 CSV，不读取或修改 DWG。优先使用“楼栋号-轴号”文字
建立每个楼栋独立的轴网坐标；图框或轴网不可用时，可用语义父容器内的归一化
局部坐标匹配到主视图。数量表只用于把已经闭合的主视图模板展开到楼层，不会
用来补齐跨视图未识别的设备。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


AXIS_LABEL = re.compile(
    r"^(\d{1,3}(?:\s*[-－]\s*\d{1,3})*)\s*[-－]\s*"
    r"([0-9]{1,3}|[A-Za-z])$"
)
BUILDING_TOKEN = re.compile(
    r"(?<![\d\-－])(\d{1,3}(?:\s*[-－]\s*\d{1,3})*)\s*[#＃]"
)
FLOOR_TOKEN = re.compile(
    r"^(B?\d{1,3})F(?:\s*[~～]\s*(B?\d{1,3})F)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AxisLocation:
    low: str
    high: str
    fraction: float
    evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class AxisLine:
    label: str
    x1: float
    y1: float
    x2: float
    y2: float
    anchor_x: float
    anchor_y: float
    label_handles: tuple[str, ...] = ()
    geometry_record_keys: tuple[str, ...] = ()
    geometry_handles: tuple[str, ...] = ()
    geometry_match_distances: tuple[float, ...] = ()


@dataclass(frozen=True)
class CurvedAxis:
    label: str
    radius: float
    start_x: float
    start_y: float
    mid_x: float
    mid_y: float
    end_x: float
    end_y: float
    straight_s_min: float
    straight_s_max: float
    label_handles: tuple[str, ...] = ()
    label_match_distance: float = math.nan
    arc_record_key: str = ""
    arc_handle: str = ""
    extension_record_key: str = ""
    extension_handle: str = ""
    transition_x: float = math.nan
    transition_y: float = math.nan


@dataclass(frozen=True)
class CurvedAxisFamily:
    center_x: float
    center_y: float
    outward_x: float
    outward_y: float
    tangent_x: float
    tangent_y: float
    axes: dict[str, CurvedAxis]


@dataclass(frozen=True)
class GeometryAxisSystem:
    x_lines: dict[str, AxisLine]
    y_lines: dict[str, AxisLine]
    y_curves: CurvedAxisFamily | None


@dataclass
class SourceConfig:
    source_id: str
    view_type: str
    candidate_csv: Path
    frame_texts_csv: Path | None
    primitive_geometry_json: Path | None
    visibility_json: Path | None
    include_decisions: set[str]
    semantic_leaf_only: bool
    visibility_state: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="归一多视图阻尼器出现并输出物理设备模板与楼层展开清单"
    )
    parser.add_argument("manifest", type=Path, help="V12 输入清单 JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", default=None)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def resolve_path(base: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_manifest(path: Path) -> tuple[dict[str, Any], list[SourceConfig]]:
    with path.open("r", encoding="utf-8-sig") as stream:
        manifest = json.load(stream)
    if not isinstance(manifest, dict):
        raise ValueError("manifest 必须是 JSON 对象")
    base = path.resolve().parent
    sources: list[SourceConfig] = []
    for item in manifest.get("sources", []):
        candidate_csv = resolve_path(base, item.get("candidate_csv"))
        if candidate_csv is None:
            raise ValueError(f"source 缺少 candidate_csv：{item}")
        sources.append(
            SourceConfig(
                source_id=str(item["source_id"]),
                view_type=str(item.get("view_type") or "unknown"),
                candidate_csv=candidate_csv,
                frame_texts_csv=resolve_path(base, item.get("frame_texts_csv")),
                primitive_geometry_json=resolve_path(
                    base, item.get("primitive_geometry_json")
                ),
                visibility_json=resolve_path(base, item.get("visibility_json")),
                include_decisions={
                    str(value) for value in item.get("include_decisions", [])
                },
                semantic_leaf_only=bool(item.get("semantic_leaf_only", True)),
                visibility_state=str(
                    item.get("visibility_state") or "api_visibility_unverified"
                ),
            )
        )
    if not sources:
        raise ValueError("manifest 未定义 sources")
    return manifest, sources


def load_visibility_evidence(
    source: SourceConfig,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """读取 V13 可见性证据；未提供时保留清单中的旧状态。"""
    if source.visibility_json is None:
        return (
            {
                "state": source.visibility_state,
                "path": "",
                "record_count": 0,
                "matched_selected_count": 0,
                "visible_selected_count": 0,
                "hidden_selected_count": 0,
                "missing_selected_count": 0,
            },
            {},
        )
    with source.visibility_json.open("r", encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"V13 可见性 JSON 缺少 records：{source.visibility_json}")
    by_key = {
        str(record.get("instance_key") or ""): record
        for record in records
        if record.get("instance_key")
    }
    blocking_error_count = sum(
        int(payload.get(field) or 0)
        for field in (
            "unknown_visibility_instance_count",
            "skipped_object_error_count",
            "dynamic_property_read_error_count",
            "layer_read_error_count",
            "entity_visibility_read_error_count",
            "viewport_read_error_count",
            "viewport_frozen_layer_read_error_count",
        )
    )
    viewport_count = int(payload.get("viewport_record_count") or 0)
    if blocking_error_count:
        state = "api_visibility_export_has_errors"
    elif viewport_count:
        # 当前实例记录是数据库层可见性，不能替代逐视口冻结/覆盖判断。
        state = "api_database_visible_viewport_visibility_unverified"
    else:
        state = "confirmed_visible_database_no_viewports"
    return (
        {
            "state": state,
            "path": str(source.visibility_json),
            "record_count": len(by_key),
            "blocking_error_count": blocking_error_count,
            "viewport_count": viewport_count,
            "matched_selected_count": 0,
            "visible_selected_count": 0,
            "hidden_selected_count": 0,
            "missing_selected_count": 0,
        },
        by_key,
    )


def apply_visibility(
    occurrence: dict[str, Any],
    record: dict[str, Any] | None,
    source_state: str,
) -> None:
    if record is None:
        occurrence["visibility_state"] = (
            source_state
            if source_state not in {"confirmed_visible_database_no_viewports"}
            else "visibility_record_missing"
        )
        occurrence["visibility_reason"] = ""
        occurrence["effective_layer"] = ""
        occurrence["entity_visible"] = ""
        occurrence["effective_visible_database"] = ""
        return
    effective = bool(record.get("effective_visible_database"))
    occurrence["visibility_state"] = (
        "confirmed_visible_database" if effective else "confirmed_hidden_database"
    )
    occurrence["visibility_reason"] = record.get("visibility_reason") or ""
    occurrence["effective_layer"] = record.get("effective_layer") or ""
    occurrence["entity_visible"] = record.get("entity_visible")
    occurrence["effective_visible_database"] = effective


def optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def normalize_building_id(value: str) -> str:
    return "-".join(
        str(int(part))
        for part in re.split(r"[-－]", value)
        if part.strip()
    )


def building_sort_key(value: str) -> tuple[tuple[int, str], ...]:
    return tuple(
        (int(part), part)
        for part in normalize_building_id(value).split("-")
    )


def extract_building_ids(text: str) -> list[str]:
    result: list[str] = []
    for value in BUILDING_TOKEN.findall(text or ""):
        normalized = normalize_building_id(value)
        if normalized not in result:
            result.append(normalized)
    return result


def build_axis_systems(
    path: Path | None,
) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    """返回 frame -> building -> x/y -> axis label -> world coordinate。"""
    if path is None:
        return {}
    raw: dict[
        str, dict[str, dict[str, dict[str, list[float]]]]
    ] = defaultdict(
        lambda: defaultdict(
            lambda: {"x": defaultdict(list), "y": defaultdict(list)}
        )
    )
    for row in read_csv(path):
        frame_id = (row.get("frame_id") or "").strip()
        match = AXIS_LABEL.fullmatch((row.get("text") or "").strip())
        if not frame_id or not match:
            continue
        building_id, axis_label = match.groups()
        building_id = normalize_building_id(building_id)
        axis_label = axis_label.upper()
        dimension = "x" if axis_label.isdigit() else "y"
        coordinate = optional_float(row.get(dimension))
        if coordinate is not None:
            raw[frame_id][building_id][dimension][axis_label].append(coordinate)

    result: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for frame_id, buildings in raw.items():
        for building_id, dimensions in buildings.items():
            x_axes = {
                key: statistics.median(values)
                for key, values in dimensions["x"].items()
            }
            y_axes = {
                key: statistics.median(values)
                for key, values in dimensions["y"].items()
            }
            # 至少三个数字轴和两个字母轴，防止项目编号等偶然文本形成假轴网。
            if len(x_axes) < 3 or len(y_axes) < 2:
                continue
            result.setdefault(frame_id, {})[building_id] = {
                "x": x_axes,
                "y": y_axes,
            }
    return result


def axis_label_sort_key(label: str) -> tuple[int, Any]:
    return (0, int(label)) if label.isdigit() else (1, label)


def distance(
    first: tuple[float, float], second: tuple[float, float]
) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def build_line_axes(
    observations: dict[str, list[tuple[float, float]]],
    label_handles: dict[str, list[str]] | None = None,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, AxisLine]:
    """用同一轴号的两端标注建立无限直轴；单个轴号不做方向猜测。"""
    label_handles = label_handles or {}
    records = records or []
    result: dict[str, AxisLine] = {}
    for label, points in observations.items():
        if len(points) < 2:
            continue
        pair = max(
            (
                (distance(first, second), first, second)
                for index, first in enumerate(points)
                for second in points[index + 1 :]
            ),
            default=None,
            key=lambda item: item[0],
        )
        if pair is None or pair[0] <= 1e-6:
            continue
        _, first, second = pair
        provisional = AxisLine(
            label=label,
            x1=first[0],
            y1=first[1],
            x2=second[0],
            y2=second[1],
            anchor_x=statistics.mean(point[0] for point in points),
            anchor_y=statistics.mean(point[1] for point in points),
        )
        # 轴号文字的插入点可能因对齐方式位于轴圈中心偏侧，不能要求它与轴线
        # 完全共线。只在两端轴号确定方向后，从同一局部范围选择最近的平行
        # AXIS 实体作为可回查证据；该实体不参与定位计算。
        tolerance = max(1000.0, pair[0] * 0.05)
        ranked_records: list[tuple[float, dict[str, Any]]] = []
        for record in records:
            if (
                record.get("entity_type") != "Line"
                or record.get("endpoints_valid") is not True
                or "AXIS" not in str(record.get("layer") or "").upper()
                or "IDEN" in str(record.get("layer") or "").upper()
            ):
                continue
            start_x = optional_float(record.get("start_x"))
            start_y = optional_float(record.get("start_y"))
            end_x = optional_float(record.get("end_x"))
            end_y = optional_float(record.get("end_y"))
            if None in (start_x, start_y, end_x, end_y):
                continue
            start_distance = line_distance(provisional, start_x, start_y)
            end_distance = line_distance(provisional, end_x, end_y)
            length = math.hypot(end_x - start_x, end_y - start_y)
            direction_x = end_x - start_x
            direction_y = end_y - start_y
            base_x = provisional.x2 - provisional.x1
            base_y = provisional.y2 - provisional.y1
            alignment_scale = max(length * pair[0], 1e-9)
            cross_ratio = abs(
                direction_x * base_y - direction_y * base_x
            ) / alignment_scale
            if (
                max(start_distance, end_distance) <= tolerance
                and length >= pair[0] * 0.2
                and cross_ratio <= math.sin(math.radians(2.0))
            ):
                ranked_records.append(
                    ((start_distance + end_distance) / 2.0, record)
                )
        matching_records: list[dict[str, Any]] = []
        if ranked_records:
            ranked_records.sort(key=lambda item: item[0])
            best_distance = ranked_records[0][0]
            distance_band = max(25.0, pair[0] * 0.002)
            matching_records = [
                record
                for record_distance, record in ranked_records
                if record_distance <= best_distance + distance_band
            ]
        result[label] = AxisLine(
            **{
                **provisional.__dict__,
                "label_handles": tuple(
                    sorted({value for value in label_handles.get(label, []) if value})
                ),
                "geometry_record_keys": tuple(
                    sorted(
                        {
                            str(record.get("record_key") or "")
                            for record in matching_records
                            if record.get("record_key")
                        }
                    )
                ),
                "geometry_handles": tuple(
                    sorted(
                        {
                            str(record.get("handle") or "")
                            for record in matching_records
                            if record.get("handle")
                        }
                    )
                ),
                "geometry_match_distances": tuple(
                    record_distance
                    for record_distance, record in ranked_records
                    if record in matching_records
                ),
            }
        )
    return result


def line_side(line: AxisLine, x: float, y: float) -> float:
    return (
        (line.x2 - line.x1) * (y - line.y1)
        - (line.y2 - line.y1) * (x - line.x1)
    )


def line_distance(line: AxisLine, x: float, y: float) -> float:
    length = math.hypot(line.x2 - line.x1, line.y2 - line.y1)
    return abs(line_side(line, x, y)) / length if length > 0 else math.inf


def line_axis_evidence(axis: AxisLine) -> dict[str, Any]:
    return {
        "label": axis.label,
        "label_handles": list(axis.label_handles),
        "geometry_record_keys": list(axis.geometry_record_keys),
        "geometry_handles": list(axis.geometry_handles),
        "geometry_match_distances": list(axis.geometry_match_distances),
        "line": {
            "x1": axis.x1,
            "y1": axis.y1,
            "x2": axis.x2,
            "y2": axis.y2,
        },
    }


def locate_between_line_axes(
    x: float, y: float, axes: dict[str, AxisLine]
) -> AxisLocation | None:
    """在相邻直轴形成的条带/扇区中定位，支持旋转轴和相交扇形轴。"""
    ordered = sorted(axes.values(), key=lambda axis: axis_label_sort_key(axis.label))
    candidates: list[tuple[float, AxisLocation]] = []
    for low, high in zip(ordered, ordered[1:]):
        low_facing = line_side(low, high.anchor_x, high.anchor_y)
        high_facing = line_side(high, low.anchor_x, low.anchor_y)
        if abs(low_facing) <= 1e-9 or abs(high_facing) <= 1e-9:
            continue
        low_point = line_side(low, x, y)
        high_point = line_side(high, x, y)
        scale = max(abs(low_facing), abs(high_facing), 1.0)
        tolerance = scale * 1e-9
        if (
            low_point * low_facing < -tolerance
            or high_point * high_facing < -tolerance
        ):
            continue
        low_distance = line_distance(low, x, y)
        high_distance = line_distance(high, x, y)
        total = low_distance + high_distance
        if not math.isfinite(total) or total <= 1e-9:
            continue
        low_direction = (low.x2 - low.x1, low.y2 - low.y1)
        high_direction = (high.x2 - high.x1, high.y2 - high.y1)
        direction_scale = max(
            math.hypot(*low_direction) * math.hypot(*high_direction), 1e-9
        )
        cross_ratio = abs(
            low_direction[0] * high_direction[1]
            - low_direction[1] * high_direction[0]
        ) / direction_scale
        candidates.append(
            (
                total,
                AxisLocation(
                    low.label,
                    high.label,
                    low_distance / total,
                    {
                        "kind": (
                            "line_strip"
                            if cross_ratio <= math.sin(math.radians(2.0))
                            else "line_wedge"
                        ),
                        "low_boundary": line_axis_evidence(low),
                        "high_boundary": line_axis_evidence(high),
                        "distance_to_low": low_distance,
                        "distance_to_high": high_distance,
                        "cross_ratio": cross_ratio,
                    },
                ),
            )
        )
    if len(candidates) != 1:
        return None
    return candidates[0][1]


def normalize_angle(value: float) -> float:
    return value % (2.0 * math.pi)


def ccw_delta(start: float, end: float) -> float:
    return normalize_angle(end - start)


def angle_on_sampled_arc(
    center_x: float,
    center_y: float,
    start_x: float,
    start_y: float,
    mid_x: float,
    mid_y: float,
    end_x: float,
    end_y: float,
    x: float,
    y: float,
    tolerance_radians: float = math.radians(2.0),
) -> bool:
    start = math.atan2(start_y - center_y, start_x - center_x)
    mid = math.atan2(mid_y - center_y, mid_x - center_x)
    end = math.atan2(end_y - center_y, end_x - center_x)
    point = math.atan2(y - center_y, x - center_x)
    ccw_span = ccw_delta(start, end)
    ccw_mid = ccw_delta(start, mid)
    if ccw_mid <= ccw_span + tolerance_radians:
        return ccw_delta(start, point) <= ccw_span + tolerance_radians
    clockwise_span = ccw_delta(end, start)
    return ccw_delta(point, start) <= clockwise_span + tolerance_radians


def build_curved_axis_family(
    letter_observations: dict[str, list[tuple[float, float]]],
    records: list[dict[str, Any]],
    label_handles: dict[str, list[str]] | None = None,
) -> CurvedAxisFamily | None:
    """把单端字母轴号与同图框的同心圆弧及其切线延伸段闭合。"""
    label_handles = label_handles or {}
    if len(letter_observations) < 2:
        return None
    arcs = [
        record
        for record in records
        if record.get("entity_type") == "Arc"
        and record.get("curve_geometry_valid") is True
        and "AXIS" in str(record.get("layer") or "").upper()
    ]
    if len(arcs) < 2:
        return None

    label_points = {
        label: (
            statistics.mean(point[0] for point in points),
            statistics.mean(point[1] for point in points),
        )
        for label, points in letter_observations.items()
        if points
    }
    if len(label_points) < 2:
        return None
    xs = [point[0] for point in label_points.values()]
    ys = [point[1] for point in label_points.values()]
    label_span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    match_limit = max(3500.0, label_span * 1.5)

    matches: list[tuple[float, str, dict[str, Any], bool]] = []
    for label, point in label_points.items():
        ranked: list[tuple[float, dict[str, Any], bool]] = []
        for arc in arcs:
            start = (
                float(arc["start_x"]),
                float(arc["start_y"]),
            )
            end = (float(arc["end_x"]), float(arc["end_y"]))
            start_distance = distance(point, start)
            end_distance = distance(point, end)
            ranked.append(
                (
                    min(start_distance, end_distance),
                    arc,
                    start_distance <= end_distance,
                )
            )
        ranked.sort(key=lambda item: item[0])
        if not ranked or ranked[0][0] > match_limit:
            continue
        if len(ranked) > 1 and ranked[1][0] <= ranked[0][0] * 1.35 + 100.0:
            continue
        matches.append((ranked[0][0], label, ranked[0][1], ranked[0][2]))

    assigned: dict[str, tuple[dict[str, Any], bool, float]] = {}
    used_records: set[str] = set()
    for match_distance, label, arc, label_near_start in sorted(
        matches, key=lambda item: item[0]
    ):
        record_key = str(arc.get("record_key") or "")
        if label in assigned or record_key in used_records:
            continue
        assigned[label] = (arc, label_near_start, match_distance)
        used_records.add(record_key)
    if len(assigned) < 2:
        return None

    centers = [
        (float(arc["curve_center_x"]), float(arc["curve_center_y"]))
        for arc, _, _ in assigned.values()
    ]
    center_x = statistics.mean(point[0] for point in centers)
    center_y = statistics.mean(point[1] for point in centers)
    median_radius = statistics.median(
        float(arc["curve_radius"]) for arc, _, _ in assigned.values()
    )
    center_tolerance = max(10.0, median_radius * 0.002)
    if any(distance(point, (center_x, center_y)) > center_tolerance for point in centers):
        return None

    transitions: dict[str, tuple[float, float]] = {}
    outward_vectors: list[tuple[float, float]] = []
    for label, (arc, label_near_start, _) in assigned.items():
        transition = (
            (float(arc["end_x"]), float(arc["end_y"]))
            if label_near_start
            else (float(arc["start_x"]), float(arc["start_y"]))
        )
        transitions[label] = transition
        vector = (transition[0] - center_x, transition[1] - center_y)
        length = math.hypot(*vector)
        if length <= 1e-9:
            return None
        outward_vectors.append((vector[0] / length, vector[1] / length))
    outward_x = statistics.mean(vector[0] for vector in outward_vectors)
    outward_y = statistics.mean(vector[1] for vector in outward_vectors)
    outward_length = math.hypot(outward_x, outward_y)
    if outward_length <= 1e-9:
        return None
    outward_x /= outward_length
    outward_y /= outward_length
    if any(
        vector[0] * outward_x + vector[1] * outward_y < math.cos(math.radians(2.0))
        for vector in outward_vectors
    ):
        return None

    tangent_x, tangent_y = -outward_y, outward_x
    mid_projection = statistics.mean(
        (float(arc["curve_mid_x"]) - center_x) * tangent_x
        + (float(arc["curve_mid_y"]) - center_y) * tangent_y
        for arc, _, _ in assigned.values()
    )
    if mid_projection < 0:
        tangent_x, tangent_y = -tangent_x, -tangent_y

    lines = [
        record
        for record in records
        if record.get("entity_type") == "Line"
        and record.get("endpoints_valid") is True
        and "AXIS" in str(record.get("layer") or "").upper()
        and "IDEN" not in str(record.get("layer") or "").upper()
    ]
    curved_axes: dict[str, CurvedAxis] = {}
    for label, (arc, label_near_start, label_match_distance) in assigned.items():
        transition = transitions[label]
        radius = float(arc["curve_radius"])
        endpoint_tolerance = max(10.0, radius * 0.002)
        extension_candidates: list[
            tuple[float, float, float, dict[str, Any]]
        ] = []
        for line in lines:
            first = (float(line["start_x"]), float(line["start_y"]))
            second = (float(line["end_x"]), float(line["end_y"]))
            if distance(first, transition) <= endpoint_tolerance:
                other = second
            elif distance(second, transition) <= endpoint_tolerance:
                other = first
            else:
                continue
            vector = (other[0] - transition[0], other[1] - transition[1])
            length = math.hypot(*vector)
            if length <= 1000.0:
                continue
            tangent_alignment = abs(
                (vector[0] * tangent_x + vector[1] * tangent_y) / length
            )
            if tangent_alignment < math.cos(math.radians(2.0)):
                continue
            s_other = (
                (other[0] - center_x) * tangent_x
                + (other[1] - center_y) * tangent_y
            )
            extension_candidates.append(
                (length, min(0.0, s_other), max(0.0, s_other), line)
            )
        if not extension_candidates:
            continue
        _, s_min, s_max, extension = max(
            extension_candidates, key=lambda item: item[0]
        )
        curved_axes[label] = CurvedAxis(
            label=label,
            radius=radius,
            start_x=float(arc["start_x"]),
            start_y=float(arc["start_y"]),
            mid_x=float(arc["curve_mid_x"]),
            mid_y=float(arc["curve_mid_y"]),
            end_x=float(arc["end_x"]),
            end_y=float(arc["end_y"]),
            straight_s_min=s_min,
            straight_s_max=s_max,
            label_handles=tuple(
                sorted({value for value in label_handles.get(label, []) if value})
            ),
            label_match_distance=label_match_distance,
            arc_record_key=str(arc.get("record_key") or ""),
            arc_handle=str(arc.get("handle") or ""),
            extension_record_key=str(extension.get("record_key") or ""),
            extension_handle=str(extension.get("handle") or ""),
            transition_x=transition[0],
            transition_y=transition[1],
        )
    if len(curved_axes) < 2:
        return None
    return CurvedAxisFamily(
        center_x=center_x,
        center_y=center_y,
        outward_x=outward_x,
        outward_y=outward_y,
        tangent_x=tangent_x,
        tangent_y=tangent_y,
        axes=curved_axes,
    )


def build_geometry_axis_systems(
    frame_texts_path: Path | None,
    primitive_geometry_path: Path | None,
) -> dict[str, dict[str, GeometryAxisSystem]]:
    if frame_texts_path is None or primitive_geometry_path is None:
        return {}
    if not frame_texts_path.is_file() or not primitive_geometry_path.is_file():
        return {}
    with primitive_geometry_path.open("r", encoding="utf-8-sig") as stream:
        payload = json.load(stream)
    records = payload.get("records")
    if not isinstance(records, list):
        return {}

    observations: dict[
        str, dict[str, dict[str, dict[str, list[tuple[float, float]]]]]
    ] = defaultdict(
        lambda: defaultdict(
            lambda: {"x": defaultdict(list), "y": defaultdict(list)}
        )
    )
    label_handles: dict[
        str, dict[str, dict[str, dict[str, list[str]]]]
    ] = defaultdict(
        lambda: defaultdict(
            lambda: {"x": defaultdict(list), "y": defaultdict(list)}
        )
    )
    for row in read_csv(frame_texts_path):
        frame_id = (row.get("frame_id") or "").strip()
        match = AXIS_LABEL.fullmatch((row.get("text") or "").strip())
        x = optional_float(row.get("x"))
        y = optional_float(row.get("y"))
        if not frame_id or not match or x is None or y is None:
            continue
        building, label = match.groups()
        label = label.upper()
        dimension = "x" if label.isdigit() else "y"
        normalized_building = normalize_building_id(building)
        observations[frame_id][normalized_building][dimension][label].append((x, y))
        handle = str(row.get("handle") or "")
        if handle:
            label_handles[frame_id][normalized_building][dimension][label].append(
                handle
            )

    result: dict[str, dict[str, GeometryAxisSystem]] = {}
    for frame_id, buildings in observations.items():
        for building, dimensions in buildings.items():
            all_points = [
                point
                for labels in dimensions.values()
                for points in labels.values()
                for point in points
            ]
            if not all_points:
                continue
            min_x = min(point[0] for point in all_points)
            min_y = min(point[1] for point in all_points)
            max_x = max(point[0] for point in all_points)
            max_y = max(point[1] for point in all_points)
            span = max(max_x - min_x, max_y - min_y, 10000.0)
            margin = span * 0.35
            nearby = [
                record
                for record in records
                if optional_float(record.get("min_x")) is not None
                and optional_float(record.get("min_y")) is not None
                and optional_float(record.get("max_x")) is not None
                and optional_float(record.get("max_y")) is not None
                and float(record["min_x"]) <= max_x + margin
                and float(record["max_x"]) >= min_x - margin
                and float(record["min_y"]) <= max_y + margin
                and float(record["max_y"]) >= min_y - margin
            ]
            handles = label_handles[frame_id][building]
            x_lines = build_line_axes(dimensions["x"], handles["x"], nearby)
            y_lines = build_line_axes(dimensions["y"], handles["y"], nearby)
            y_curves = build_curved_axis_family(
                dimensions["y"], nearby, handles["y"]
            )
            if len(x_lines) < 3:
                continue
            if len(y_lines) < 2 and y_curves is None:
                continue
            result.setdefault(frame_id, {})[building] = GeometryAxisSystem(
                x_lines=x_lines,
                y_lines=y_lines,
                y_curves=y_curves,
            )
    return result


def curved_axis_supported(
    family: CurvedAxisFamily, axis: CurvedAxis, x: float, y: float
) -> bool:
    vector_x = x - family.center_x
    vector_y = y - family.center_y
    s = vector_x * family.tangent_x + vector_y * family.tangent_y
    tolerance = max(10.0, axis.radius * 0.002)
    if s < -tolerance:
        return axis.straight_s_min - tolerance <= s <= axis.straight_s_max + tolerance
    return angle_on_sampled_arc(
        family.center_x,
        family.center_y,
        axis.start_x,
        axis.start_y,
        axis.mid_x,
        axis.mid_y,
        axis.end_x,
        axis.end_y,
        x,
        y,
    )


def curved_axis_evidence(axis: CurvedAxis) -> dict[str, Any]:
    return {
        "label": axis.label,
        "label_handles": list(axis.label_handles),
        "label_match_distance": axis.label_match_distance,
        "arc_record_key": axis.arc_record_key,
        "arc_handle": axis.arc_handle,
        "extension_record_key": axis.extension_record_key,
        "extension_handle": axis.extension_handle,
        "radius": axis.radius,
        "arc": {
            "start_x": axis.start_x,
            "start_y": axis.start_y,
            "mid_x": axis.mid_x,
            "mid_y": axis.mid_y,
            "end_x": axis.end_x,
            "end_y": axis.end_y,
        },
        "transition": {
            "x": axis.transition_x,
            "y": axis.transition_y,
        },
        "straight_s_min": axis.straight_s_min,
        "straight_s_max": axis.straight_s_max,
    }


def locate_between_curved_axes(
    x: float, y: float, family: CurvedAxisFamily
) -> AxisLocation | None:
    vector_x = x - family.center_x
    vector_y = y - family.center_y
    s = vector_x * family.tangent_x + vector_y * family.tangent_y
    if s >= 0:
        effective_radius = math.hypot(vector_x, vector_y)
    else:
        effective_radius = (
            vector_x * family.outward_x + vector_y * family.outward_y
        )
    coordinate = -effective_radius
    ordered = sorted((-axis.radius, label, axis) for label, axis in family.axes.items())
    candidates: list[AxisLocation] = []
    for (low_value, low_label, low_axis), (
        high_value,
        high_label,
        high_axis,
    ) in zip(ordered, ordered[1:]):
        if not (low_value <= coordinate <= high_value and high_value > low_value):
            continue
        if not curved_axis_supported(family, low_axis, x, y):
            continue
        if not curved_axis_supported(family, high_axis, x, y):
            continue
        candidates.append(
            AxisLocation(
                low_label,
                high_label,
                (coordinate - low_value) / (high_value - low_value),
                {
                    "kind": "curved_axis_family",
                    "active_region": "arc" if s >= 0 else "tangent",
                    "center": {"x": family.center_x, "y": family.center_y},
                    "outward_vector": {
                        "x": family.outward_x,
                        "y": family.outward_y,
                    },
                    "tangent_vector": {
                        "x": family.tangent_x,
                        "y": family.tangent_y,
                    },
                    "tangent_coordinate": s,
                    "effective_coordinate": coordinate,
                    "distance_to_low": coordinate - low_value,
                    "distance_to_high": high_value - coordinate,
                    "low_boundary": curved_axis_evidence(low_axis),
                    "high_boundary": curved_axis_evidence(high_axis),
                },
            )
        )
    if not candidates and len(ordered) >= 2:
        first_value, first_label, first_axis = ordered[0]
        second_value, _, second_axis = ordered[1]
        last_value, last_label, last_axis = ordered[-1]
        previous_value, _, previous_axis = ordered[-2]
        if coordinate < first_value and second_value > first_value:
            outside = (first_value - coordinate) / (second_value - first_value)
            if (
                outside <= 0.25
                and curved_axis_supported(family, first_axis, x, y)
                and curved_axis_supported(family, second_axis, x, y)
            ):
                candidates.append(
                    AxisLocation(
                        "OUT",
                        first_label,
                        outside,
                        {
                            "kind": "curved_axis_family_outer_band",
                            "active_region": "arc" if s >= 0 else "tangent",
                            "center": {"x": family.center_x, "y": family.center_y},
                            "tangent_coordinate": s,
                            "effective_coordinate": coordinate,
                            "outside_ratio": outside,
                            "outer_boundary": curved_axis_evidence(first_axis),
                            "spacing_reference_boundary": curved_axis_evidence(
                                second_axis
                            ),
                        },
                    )
                )
        elif coordinate > last_value and last_value > previous_value:
            outside = (coordinate - last_value) / (last_value - previous_value)
            if (
                outside <= 0.25
                and curved_axis_supported(family, previous_axis, x, y)
                and curved_axis_supported(family, last_axis, x, y)
            ):
                candidates.append(
                    AxisLocation(
                        last_label,
                        "OUT",
                        outside,
                        {
                            "kind": "curved_axis_family_outer_band",
                            "active_region": "arc" if s >= 0 else "tangent",
                            "center": {"x": family.center_x, "y": family.center_y},
                            "tangent_coordinate": s,
                            "effective_coordinate": coordinate,
                            "outside_ratio": outside,
                            "outer_boundary": curved_axis_evidence(last_axis),
                            "spacing_reference_boundary": curved_axis_evidence(
                                previous_axis
                            ),
                        },
                    )
                )
    return candidates[0] if len(candidates) == 1 else None


def locate_between_axes(
    coordinate: float, axes: dict[str, float]
) -> AxisLocation | None:
    ordered = sorted((value, label) for label, value in axes.items())
    if len(ordered) < 2:
        return None
    for index in range(len(ordered) - 1):
        low_value, low_label = ordered[index]
        high_value, high_label = ordered[index + 1]
        if low_value <= coordinate <= high_value and high_value > low_value:
            fraction = (coordinate - low_value) / (high_value - low_value)
            return AxisLocation(
                low_label,
                high_label,
                fraction,
                {
                    "kind": "coordinate_axis_band",
                    "low_boundary": {
                        "label": low_label,
                        "coordinate": low_value,
                    },
                    "high_boundary": {
                        "label": high_label,
                        "coordinate": high_value,
                    },
                    "distance_to_low": coordinate - low_value,
                    "distance_to_high": high_value - coordinate,
                },
            )
    return None


def selected_leaf(row: dict[str, str], source: SourceConfig) -> bool:
    if source.include_decisions and row.get("decision") not in source.include_decisions:
        return False
    if source.semantic_leaf_only and row.get("semantic_leaf_symbol") != "True":
        return False
    return True


def parent_local_coordinate(
    row: dict[str, str], rows_by_key: dict[str, dict[str, str]]
) -> tuple[float, float, dict[str, str]] | None:
    parent_key = (row.get("semantic_parent_key") or "").strip()
    parent = rows_by_key.get(parent_key)
    if not parent:
        return None
    values = [
        optional_float(parent.get("min_x")),
        optional_float(parent.get("min_y")),
        optional_float(parent.get("max_x")),
        optional_float(parent.get("max_y")),
        optional_float(row.get("x")),
        optional_float(row.get("y")),
    ]
    if any(value is None for value in values):
        return None
    min_x, min_y, max_x, max_y, x, y = (float(value) for value in values)
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        return None
    return (x - min_x) / width, (y - min_y) / height, parent


def occurrence_base(
    source: SourceConfig,
    row: dict[str, str],
    building_id: str,
    method: str,
) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "view_type": source.view_type,
        "source_frame_id": row.get("frame_id") or "",
        "source_decision": row.get("decision") or "",
        "raw_instance_key": row.get("instance_key") or "",
        "raw_parent_key": row.get("semantic_parent_key") or "",
        "building_id": building_id,
        "mapping_method": method,
        "mapping_status": "",
        "physical_template_id": "",
        "axis_x_low": "",
        "axis_x_high": "",
        "axis_x_fraction": "",
        "axis_y_low": "",
        "axis_y_high": "",
        "axis_y_fraction": "",
        "parent_u": "",
        "parent_v": "",
        "world_x": row.get("x") or "",
        "world_y": row.get("y") or "",
        "visibility_state": source.visibility_state,
        "geometry_signature": row.get("geometry_signature") or "",
        "source_reason": row.get("reasons") or "",
    }


def axis_occurrences(
    source: SourceConfig,
    rows: list[dict[str, str]],
    axis_systems: dict[str, dict[str, dict[str, dict[str, float]]]],
    geometry_axis_systems: dict[str, dict[str, GeometryAxisSystem]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    geometry_axis_systems = geometry_axis_systems or {}
    mapped: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for row in rows:
        if not selected_leaf(row, source):
            continue
        frame_systems = axis_systems.get((row.get("frame_id") or "").strip(), {})
        frame_geometry = geometry_axis_systems.get(
            (row.get("frame_id") or "").strip(), {}
        )
        x = optional_float(row.get("x"))
        y = optional_float(row.get("y"))
        row_mapped = False
        if x is not None and y is not None:
            for building_id, system in sorted(
                frame_geometry.items(),
                key=lambda item: building_sort_key(item[0]),
            ):
                x_location = locate_between_line_axes(x, y, system.x_lines)
                if system.y_curves is not None:
                    y_location = locate_between_curved_axes(x, y, system.y_curves)
                else:
                    y_location = locate_between_line_axes(x, y, system.y_lines)
                if x_location is None or y_location is None:
                    continue
                occurrence = occurrence_base(
                    source, row, building_id, "building_axis_grid_geometry"
                )
                occurrence.update(
                    {
                        "axis_x_low": x_location.low,
                        "axis_x_high": x_location.high,
                        "axis_x_fraction": x_location.fraction,
                        "axis_y_low": y_location.low,
                        "axis_y_high": y_location.high,
                        "axis_y_fraction": y_location.fraction,
                    }
                )
                mapped.append(occurrence)
                row_mapped = True
            if row_mapped:
                continue
            for building_id, system in sorted(
                frame_systems.items(), key=lambda item: building_sort_key(item[0])
            ):
                x_location = locate_between_axes(x, system["x"])
                y_location = locate_between_axes(y, system["y"])
                if x_location is None or y_location is None:
                    continue
                occurrence = occurrence_base(
                    source, row, building_id, "building_axis_grid"
                )
                occurrence.update(
                    {
                        "axis_x_low": x_location.low,
                        "axis_x_high": x_location.high,
                        "axis_x_fraction": x_location.fraction,
                        "axis_y_low": y_location.low,
                        "axis_y_high": y_location.high,
                        "axis_y_fraction": y_location.fraction,
                    }
                )
                mapped.append(occurrence)
                row_mapped = True
        if not row_mapped:
            unresolved.append(row)
    return mapped, unresolved


def axis_match(
    occurrence: dict[str, Any],
    references: list[dict[str, Any]],
    tolerance: float,
) -> tuple[dict[str, Any] | None, float | None, float | None]:
    candidates: list[tuple[float, float, float, dict[str, Any]]] = []
    for reference in references:
        if occurrence["building_id"] != reference["building_id"]:
            continue
        keys = ("axis_x_low", "axis_x_high", "axis_y_low", "axis_y_high")
        if any(occurrence[key] != reference[key] for key in keys):
            continue
        dx = abs(
            float(occurrence["axis_x_fraction"])
            - float(reference["axis_x_fraction"])
        )
        dy = abs(
            float(occurrence["axis_y_fraction"])
            - float(reference["axis_y_fraction"])
        )
        candidates.append((dx + dy, dx, dy, reference))
    if not candidates:
        return None, None, None
    _, dx, dy, reference = min(candidates, key=lambda item: item[:3])
    if dx > tolerance or dy > tolerance:
        return None, dx, dy
    return reference, dx, dy


def parent_building_ids(parent: dict[str, str]) -> list[str]:
    text = " ".join(
        [
            parent.get("block_name") or "",
            parent.get("effective_name") or "",
            parent.get("name_path") or "",
            parent.get("semantic_preview") or "",
        ]
    )
    return extract_building_ids(text)


def local_match(
    building_id: str,
    u: float,
    v: float,
    references: list[dict[str, Any]],
    tolerance: float,
) -> tuple[dict[str, Any] | None, float | None, float | None]:
    candidates: list[tuple[float, float, float, dict[str, Any]]] = []
    for reference in references:
        if reference["building_id"] != building_id:
            continue
        reference_u = reference.get("_parent_u")
        reference_v = reference.get("_parent_v")
        if reference_u is None or reference_v is None:
            continue
        du = abs(u - float(reference_u))
        dv = abs(v - float(reference_v))
        candidates.append((du + dv, du, dv, reference))
    if not candidates:
        return None, None, None
    _, du, dv, reference = min(candidates, key=lambda item: item[:3])
    if du > tolerance or dv > tolerance:
        return None, du, dv
    return reference, du, dv


def stable_template_ids(primary: list[dict[str, Any]]) -> None:
    primary.sort(
        key=lambda row: (
            building_sort_key(row["building_id"]),
            row["source_frame_id"],
            float(row["world_x"]),
            float(row["world_y"]),
            row["raw_instance_key"],
        )
    )
    for index, row in enumerate(primary, 1):
        row["physical_template_id"] = f"PT-{index:04d}"
        row["mapping_status"] = "primary_axis_reference"


def ambiguous_primary_mapping_keys(
    primary_axis_rows: list[dict[str, Any]],
    shared_evidence_buildings: set[str],
) -> set[str]:
    """识别没有多栋共用证据支撑的一实例多轴网映射。"""
    mappings_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in primary_axis_rows:
        mappings_by_key[row["raw_instance_key"]].append(row)
    ambiguous: set[str] = set()
    for raw_key, mappings in mappings_by_key.items():
        building_counts = Counter(row["building_id"] for row in mappings)
        mapped_buildings = set(building_counts)
        if any(count != 1 for count in building_counts.values()):
            ambiguous.add(raw_key)
        elif (
            len(mapped_buildings) > 1
            and not mapped_buildings.issubset(shared_evidence_buildings)
        ):
            ambiguous.add(raw_key)
    return ambiguous


def source_summary(
    sources: list[SourceConfig],
    raw_counts: Counter[str],
    occurrences: list[dict[str, Any]],
    visibility_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in sources:
        rows = [row for row in occurrences if row["source_id"] == source.source_id]
        mapped = [row for row in rows if row["physical_template_id"]]
        result.append(
            {
                "source_id": source.source_id,
                "view_type": source.view_type,
                "raw_leaf_count": raw_counts[source.source_id],
                "expanded_occurrence_count": len(rows),
                "mapped_occurrence_count": len(mapped),
                "unresolved_occurrence_count": len(rows) - len(mapped),
                "unique_physical_template_count": len(
                    {row["physical_template_id"] for row in mapped}
                ),
                "visibility_state": source.visibility_state,
                "visibility_record_count": visibility_meta[source.source_id].get(
                    "record_count", 0
                ),
                "visibility_matched_selected_count": visibility_meta[
                    source.source_id
                ].get("matched_selected_count", 0),
                "visibility_visible_selected_count": visibility_meta[
                    source.source_id
                ].get("visible_selected_count", 0),
                "visibility_hidden_selected_count": visibility_meta[
                    source.source_id
                ].get("hidden_selected_count", 0),
                "visibility_missing_selected_count": visibility_meta[
                    source.source_id
                ].get("missing_selected_count", 0),
                "viewport_record_count": visibility_meta[source.source_id].get(
                    "viewport_count", 0
                ),
                "visibility_blocking_error_count": visibility_meta[
                    source.source_id
                ].get("blocking_error_count", 0),
            }
        )
    return result


def read_shared_layouts(path: Path | None) -> list[dict[str, str]]:
    return read_csv(path) if path is not None else []


def expand_floor_tokens(values: Iterable[str]) -> tuple[list[str], list[str]]:
    """把明确楼层及同类楼层范围展开；保留无法安全展开的标记。"""
    floors: list[str] = []
    unresolved: list[str] = []
    for raw in values:
        token = (raw or "").strip().upper()
        if not token:
            continue
        match = FLOOR_TOKEN.fullmatch(token)
        if not match:
            if token in {"ROOF", "FOUNDATION"}:
                if token not in floors:
                    floors.append(token)
            else:
                unresolved.append(token)
            continue
        start, end = match.groups()
        start = start.upper()
        end = (end or start).upper()
        start_basement = start.startswith("B")
        end_basement = end.startswith("B")
        if start_basement != end_basement:
            unresolved.append(token)
            continue
        start_number = int(start[1:] if start_basement else start)
        end_number = int(end[1:] if end_basement else end)
        step = 1 if end_number >= start_number else -1
        for number in range(start_number, end_number + step, step):
            floor = f"B{number}F" if start_basement else f"{number}F"
            if floor not in floors:
                floors.append(floor)
    return floors, unresolved


def resolve_floors_by_building(
    shared_rows: list[dict[str, str]],
    scope_buildings: list[str],
    scope_floors: list[str],
) -> tuple[dict[str, list[str]], str, list[str]]:
    """优先采用已调和的多栋标准层证据，否则只允许单栋范围回退。"""
    floors_by_building: dict[str, list[str]] = {}
    issues: list[str] = []
    for row in shared_rows:
        if row.get("status") != "shared_building_standard_floor_consistent":
            continue
        buildings = [
            normalize_building_id(value.strip())
            for value in (row.get("building_ids") or "").split(",")
            if value.strip()
        ]
        floors, unresolved = expand_floor_tokens(
            value.strip()
            for value in (row.get("floors") or "").split(",")
            if value.strip()
        )
        if unresolved:
            issues.append(
                "shared_layout_floor_unresolved:" + ",".join(unresolved)
            )
            continue
        for building_id in buildings:
            if (
                building_id in floors_by_building
                and floors_by_building[building_id] != floors
            ):
                raise ValueError(f"楼栋 {building_id} 存在冲突楼层范围")
            floors_by_building[building_id] = floors
    if floors_by_building:
        return floors_by_building, "shared_layout_evidence", issues

    normalized_buildings = [
        normalize_building_id(value)
        for value in scope_buildings
        if str(value).strip()
    ]
    if len(normalized_buildings) != 1:
        issues.append("scope_floor_assignment_unresolved")
        return {}, "unresolved", issues
    floors, unresolved = expand_floor_tokens(scope_floors)
    if unresolved:
        issues.append("scope_floor_token_unresolved:" + ",".join(unresolved))
    if not floors or unresolved:
        return {}, "unresolved", issues
    return (
        {normalized_buildings[0]: floors},
        "single_building_scope",
        issues,
    )


def expand_physical_devices(
    templates: list[dict[str, Any]],
    floors_by_building: dict[str, list[str]],
    floor_evidence_source: str,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for template in templates:
        building_id = template["building_id"]
        for floor in floors_by_building.get(building_id, []):
            result.append(
                {
                    "physical_device_id": (
                        f"PD-B{building_id}-{floor}-{template['physical_template_id']}"
                    ),
                    "physical_template_id": template["physical_template_id"],
                    "building_id": building_id,
                    "floor": floor,
                    "axis_position_key": template["axis_position_key"],
                    "axis_x_low": template["axis_x_low"],
                    "axis_x_high": template["axis_x_high"],
                    "axis_x_fraction": template["axis_x_fraction"],
                    "axis_y_low": template["axis_y_low"],
                    "axis_y_high": template["axis_y_high"],
                    "axis_y_fraction": template["axis_y_fraction"],
                    "primary_source_id": template["primary_source_id"],
                    "primary_frame_id": template["primary_frame_id"],
                    "primary_instance_key": template["primary_instance_key"],
                    "primary_world_x": template["primary_world_x"],
                    "primary_world_y": template["primary_world_y"],
                    "location_method": template["location_method"],
                    "floor_evidence_source": floor_evidence_source,
                    "location_status": "device_location_complete",
                    "evidence_status": template["evidence_status"],
                }
            )
    return result


def format_fraction(value: Any) -> str:
    if value in ("", None):
        return ""
    return f"{float(value):.4f}"


def main() -> int:
    args = parse_args()
    manifest, sources = load_manifest(args.manifest)
    primary_source_id = str(manifest.get("primary_source_id") or "")
    if primary_source_id not in {source.source_id for source in sources}:
        raise ValueError("primary_source_id 未对应任何 source")
    axis_tolerance = float(manifest.get("axis_fraction_tolerance", 0.08))
    local_tolerance = float(manifest.get("parent_normalized_tolerance", 0.02))
    manifest_base = args.manifest.resolve().parent
    shared_layout_csv = resolve_path(
        manifest_base, manifest.get("shared_layout_csv")
    )
    shared_rows = read_shared_layouts(shared_layout_csv)
    shared_evidence_buildings = {
        normalize_building_id(value.strip())
        for row in shared_rows
        if row.get("status") == "shared_building_standard_floor_consistent"
        for value in (row.get("building_ids") or "").split(",")
        if value.strip()
    }

    rows_by_source: dict[str, list[dict[str, str]]] = {}
    rows_by_key: dict[str, dict[str, dict[str, str]]] = {}
    raw_counts: Counter[str] = Counter()
    axis_mapped_by_source: dict[str, list[dict[str, Any]]] = {}
    unresolved_by_source: dict[str, list[dict[str, str]]] = {}
    visibility_meta_by_source: dict[str, dict[str, Any]] = {}
    visibility_by_source: dict[str, dict[str, dict[str, Any]]] = {}

    for source in sources:
        rows = read_csv(source.candidate_csv)
        rows_by_source[source.source_id] = rows
        rows_by_key[source.source_id] = {
            row.get("instance_key") or "": row
            for row in rows
            if row.get("instance_key")
        }
        raw_counts[source.source_id] = sum(
            1 for row in rows if selected_leaf(row, source)
        )
        visibility_meta, visibility_records = load_visibility_evidence(source)
        selected_rows = [row for row in rows if selected_leaf(row, source)]
        selected_keys = [
            row.get("instance_key") or ""
            for row in selected_rows
            if row.get("instance_key")
        ]
        matched_selected = [
            visibility_records[key]
            for key in selected_keys
            if key in visibility_records
        ]
        visibility_meta["matched_selected_count"] = len(matched_selected)
        visibility_meta["visible_selected_count"] = sum(
            1
            for record in matched_selected
            if bool(record.get("effective_visible_database"))
        )
        visibility_meta["hidden_selected_count"] = sum(
            1
            for record in matched_selected
            if not bool(record.get("effective_visible_database"))
        )
        visibility_meta["missing_selected_count"] = (
            len(selected_keys) - len(matched_selected)
        )
        if source.visibility_json is not None:
            if visibility_meta["missing_selected_count"]:
                visibility_meta["state"] = "visibility_record_missing"
            elif visibility_meta["hidden_selected_count"]:
                visibility_meta["state"] = "selected_instance_hidden_database"
        source.visibility_state = str(visibility_meta["state"])
        visibility_meta_by_source[source.source_id] = visibility_meta
        visibility_by_source[source.source_id] = visibility_records
        axis_systems = build_axis_systems(source.frame_texts_csv)
        geometry_axis_systems = build_geometry_axis_systems(
            source.frame_texts_csv,
            source.primitive_geometry_json,
        )
        axis_rows, unresolved = axis_occurrences(
            source,
            rows,
            axis_systems,
            geometry_axis_systems,
        )
        axis_mapped_by_source[source.source_id] = axis_rows
        unresolved_by_source[source.source_id] = unresolved

    primary_source = next(
        source for source in sources if source.source_id == primary_source_id
    )
    primary_axis_rows = axis_mapped_by_source[primary_source_id]
    mapped_counts = Counter(row["raw_instance_key"] for row in primary_axis_rows)
    ambiguous_primary_keys = ambiguous_primary_mapping_keys(
        primary_axis_rows,
        shared_evidence_buildings,
    )
    primary = [
        row
        for row in primary_axis_rows
        if row["raw_instance_key"] not in ambiguous_primary_keys
    ]
    stable_template_ids(primary)

    primary_issues: list[dict[str, Any]] = []
    for raw in unresolved_by_source[primary_source_id]:
        occurrence = occurrence_base(primary_source, raw, "", "unresolved")
        occurrence["mapping_status"] = "primary_axis_location_unresolved"
        primary_issues.append(occurrence)
    primary_by_raw_key = rows_by_key[primary_source_id]
    for raw_key in sorted(ambiguous_primary_keys):
        raw = primary_by_raw_key.get(raw_key)
        if raw is None:
            continue
        occurrence = occurrence_base(primary_source, raw, "", "unresolved")
        occurrence["mapping_status"] = "primary_axis_location_ambiguous"
        occurrence["source_reason"] = (
            f"同一主视图实例落入{mapped_counts[raw_key]}套楼栋轴网"
        )
        primary_issues.append(occurrence)

    # 给主视图参考补充父容器局部坐标，供无图框/无轴网的跨视图匹配。
    for occurrence in primary:
        raw = primary_by_raw_key.get(occurrence["raw_instance_key"])
        local = (
            parent_local_coordinate(raw, primary_by_raw_key) if raw else None
        )
        if local:
            occurrence["_parent_u"], occurrence["_parent_v"], _ = local

    occurrences: list[dict[str, Any]] = [*primary, *primary_issues]
    for source in sources:
        if source.source_id == primary_source_id:
            continue
        source_rows_by_key = rows_by_key[source.source_id]
        axis_rows = axis_mapped_by_source[source.source_id]
        axis_raw_keys: set[str] = set()
        for occurrence in axis_rows:
            reference, dx, dy = axis_match(occurrence, primary, axis_tolerance)
            if reference:
                occurrence["physical_template_id"] = reference[
                    "physical_template_id"
                ]
                occurrence["mapping_status"] = "matched_primary_axis_grid"
                occurrence["axis_fraction_delta_x"] = dx
                occurrence["axis_fraction_delta_y"] = dy
            else:
                occurrence["mapping_status"] = "axis_match_unresolved"
                occurrence["axis_fraction_delta_x"] = dx
                occurrence["axis_fraction_delta_y"] = dy
            raw = source_rows_by_key.get(occurrence["raw_instance_key"])
            local = (
                parent_local_coordinate(raw, source_rows_by_key) if raw else None
            )
            if local:
                occurrence["parent_u"], occurrence["parent_v"], _ = local
            occurrences.append(occurrence)
            axis_raw_keys.add(occurrence["raw_instance_key"])

        # 对轴网映射失败或没有图框的原始叶子，使用语义父容器局部坐标匹配。
        for raw in rows_by_source[source.source_id]:
            if not selected_leaf(raw, source):
                continue
            if raw.get("instance_key") in axis_raw_keys:
                continue
            local = parent_local_coordinate(raw, source_rows_by_key)
            if local is None:
                occurrence = occurrence_base(source, raw, "", "unresolved")
                occurrence["mapping_status"] = "parent_bounds_missing"
                occurrences.append(occurrence)
                continue
            u, v, parent = local
            building_ids = parent_building_ids(parent)
            if not building_ids:
                occurrence = occurrence_base(
                    source, raw, "", "semantic_parent_local"
                )
                occurrence.update(
                    {
                        "mapping_status": "parent_building_unresolved",
                        "parent_u": u,
                        "parent_v": v,
                    }
                )
                occurrences.append(occurrence)
                continue
            for building_id in building_ids:
                occurrence = occurrence_base(
                    source, raw, building_id, "semantic_parent_local"
                )
                occurrence["parent_u"] = u
                occurrence["parent_v"] = v
                reference, du, dv = local_match(
                    building_id, u, v, primary, local_tolerance
                )
                if reference:
                    occurrence.update(
                        {
                            "physical_template_id": reference[
                                "physical_template_id"
                            ],
                            "mapping_status": "matched_primary_parent_local",
                            "axis_x_low": reference["axis_x_low"],
                            "axis_x_high": reference["axis_x_high"],
                            "axis_x_fraction": reference["axis_x_fraction"],
                            "axis_y_low": reference["axis_y_low"],
                            "axis_y_high": reference["axis_y_high"],
                            "axis_y_fraction": reference["axis_y_fraction"],
                            "parent_delta_u": du,
                            "parent_delta_v": dv,
                        }
                    )
                else:
                    occurrence.update(
                        {
                            "mapping_status": "parent_local_match_unresolved",
                            "parent_delta_u": du,
                            "parent_delta_v": dv,
                        }
                    )
                occurrences.append(occurrence)

    for occurrence in occurrences:
        source_id = occurrence["source_id"]
        visibility_record = visibility_by_source[source_id].get(
            occurrence["raw_instance_key"]
        )
        apply_visibility(
            occurrence,
            visibility_record,
            visibility_meta_by_source[source_id]["state"],
        )

    source_rows = source_summary(
        sources, raw_counts, occurrences, visibility_meta_by_source
    )
    mapped_occurrences = [
        row for row in occurrences if row.get("physical_template_id")
    ]
    unresolved_occurrences = [
        row for row in occurrences if not row.get("physical_template_id")
    ]

    occurrence_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in mapped_occurrences:
        occurrence_counts[row["physical_template_id"]][row["source_id"]] += 1

    source_ids = [source.source_id for source in sources]
    templates: list[dict[str, Any]] = []
    for reference in primary:
        template_id = reference["physical_template_id"]
        axis_key = (
            f"B{reference['building_id']}|"
            f"X{reference['axis_x_low']}>{reference['axis_x_high']}"
            f"@{float(reference['axis_x_fraction']):.4f}|"
            f"Y{reference['axis_y_low']}>{reference['axis_y_high']}"
            f"@{float(reference['axis_y_fraction']):.4f}"
        )
        counts = occurrence_counts[template_id]
        missing_sources = [
            source_id for source_id in source_ids if counts[source_id] == 0
        ]
        templates.append(
            {
                "physical_template_id": template_id,
                "building_id": reference["building_id"],
                "axis_position_key": axis_key,
                "axis_x_low": reference["axis_x_low"],
                "axis_x_high": reference["axis_x_high"],
                "axis_x_fraction": format_fraction(
                    reference["axis_x_fraction"]
                ),
                "axis_y_low": reference["axis_y_low"],
                "axis_y_high": reference["axis_y_high"],
                "axis_y_fraction": format_fraction(
                    reference["axis_y_fraction"]
                ),
                "primary_source_id": primary_source_id,
                "primary_frame_id": reference["source_frame_id"],
                "primary_instance_key": reference["raw_instance_key"],
                "primary_world_x": reference["world_x"],
                "primary_world_y": reference["world_y"],
                "location_method": reference["mapping_method"],
                **{
                    f"{source_id}_occurrence_count": counts[source_id]
                    for source_id in source_ids
                },
                "missing_sources": ",".join(missing_sources),
                "evidence_status": (
                    "all_sources_mapped"
                    if not missing_sources
                    else "cross_view_source_missing"
                ),
            }
        )

    scope_buildings = [
        str(value) for value in manifest.get("scope_buildings", [])
    ]
    scope_floors = [str(value) for value in manifest.get("scope_floors", [])]
    floors_by_building, floor_evidence_source, floor_scope_issues = (
        resolve_floors_by_building(
            shared_rows,
            scope_buildings,
            scope_floors,
        )
    )
    physical_devices = expand_physical_devices(
        templates,
        floors_by_building,
        floor_evidence_source,
    )
    cross_only = {
        row["physical_template_id"]
        for row in mapped_occurrences
        if row["source_id"] != primary_source_id
    } - {row["physical_template_id"] for row in primary}
    visibility_unverified = any(
        source.visibility_state
        not in {"confirmed_visible", "confirmed_visible_database_no_viewports"}
        for source in sources
    ) or any(
        row.get("visibility_state") != "confirmed_visible_database"
        for row in occurrences
        if visibility_meta_by_source[row["source_id"]].get("path")
    )
    single_primary = len(sources) == 1
    location_missing_fields = sum(
        1
        for row in physical_devices
        if any(
            row.get(field) in ("", None)
            for field in (
                "building_id",
                "floor",
                "axis_position_key",
                "primary_source_id",
                "primary_frame_id",
                "primary_instance_key",
                "primary_world_x",
                "primary_world_y",
            )
        )
    )
    duplicate_device_id_count = len(physical_devices) - len(
        {row["physical_device_id"] for row in physical_devices}
    )
    if unresolved_occurrences or cross_only:
        overall_status = (
            "single_primary_device_location_unresolved"
            if single_primary
            else "cross_view_identity_unresolved"
        )
    elif floor_scope_issues or (templates and not physical_devices):
        overall_status = "device_location_floor_scope_unresolved"
    elif location_missing_fields or duplicate_device_id_count:
        overall_status = "device_location_completeness_failed"
    elif visibility_unverified:
        overall_status = (
            "single_primary_location_visibility_unverified"
            if single_primary
            else "cross_view_identity_consistent_visibility_unverified"
        )
    elif single_primary:
        overall_status = "single_primary_device_location_complete"
    else:
        overall_status = "cross_view_quantity_closed"

    prefix = args.prefix or str(manifest.get("output_prefix") or "V12跨视图")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    occurrence_fields = [
        "source_id",
        "view_type",
        "source_frame_id",
        "source_decision",
        "raw_instance_key",
        "raw_parent_key",
        "building_id",
        "mapping_method",
        "mapping_status",
        "physical_template_id",
        "axis_x_low",
        "axis_x_high",
        "axis_x_fraction",
        "axis_y_low",
        "axis_y_high",
        "axis_y_fraction",
        "axis_fraction_delta_x",
        "axis_fraction_delta_y",
        "parent_u",
        "parent_v",
        "parent_delta_u",
        "parent_delta_v",
        "world_x",
        "world_y",
        "visibility_state",
        "effective_visible_database",
        "entity_visible",
        "effective_layer",
        "visibility_reason",
        "geometry_signature",
        "source_reason",
    ]
    for row in occurrences:
        for field in (
            "axis_x_fraction",
            "axis_y_fraction",
            "axis_fraction_delta_x",
            "axis_fraction_delta_y",
            "parent_u",
            "parent_v",
            "parent_delta_u",
            "parent_delta_v",
        ):
            row[field] = format_fraction(row.get(field))
    write_csv(
        output_dir / f"{prefix}.device_occurrence.csv",
        occurrences,
        occurrence_fields,
    )
    template_fields = [
        "physical_template_id",
        "building_id",
        "axis_position_key",
        "axis_x_low",
        "axis_x_high",
        "axis_x_fraction",
        "axis_y_low",
        "axis_y_high",
        "axis_y_fraction",
        "primary_source_id",
        "primary_frame_id",
        "primary_instance_key",
        "primary_world_x",
        "primary_world_y",
        "location_method",
        *[f"{source_id}_occurrence_count" for source_id in source_ids],
        "missing_sources",
        "evidence_status",
    ]
    write_csv(
        output_dir / f"{prefix}.physical_template.csv",
        templates,
        template_fields,
    )
    write_csv(
        output_dir / f"{prefix}.physical_device.csv",
        physical_devices,
        [
            "physical_device_id",
            "physical_template_id",
            "building_id",
            "floor",
            "axis_position_key",
            "axis_x_low",
            "axis_x_high",
            "axis_x_fraction",
            "axis_y_low",
            "axis_y_high",
            "axis_y_fraction",
            "primary_source_id",
            "primary_frame_id",
            "primary_instance_key",
            "primary_world_x",
            "primary_world_y",
            "location_method",
            "floor_evidence_source",
            "location_status",
            "evidence_status",
        ],
    )
    write_csv(
        output_dir / f"{prefix}.source_summary.csv",
        source_rows,
        [
            "source_id",
            "view_type",
            "raw_leaf_count",
            "expanded_occurrence_count",
            "mapped_occurrence_count",
            "unresolved_occurrence_count",
            "unique_physical_template_count",
            "visibility_state",
            "visibility_record_count",
            "visibility_matched_selected_count",
            "visibility_visible_selected_count",
            "visibility_hidden_selected_count",
            "visibility_missing_selected_count",
            "viewport_record_count",
            "visibility_blocking_error_count",
        ],
    )

    template_by_building = Counter(row["building_id"] for row in templates)
    device_by_building = Counter(row["building_id"] for row in physical_devices)
    max_axis_dx = max(
        (
            optional_float(row.get("axis_fraction_delta_x")) or 0.0
            for row in occurrences
        ),
        default=0.0,
    )
    max_axis_dy = max(
        (
            optional_float(row.get("axis_fraction_delta_y")) or 0.0
            for row in occurrences
        ),
        default=0.0,
    )
    report_lines = [
        f"# {prefix}：跨视图物理设备归一",
        "",
        "## 结论",
        "",
        f"- 状态：`{overall_status}`。",
        f"- 主视图楼栋—模板物理位置：{len(templates)}。",
        f"- 楼层展开物理设备候选：{len(physical_devices)}。",
        f"- 跨视图展开出现记录：{len(occurrences)}；未归一：{len(unresolved_occurrences)}。",
        f"- 主视图位置未决：{len(primary_issues)}；"
        f"楼层范围问题：{len(floor_scope_issues)}。",
        f"- 定位字段缺失：{location_missing_fields}；"
        f"设备ID重复：{duplicate_device_id_count}。",
        f"- 轴间相对位置最大匹配偏差：X={max_axis_dx:.4f}，Y={max_axis_dy:.4f}。",
        "- 数量仅表示训练用设计布置候选，不代表合同、供货、生产或正式审图数量。",
        "",
        "## 输入与方法",
        "",
        f"- 输入清单：`{args.manifest.resolve()}`",
        f"- 主视图：`{primary_source_id}`",
        "- 优先主键：楼栋 + 数字轴间位置 + 字母轴间位置。",
        "- 轴网不可用时：以语义父容器内的归一化局部坐标匹配主视图。",
        "- 数量表只展开已识别主模板，不补齐跨视图设备。",
        f"- 楼层展开证据：`{floor_evidence_source}`。",
        "",
        "## 各视图结果",
        "",
        "| 来源 | 角色 | 原始叶子 | 楼栋展开出现 | 已归一 | 未归一 | 唯一模板 | 可见/隐藏/缺记录 | 可见性状态 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in source_rows:
        report_lines.append(
            "| {source_id} | {view_type} | {raw_leaf_count} | "
            "{expanded_occurrence_count} | {mapped_occurrence_count} | "
            "{unresolved_occurrence_count} | {unique_physical_template_count} | "
            "{visibility_visible_selected_count}/"
            "{visibility_hidden_selected_count}/"
            "{visibility_missing_selected_count} | "
            "{visibility_state} |".format(**row)
        )
    report_lines.extend(
        [
            "",
            "## 按楼栋",
            "",
            "| 楼栋 | 模板位置 | 楼层展开候选 |",
            "| --- | ---: | ---: |",
        ]
    )
    for building_id in sorted(template_by_building, key=building_sort_key):
        report_lines.append(
            f"| {building_id} | {template_by_building[building_id]} | "
            f"{device_by_building[building_id]} |"
        )
    report_lines.extend(
        [
            "",
            "## 边界",
            "",
            "- `confirmed_visible_database` 表示 V13 已按相同 `instance_key` "
            "确认实体、父实例及有效图层在 DWG 数据库层可见。",
            "- 当前纳入来源若均为模型空间且V13未发现布局视口，则无需追加"
            "逐视口冻结判断。任一来源存在布局视口时，状态会停在 "
            "`api_database_visible_viewport_visibility_unverified`，等待V14复核。",
            "- 若状态为 `cross_view_identity_consistent_visibility_unverified`，"
            "说明物理位置身份已闭合，但可见性证据仍有缺口。",
            "- 本次 `cross_view_quantity_closed` 只关闭 API 数据库识别范围内的"
            "跨视图不重不漏；不代表打印效果、合同、供货、生产或正式审图数量。",
            "- 任一跨视图出现无法映射到主视图，状态必须退回 "
            "`cross_view_identity_unresolved`，不得用数量表补齐。",
            "- 单栋且楼层范围明确时可由清单范围展开；多栋共用图必须提供"
            "逐栋楼层调和证据，禁止自动将同一平面乘到所有楼栋。",
            "",
            "## 输出",
            "",
            f"- `{output_dir / (prefix + '.device_occurrence.csv')}`",
            f"- `{output_dir / (prefix + '.physical_template.csv')}`",
            f"- `{output_dir / (prefix + '.physical_device.csv')}`",
            f"- `{output_dir / (prefix + '.source_summary.csv')}`",
        ]
    )
    report_path = output_dir / f"{prefix}.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": overall_status,
                "physical_template_count": len(templates),
                "physical_device_count": len(physical_devices),
                "occurrence_count": len(occurrences),
                "unresolved_occurrence_count": len(unresolved_occurrences),
                "primary_location_issue_count": len(primary_issues),
                "floor_scope_issue_count": len(floor_scope_issues),
                "location_missing_field_count": location_missing_fields,
                "duplicate_device_id_count": duplicate_device_id_count,
                "floor_evidence_source": floor_evidence_source,
                "physical_device_csv": str(
                    output_dir / f"{prefix}.physical_device.csv"
                ),
                "physical_template_csv": str(
                    output_dir / f"{prefix}.physical_template.csv"
                ),
                "device_occurrence_csv": str(
                    output_dir / f"{prefix}.device_occurrence.csv"
                ),
                "report": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
