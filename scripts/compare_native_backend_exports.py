#!/usr/bin/env python3
"""Compare the evidence-critical fields emitted by ZWCAD and AutoCAD 2023.

The exporters share source code, but native hosts can still disagree about
extents, proxy objects, dynamic blocks, fonts, and malformed database objects.
This comparison therefore reports field groups and never promotes either host
to universal backend equivalence.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


EXPORT_SUFFIXES = {
    "text": "cad_text_export_v5",
    "frame": "cad_frame_export_v5",
    "symbol": "cad_symbol_export_v6",
    "oriented_text": "cad_oriented_text_export_v7",
    "primitive": "cad_primitive_export_v10",
    "visibility": "cad_visibility_export_v13",
}


def load_export(directory: Path, stem: str, suffix: str) -> dict[str, Any]:
    path = directory / f"{stem}.{suffix}.json"
    with path.open("r", encoding="utf-8-sig") as stream:
        return json.load(stream)


def quantize(value: Any, tolerance: float) -> Any:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        if tolerance <= 0:
            return value
        return round(float(value) / tolerance)
    if isinstance(value, list):
        return tuple(quantize(item, tolerance) for item in value)
    if isinstance(value, dict):
        return tuple(
            (key, quantize(value[key], tolerance)) for key in sorted(value)
        )
    return str(value)


def projected(record: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: record.get(field) for field in fields}


def is_paper_viewport(record: dict[str, Any]) -> bool:
    if "is_paper_viewport" in record:
        return bool(record.get("is_paper_viewport"))
    try:
        return int(record.get("number")) == 1
    except (TypeError, ValueError):
        return False


def compare_multiset(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    fields: list[str],
    tolerance: float,
) -> dict[str, Any]:
    left_values = Counter(
        quantize(projected(record, fields), tolerance) for record in left
    )
    right_values = Counter(
        quantize(projected(record, fields), tolerance) for record in right
    )
    matched = sum((left_values & right_values).values())
    return {
        "zwcad_count": len(left),
        "autocad_count": len(right),
        "matched": matched,
        "zwcad_only": len(left) - matched,
        "autocad_only": len(right) - matched,
        "consistent": matched == len(left) == len(right),
    }


def compare_by_key(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    key: str,
    fields: list[str],
    tolerance: float,
) -> dict[str, Any]:
    left_map = {str(record.get(key, "")): record for record in left}
    right_map = {str(record.get(key, "")): record for record in right}
    common = sorted(set(left_map) & set(right_map))
    matched = 0
    examples: list[dict[str, Any]] = []
    for value in common:
        left_value = quantize(projected(left_map[value], fields), tolerance)
        right_value = quantize(projected(right_map[value], fields), tolerance)
        if left_value == right_value:
            matched += 1
        elif len(examples) < 5:
            different_fields = [
                field
                for field in fields
                if quantize(left_map[value].get(field), tolerance)
                != quantize(right_map[value].get(field), tolerance)
            ]
            examples.append({"key": value, "different_fields": different_fields})
    left_only = sorted(set(left_map) - set(right_map))
    right_only = sorted(set(right_map) - set(left_map))
    return {
        "zwcad_count": len(left_map),
        "autocad_count": len(right_map),
        "common_keys": len(common),
        "matched": matched,
        "field_mismatch": len(common) - matched,
        "zwcad_only": len(left_only),
        "autocad_only": len(right_only),
        "examples": examples,
        "consistent": (
            matched == len(common)
            and not left_only
            and not right_only
            and len(left_map) == len(left)
            and len(right_map) == len(right)
        ),
    }


TEXT_FIELDS = [
    "entity_type",
    "origin",
    "text",
    "x",
    "y",
    "z",
    "layer",
    "space",
    "handle",
    "block_path",
]
FRAME_LINE_FIELDS = [
    "entity_type",
    "origin",
    "space",
    "layer",
    "handle",
    "block_path",
    "start_x",
    "start_y",
    "end_x",
    "end_y",
]
SYMBOL_CORE_FIELDS = [
    "instance_handle",
    "definition_handle",
    "parent_instance_key",
    "parent_instance_handle",
    "root_instance_handle",
    "instance_path",
    "name_path",
    "block_name",
    "effective_name",
    "is_dynamic",
    "space",
    "layer",
    "x",
    "y",
    "z",
    "local_rotation_radians",
    "scale_x",
    "scale_y",
    "scale_z",
    "attributes",
    "definition_texts",
]
ORIENTED_CORE_FIELDS = [
    "entity_type",
    "origin",
    "text",
    "x",
    "y",
    "z",
    "local_rotation_radians",
    "world_rotation_radians",
    "world_axis_x",
    "world_axis_y",
    "layer",
    "space",
    "handle",
    "block_path",
]
PRIMITIVE_CORE_FIELDS = [
    "entity_type",
    "origin",
    "root_instance_handle",
    "block_path",
    "handle",
    "layer",
    "linetype",
    "color_index",
    "space",
    "endpoints_valid",
    "start_x",
    "start_y",
    "end_x",
    "end_y",
    "curve_geometry_valid",
    "curve_center_x",
    "curve_center_y",
    "curve_mid_x",
    "curve_mid_y",
    "curve_radius",
    "vertex_count",
    "closed",
]
PRIMITIVE_BOUNDS_FIELDS = [
    "bounds_valid",
    "min_x",
    "min_y",
    "max_x",
    "max_y",
    "center_x",
    "center_y",
    "width",
    "height",
]
VISIBILITY_CORE_FIELDS = [
    "instance_handle",
    "definition_handle",
    "parent_instance_key",
    "parent_instance_handle",
    "root_instance_handle",
    "name_path",
    "block_name",
    "effective_name",
    "is_dynamic",
    "space",
    "own_layer",
    "effective_layer",
    "inherits_layer_zero",
    "entity_visible",
    "parent_effective_visible",
    "effective_visible_database",
    "visibility_reason",
    "own_layer_state",
    "effective_layer_state",
    "dynamic_properties",
]


def compare_exports(
    zwcad_dir: Path,
    autocad_dir: Path,
    stem: str,
    tolerance: float,
) -> dict[str, Any]:
    exports = {
        name: (
            load_export(zwcad_dir, stem, suffix),
            load_export(autocad_dir, stem, suffix),
        )
        for name, suffix in EXPORT_SUFFIXES.items()
    }
    text_z, text_a = exports["text"]
    frame_z, frame_a = exports["frame"]
    symbol_z, symbol_a = exports["symbol"]
    oriented_z, oriented_a = exports["oriented_text"]
    primitive_z, primitive_a = exports["primitive"]
    visibility_z, visibility_a = exports["visibility"]
    all_viewports_z = list(visibility_z.get("viewports", []))
    all_viewports_a = list(visibility_a.get("viewports", []))
    model_viewports_z = [
        record for record in all_viewports_z if not is_paper_viewport(record)
    ]
    model_viewports_a = [
        record for record in all_viewports_a if not is_paper_viewport(record)
    ]
    viewport_core_fields = sorted(
        set().union(
            *(record.keys() for record in model_viewports_z),
            *(record.keys() for record in model_viewports_a),
        )
        - {"handle", "number"}
    )

    comparisons = {
        "text_core": compare_multiset(
            text_z.get("records", []),
            text_a.get("records", []),
            TEXT_FIELDS,
            tolerance,
        ),
        "frame_lines": compare_multiset(
            frame_z.get("line_segments", []),
            frame_a.get("line_segments", []),
            FRAME_LINE_FIELDS,
            tolerance,
        ),
        "symbol_core": compare_by_key(
            symbol_z.get("records", []),
            symbol_a.get("records", []),
            "instance_key",
            SYMBOL_CORE_FIELDS,
            tolerance,
        ),
        "oriented_text_core": compare_by_key(
            oriented_z.get("records", []),
            oriented_a.get("records", []),
            "record_key",
            ORIENTED_CORE_FIELDS,
            tolerance,
        ),
        "primitive_core": compare_by_key(
            primitive_z.get("records", []),
            primitive_a.get("records", []),
            "record_key",
            PRIMITIVE_CORE_FIELDS,
            tolerance,
        ),
        "visibility_instance_core": compare_by_key(
            visibility_z.get("records", []),
            visibility_a.get("records", []),
            "instance_key",
            VISIBILITY_CORE_FIELDS,
            tolerance,
        ),
        "visibility_layers": compare_by_key(
            visibility_z.get("layers", []),
            visibility_a.get("layers", []),
            "name",
            sorted(
                set().union(
                    *(record.keys() for record in visibility_z.get("layers", [])),
                    *(record.keys() for record in visibility_a.get("layers", [])),
                )
                - {"name"}
            ),
            tolerance,
        ),
        "visibility_layouts": compare_by_key(
            visibility_z.get("layouts", []),
            visibility_a.get("layouts", []),
            "layout_handle",
            sorted(
                set().union(
                    *(record.keys() for record in visibility_z.get("layouts", [])),
                    *(record.keys() for record in visibility_a.get("layouts", [])),
                )
                - {"layout_handle"}
            ),
            tolerance,
        ),
        "visibility_viewports": compare_by_key(
            model_viewports_z,
            model_viewports_a,
            "handle",
            viewport_core_fields,
            tolerance,
        ),
    }
    core_consistent = all(value["consistent"] for value in comparisons.values())
    primitive_bounds = compare_by_key(
        primitive_z.get("records", []),
        primitive_a.get("records", []),
        "record_key",
        PRIMITIVE_BOUNDS_FIELDS,
        tolerance,
    )
    number_diagnostics = compare_by_key(
        model_viewports_z,
        model_viewports_a,
        "handle",
        ["number"],
        tolerance,
    )
    return {
        "schema_version": "native-cad-backend-comparison/0.1",
        "drawing_stem": stem,
        "tolerance": tolerance,
        "status": (
            "native_core_fields_consistent"
            if core_consistent
            else "native_core_field_comparison_unresolved"
        ),
        "backend_equivalent": False,
        "absence_proven": False,
        "comparisons": comparisons,
        "host_extent_diagnostics": {
            "zwcad_frame_bounds_candidates": len(frame_z.get("bounds_candidates", [])),
            "autocad_frame_bounds_candidates": len(frame_a.get("bounds_candidates", [])),
            "zwcad_symbol_bounds_unavailable": symbol_z.get(
                "bounds_unavailable_count", 0
            ),
            "autocad_symbol_bounds_unavailable": symbol_a.get(
                "bounds_unavailable_count", 0
            ),
            "zwcad_oriented_bounds_unavailable": oriented_z.get(
                "bounds_unavailable_count", 0
            ),
            "autocad_oriented_bounds_unavailable": oriented_a.get(
                "bounds_unavailable_count", 0
            ),
            "primitive_bounds": primitive_bounds,
            "zwcad_all_viewport_count": len(all_viewports_z),
            "autocad_all_viewport_count": len(all_viewports_a),
            "zwcad_paper_viewport_count": sum(
                is_paper_viewport(record) for record in all_viewports_z
            ),
            "autocad_paper_viewport_count": sum(
                is_paper_viewport(record) for record in all_viewports_a
            ),
            "viewport_number_diagnostics": number_diagnostics,
            "note": (
                "Native text/font extents, the mandatory paper-space viewport "
                "camera values, and runtime viewport numbers are diagnostic."
            ),
        },
    }


def write_markdown(result: dict[str, Any], path: Path) -> None:
    lines = [
        "# AutoCAD 2023 与中望 CAD 字段级对照",
        "",
        f"- 图纸：`{result['drawing_stem']}`",
        f"- 状态：`{result['status']}`",
        "- 后端整体等价：`false`",
        "- 阴性结果证明不存在：`false`",
        "",
        "| 字段组 | 中望 | AutoCAD | 匹配 | 中望独有 | AutoCAD独有 | 状态 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for name, value in result["comparisons"].items():
        lines.append(
            "| {name} | {z} | {a} | {matched} | {zo} | {ao} | {status} |".format(
                name=name,
                z=value["zwcad_count"],
                a=value["autocad_count"],
                matched=value["matched"],
                zo=value["zwcad_only"],
                ao=value["autocad_only"],
                status="一致" if value["consistent"] else "未闭合",
            )
        )
    diagnostics = result["host_extent_diagnostics"]
    lines.extend(
        [
            "",
            "## 宿主外包框诊断",
            "",
            f"- 图框候选：中望 {diagnostics['zwcad_frame_bounds_candidates']}，AutoCAD {diagnostics['autocad_frame_bounds_candidates']}。",
            f"- V6外包框不可用：中望 {diagnostics['zwcad_symbol_bounds_unavailable']}，AutoCAD {diagnostics['autocad_symbol_bounds_unavailable']}。",
            f"- V7文字外包框不可用：中望 {diagnostics['zwcad_oriented_bounds_unavailable']}，AutoCAD {diagnostics['autocad_oriented_bounds_unavailable']}。",
            "- V10基础几何外包框：中望 {z}，AutoCAD {a}，匹配 {m}。".format(
                z=diagnostics["primitive_bounds"]["zwcad_count"],
                a=diagnostics["primitive_bounds"]["autocad_count"],
                m=diagnostics["primitive_bounds"]["matched"],
            ),
            "- 视口总数：中望 {z}，AutoCAD {a}；其中纸空间整体视口 {zp}/{ap}。".format(
                z=diagnostics["zwcad_all_viewport_count"],
                a=diagnostics["autocad_all_viewport_count"],
                zp=diagnostics["zwcad_paper_viewport_count"],
                ap=diagnostics["autocad_paper_viewport_count"],
            ),
            "- 模型展示视口的运行时编号差异：{n}；编号只作诊断，Handle 仍是对照键。".format(
                n=diagnostics["viewport_number_diagnostics"]["field_mismatch"],
            ),
            "- 字体和宿主外包框差异只作诊断，不覆盖Handle、文字、WCS、方向或可见性核心字段。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--zwcad-dir", required=True, type=Path)
    parser.add_argument("--autocad-dir", required=True, type=Path)
    parser.add_argument("--stem", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = compare_exports(
        args.zwcad_dir,
        args.autocad_dir,
        args.stem,
        args.tolerance,
    )
    json_path = args.output_dir / "native-backend-comparison.json"
    markdown_path = args.output_dir / "native-backend-comparison.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_markdown(result, markdown_path)
    print(json_path)
    print(markdown_path)
    print(result["status"])
    return 0 if result["status"] == "native_core_fields_consistent" else 2


if __name__ == "__main__":
    raise SystemExit(main())
