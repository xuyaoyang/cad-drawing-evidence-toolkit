#!/usr/bin/env python3
"""把阻尼器候选映射到布局视口，防止按布局页重复计数。

输入为 V13 可见性 JSON 和阻尼器候选 CSV。脚本只读旁路证据，不读取或修改
DWG。模型空间唯一 instance_key 始终是计数键；视口只证明候选在哪些出图窗口
中可见。非俯视视口、非矩形裁剪、缺失可见性记录或导出错误均安全停止。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分析布局视口中的阻尼器可见性")
    parser.add_argument("visibility_json", type=Path)
    parser.add_argument("candidate_csv", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", default="布局视口阻尼器可见性")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def top_plan_viewport(viewport: dict[str, Any], tolerance: float = 1e-8) -> bool:
    return (
        abs(float(viewport.get("view_direction_x") or 0.0)) <= tolerance
        and abs(float(viewport.get("view_direction_y") or 0.0)) <= tolerance
        and abs(float(viewport.get("view_direction_z") or 0.0) - 1.0)
        <= tolerance
        and float(viewport.get("paper_width") or 0.0) > 0.0
        and float(viewport.get("paper_height") or 0.0) > 0.0
        and float(viewport.get("view_height") or 0.0) > 0.0
    )


def inside_view_rectangle(
    x: float, y: float, viewport: dict[str, Any]
) -> bool:
    """将 WCS 顶视点转到视口 DCS，判断是否进入矩形视窗。"""
    target_x = float(viewport.get("view_target_x") or 0.0)
    target_y = float(viewport.get("view_target_y") or 0.0)
    angle = float(viewport.get("twist_angle") or 0.0)
    dx = x - target_x
    dy = y - target_y
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dcs_x = cos_a * dx - sin_a * dy
    dcs_y = sin_a * dx + cos_a * dy

    view_height = float(viewport["view_height"])
    model_width = (
        view_height
        * float(viewport["paper_width"])
        / float(viewport["paper_height"])
    )
    center_x = float(viewport.get("view_center_x") or 0.0)
    center_y = float(viewport.get("view_center_y") or 0.0)
    tolerance = max(model_width, view_height) * 1e-9
    return (
        abs(dcs_x - center_x) <= model_width / 2.0 + tolerance
        and abs(dcs_y - center_y) <= view_height / 2.0 + tolerance
    )


def viewport_content_enabled(viewport: dict[str, Any]) -> bool:
    # 视口边框位于“不打印”层是正常做法；IsPlottable 不控制视口内模型内容。
    state = viewport.get("layer_state") or {}
    return (
        bool(viewport.get("on"))
        and bool(viewport.get("entity_visible"))
        and not bool(state.get("is_off"))
        and not bool(state.get("is_frozen"))
        and not bool(state.get("is_hidden"))
    )


def is_paper_viewport(viewport: dict[str, Any]) -> bool:
    """识别每个布局自动拥有的整体纸空间视口。

    新版 V13 直接给出 is_paper_viewport；旧证据兼容使用 Number == 1。
    该视口描述纸空间本身，不是展示模型内容的浮动视口。
    """
    if "is_paper_viewport" in viewport:
        return bool(viewport.get("is_paper_viewport"))
    try:
        return int(viewport.get("number")) == 1
    except (TypeError, ValueError):
        return False


def main() -> int:
    args = parse_args()
    visibility = json.loads(
        args.visibility_json.read_text(encoding="utf-8-sig")
    )
    candidates = [
        row
        for row in read_csv(args.candidate_csv)
        if (row.get("semantic_leaf_symbol") or "").lower() == "true"
    ]
    visibility_by_key = {
        str(row.get("instance_key") or ""): row
        for row in visibility.get("records", [])
        if row.get("instance_key")
    }
    all_viewports = list(visibility.get("viewports", []))
    paper_viewports = [
        viewport for viewport in all_viewports if is_paper_viewport(viewport)
    ]
    viewports = [
        viewport for viewport in all_viewports if not is_paper_viewport(viewport)
    ]
    layouts = list(visibility.get("layouts", []))

    blocking_error_fields = (
        "unknown_visibility_instance_count",
        "skipped_object_error_count",
        "dynamic_property_read_error_count",
        "layer_read_error_count",
        "entity_visibility_read_error_count",
        "viewport_read_error_count",
        "viewport_frozen_layer_read_error_count",
    )
    blocking_errors = {
        field: int(visibility.get(field) or 0)
        for field in blocking_error_fields
        if int(visibility.get(field) or 0)
    }

    result_rows: list[dict[str, Any]] = []
    viewport_counts: dict[str, Counter[str]] = {
        str(viewport.get("handle") or ""): Counter() for viewport in viewports
    }
    unresolved_viewports = [
        viewport
        for viewport in viewports
        if not top_plan_viewport(viewport)
    ]

    for candidate in candidates:
        instance_key = candidate.get("instance_key") or ""
        x = as_float(candidate.get("x"))
        y = as_float(candidate.get("y"))
        record = visibility_by_key.get(instance_key)
        containing: list[dict[str, Any]] = []
        exact_visible: list[dict[str, Any]] = []
        clip_unverified: list[dict[str, Any]] = []
        layer_hidden: list[dict[str, Any]] = []

        if x is not None and y is not None:
            for viewport in viewports:
                if not top_plan_viewport(viewport):
                    continue
                if not inside_view_rectangle(x, y, viewport):
                    continue
                containing.append(viewport)
                handle = str(viewport.get("handle") or "")
                viewport_counts[handle]["candidate_in_rectangle"] += 1
                if bool(viewport.get("non_rect_clip_on")):
                    clip_unverified.append(viewport)
                    viewport_counts[handle]["non_rect_clip_unverified"] += 1
                    continue
                frozen_layers = {
                    str(value).casefold()
                    for value in viewport.get("frozen_layers", [])
                }
                effective_layer = (
                    str(record.get("effective_layer") or "")
                    if record is not None
                    else ""
                )
                if effective_layer.casefold() in frozen_layers:
                    layer_hidden.append(viewport)
                    viewport_counts[handle]["candidate_layer_frozen"] += 1
                    continue
                if (
                    record is not None
                    and bool(record.get("effective_visible_database"))
                    and viewport_content_enabled(viewport)
                ):
                    exact_visible.append(viewport)
                    viewport_counts[handle]["candidate_visible"] += 1

        if record is None:
            state = "visibility_record_missing"
        elif not bool(record.get("effective_visible_database")):
            state = "hidden_in_database"
        elif exact_visible:
            state = "visible_in_layout_viewport"
        elif clip_unverified:
            state = "non_rect_clip_unverified"
        elif layer_hidden:
            state = "hidden_by_viewport_layer_freeze"
        elif containing:
            state = "matching_viewport_disabled"
        else:
            state = "layout_viewport_not_found"

        result_rows.append(
            {
                "instance_key": instance_key,
                "block_name": candidate.get("block_name") or "",
                "semantic_parent_block": candidate.get("semantic_parent_block")
                or "",
                "x": candidate.get("x") or "",
                "y": candidate.get("y") or "",
                "effective_layer": (
                    record.get("effective_layer") or "" if record else ""
                ),
                "database_visible": (
                    bool(record.get("effective_visible_database"))
                    if record
                    else ""
                ),
                "matching_viewport_count": len(containing),
                "visible_viewport_count": len(exact_visible),
                "matching_viewport_handles": ",".join(
                    str(viewport.get("handle") or "")
                    for viewport in containing
                ),
                "visible_viewport_handles": ",".join(
                    str(viewport.get("handle") or "")
                    for viewport in exact_visible
                ),
                "visibility_state": state,
            }
        )

    state_counts = Counter(row["visibility_state"] for row in result_rows)
    duplicate_display_count = sum(
        int(row["visible_viewport_count"]) > 1 for row in result_rows
    )
    unresolved_candidate_count = sum(
        row["visibility_state"] != "visible_in_layout_viewport"
        for row in result_rows
    )
    if (
        blocking_errors
        or unresolved_candidate_count
        or unresolved_viewports
    ):
        overall_status = "layout_viewport_evidence_unresolved"
    else:
        overall_status = "layout_viewport_visibility_consistent"

    viewport_rows: list[dict[str, Any]] = []
    for viewport in all_viewports:
        handle = str(viewport.get("handle") or "")
        counts = viewport_counts.get(handle, Counter())
        viewport_rows.append(
            {
                "layout_name": viewport.get("layout_name") or "",
                "layout_tab_order": viewport.get("layout_tab_order"),
                "viewport_handle": handle,
                "viewport_number": viewport.get("number"),
                "is_paper_viewport": is_paper_viewport(viewport),
                "on": viewport.get("on"),
                "entity_visible": viewport.get("entity_visible"),
                "layer": (viewport.get("layer_state") or {}).get("name", ""),
                "top_plan_geometry": top_plan_viewport(viewport),
                "non_rect_clip_on": viewport.get("non_rect_clip_on"),
                "frozen_layer_count": len(viewport.get("frozen_layers", [])),
                "candidate_in_rectangle": counts["candidate_in_rectangle"],
                "candidate_visible": counts["candidate_visible"],
                "candidate_layer_frozen": counts[
                    "candidate_layer_frozen"
                ],
                "non_rect_clip_unverified": counts[
                    "non_rect_clip_unverified"
                ],
            }
        )

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = output_dir / f"{args.prefix}.candidate_viewport.csv"
    viewport_path = output_dir / f"{args.prefix}.viewport_summary.csv"
    report_path = output_dir / f"{args.prefix}.md"
    write_csv(
        candidate_path,
        result_rows,
        [
            "instance_key",
            "block_name",
            "semantic_parent_block",
            "x",
            "y",
            "effective_layer",
            "database_visible",
            "matching_viewport_count",
            "visible_viewport_count",
            "matching_viewport_handles",
            "visible_viewport_handles",
            "visibility_state",
        ],
    )
    write_csv(
        viewport_path,
        viewport_rows,
        [
            "layout_name",
            "layout_tab_order",
            "viewport_handle",
            "viewport_number",
            "is_paper_viewport",
            "on",
            "entity_visible",
            "layer",
            "top_plan_geometry",
            "non_rect_clip_on",
            "frozen_layer_count",
            "candidate_in_rectangle",
            "candidate_visible",
            "candidate_layer_frozen",
            "non_rect_clip_unverified",
        ],
    )

    lines = [
        f"# {args.prefix}",
        "",
        "## 结论",
        "",
        f"- 状态：`{overall_status}`。",
        f"- 布局：{len(layouts)}；模型展示视口：{len(viewports)}；"
        f"纸空间整体视口：{len(paper_viewports)}。",
        f"- 阻尼器语义叶子候选：{len(candidates)}。",
        f"- 在至少一个布局视口确认可见："
        f"{state_counts['visible_in_layout_viewport']}。",
        f"- 同一模型实例在两个及以上视口重复展示："
        f"{duplicate_display_count}；仍按唯一 `instance_key` 计一次。",
        f"- 未解决候选：{unresolved_candidate_count}；"
        f"非俯视/几何不完整视口：{len(unresolved_viewports)}。",
        f"- V13 阻塞错误：{sum(blocking_errors.values())}。",
        "",
        "## 边界",
        "",
        "- 布局页或视口不是新的设备实例，禁止把各视口数量直接相加。",
        "- 非矩形裁剪视口只有矩形外包范围，候选落入其中时必须继续解析裁剪边界。",
        "- 本结果只证明 API 图面表达，不代表合同、供货、生产或正式审图数量。",
        "",
        "## 输出",
        "",
        f"- `{candidate_path}`",
        f"- `{viewport_path}`",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": overall_status,
                "candidate_count": len(candidates),
                "visible_candidate_count": state_counts[
                    "visible_in_layout_viewport"
                ],
                "duplicate_display_count": duplicate_display_count,
                "unresolved_candidate_count": unresolved_candidate_count,
                "viewport_count": len(viewports),
                "paper_viewport_count": len(paper_viewports),
                "unresolved_viewport_count": len(unresolved_viewports),
                "blocking_error_count": sum(blocking_errors.values()),
                "report": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
