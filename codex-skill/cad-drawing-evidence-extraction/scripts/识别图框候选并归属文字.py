#!/usr/bin/env python3
"""Detect outer drawing-frame candidates from exported geometry and assign text.

The method intentionally uses only geometric properties: closed polylines and
large block extents, area distribution, containment removal, and
point-in-rectangle assignment.
It never uses a drawing number, a title-block name, a fixed paper size, or a
fixed coordinate direction.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SOURCE = ROOT / "CAD文件" / "cad_text_export_v3.json"
GEOMETRY_SOURCE = ROOT / "CAD文件" / "cad_frame_export_v4.json"
FRAME_CSV = ROOT / "输出" / "图框候选清单.csv"
TEXT_CSV = ROOT / "输出" / "文字按图框归属清单.csv"
REPORT = ROOT / "输出" / "图框几何识别与文字归属.md"


def key(box: dict) -> tuple[float, float, float, float]:
    return tuple(round(box[name], 3) for name in ("min_x", "min_y", "max_x", "max_y"))


def area(box: dict) -> float:
    return box["width"] * box["height"]


def contains(outer: dict, inner: dict, tolerance: float = 1e-6) -> bool:
    return (
        outer["min_x"] <= inner["min_x"] + tolerance
        and outer["min_y"] <= inner["min_y"] + tolerance
        and outer["max_x"] >= inner["max_x"] - tolerance
        and outer["max_y"] >= inner["max_y"] - tolerance
    )


def overlap_area(first: dict, second: dict) -> float:
    width = max(0.0, min(first["max_x"], second["max_x"]) - max(first["min_x"], second["min_x"]))
    height = max(0.0, min(first["max_y"], second["max_y"]) - max(first["min_y"], second["min_y"]))
    return width * height


def prune_secondary_area_family(frames: list[dict]) -> tuple[list[dict], float, int]:
    """Discard a much smaller, attached rectangle family after frame selection.

    The first cutoff separates drawing-scale geometry from ordinary entities.
    Some title-block layouts still leave a second family of narrow, closed
    rectangles (for example a scale strip between two sheets) above that first
    cutoff.  Only prune when the selected candidates themselves have a clear
    logarithmic area break; otherwise preserve every selected frame.
    """
    if len(frames) < 2:
        return frames, 0.0, 0
    ordered = sorted(frames, key=area, reverse=True)
    gaps = [
        (math.log10(area(ordered[index]) / area(ordered[index + 1])), index)
        for index in range(len(ordered) - 1)
        if area(ordered[index + 1]) > 0
    ]
    largest_gap, gap_index = max(gaps)
    if largest_gap < 0.7:
        return frames, largest_gap, 0
    kept = ordered[:gap_index + 1]
    return kept, largest_gap, len(frames) - len(kept)


def choose_large_boxes(
    closed_boxes: list[dict], block_boxes: list[dict], include_large_blocks: bool,
) -> tuple[list[dict], float, float, int]:
    """Select geometry-only outer frames, supplementing closed paths with blocks.

    The cutoff comes only from closed polylines, so a drawing full of arbitrary
    symbols/blocks cannot shift the frame-size threshold.  A block reference
    above that natural threshold can be a frame when the border itself lives in
    a reusable block.  A large block that substantially overlaps two or more
    other large candidates is treated as a multi-sheet/container block, not a
    sheet.  Overlap is used because adjacent sheet borders may differ by a
    narrow title-strip margin and therefore not be strictly contained.
    """
    unique: dict[tuple[float, float, float, float], dict] = {}
    for box in closed_boxes:
        unique.setdefault(key(box), box)
    values = sorted(unique.values(), key=area, reverse=True)
    if len(values) < 2:
        raise RuntimeError("闭合多段线候选不足，无法识别图框。")

    # Determine cutoff from the largest logarithmic area discontinuity. This
    # adapts to drawing units and A-series/custom elongated sheet sizes.
    gaps = []
    for index in range(len(values) - 1):
        if area(values[index + 1]) <= 0:
            continue
        gaps.append((math.log10(area(values[index]) / area(values[index + 1])), index))
    largest_gap, gap_index = max(gaps)
    if largest_gap < 0.7:  # no strong geometry separation; do not guess
        raise RuntimeError("闭合多段线面积没有明显断层，无法可靠选择图框候选。")
    cutoff = math.sqrt(area(values[gap_index]) * area(values[gap_index + 1]))
    large_closed = [box for box in values if area(box) > cutoff]

    # A closed-polyline and a block can represent the same outline. Prefer the
    # closed path in that exact-bounds case, but retain a large block when it is
    # the only available representation of a sheet border.
    candidates: dict[tuple[float, float, float, float], dict] = {
        key(box): box for box in large_closed
    }
    if include_large_blocks:
        for block in block_boxes:
            if area(block) > cutoff:
                candidates.setdefault(key(block), block)

    large = list(candidates.values())
    retained = []
    for box in large:
        if include_large_blocks and box["entity_type"] == "block-reference":
            covered_candidates = [
                other for other in large
                if key(other) != key(box)
                and area(other) >= cutoff
                and (
                    contains(box, other)
                    or overlap_area(box, other) / area(other) >= 0.80
                )
            ]
            if len(covered_candidates) >= 2:
                continue
        retained.append(box)

    frames = []
    for box in retained:
        is_inner = any(
            area(other) > area(box) and contains(other, box)
            for other in retained
        )
        if not is_inner:
            frames.append(dict(box))
    frames, secondary_gap, pruned_count = prune_secondary_area_family(frames)
    frames.sort(key=lambda box: (-box["max_y"], box["min_x"]))
    for index, frame in enumerate(frames, start=1):
        frame["frame_id"] = f"FRAME-{index:03d}"
        frame["area"] = area(frame)
    return frames, cutoff, largest_gap, len(retained) - len(large_closed), secondary_gap, pruned_count


def overlap_length(first_min: float, first_max: float, second_min: float, second_max: float) -> float:
    return max(0.0, min(first_max, second_max) - max(first_min, second_min))


def attach_touching_blocks(frames: list[dict], candidates: list[dict]) -> None:
    """Extend a sheet region with block references geometrically touching it.

    This captures a separate title-block/legend block placed immediately beside
    an outer frame, without assuming a block name, a side, or a fixed margin.
    """
    blocks = [item for item in candidates if item["entity_type"] == "block-reference"]
    tolerance = 1e-3
    for frame in frames:
        frame["region_min_x"] = frame["min_x"]
        frame["region_min_y"] = frame["min_y"]
        frame["region_max_x"] = frame["max_x"]
        frame["region_max_y"] = frame["max_y"]
        frame["attached_block_handles"] = []
        for block in blocks:
            if block["space"] != frame["space"]:
                continue
            # An adjacent object comparable in area to the frame is normally
            # another sheet/view, not a title-block add-on.  This relative
            # test prevents merging consecutive sheets in multi-sheet rows.
            if area(block) >= area(frame) * 0.20:
                continue
            horizontal_overlap = overlap_length(frame["min_x"], frame["max_x"], block["min_x"], block["max_x"])
            vertical_overlap = overlap_length(frame["min_y"], frame["max_y"], block["min_y"], block["max_y"])
            touches_vertical_edge = (
                abs(frame["max_x"] - block["min_x"]) <= tolerance
                or abs(frame["min_x"] - block["max_x"]) <= tolerance
            ) and vertical_overlap > 0.0
            touches_horizontal_edge = (
                abs(frame["max_y"] - block["min_y"]) <= tolerance
                or abs(frame["min_y"] - block["max_y"]) <= tolerance
            ) and horizontal_overlap > 0.0
            if not (touches_vertical_edge or touches_horizontal_edge):
                continue
            frame["region_min_x"] = min(frame["region_min_x"], block["min_x"])
            frame["region_min_y"] = min(frame["region_min_y"], block["min_y"])
            frame["region_max_x"] = max(frame["region_max_x"], block["max_x"])
            frame["region_max_y"] = max(frame["region_max_y"], block["max_y"])
            frame["attached_block_handles"].append(block["handle"])


def frame_for_point(x: float, y: float, space: str, frames: list[dict]) -> tuple[str, str]:
    containers = [
        frame for frame in frames
        if frame["space"] == space
        and frame["region_min_x"] <= x <= frame["region_max_x"]
        and frame["region_min_y"] <= y <= frame["region_max_y"]
    ]
    if not containers:
        return "", "不在任何图框候选内"
    containers.sort(key=lambda frame: frame["area"])
    selected = containers[0]
    in_core = (
        selected["min_x"] <= x <= selected["max_x"]
        and selected["min_y"] <= y <= selected["max_y"]
    )
    if len(containers) > 1:
        return selected["frame_id"], "位于嵌套候选内，取最小图纸区域"
    return selected["frame_id"], "坐标位于外框内" if in_core else "坐标位于与外框相接的块参照区域内"


def preview(value: str, length: int = 86) -> str:
    value = value.replace("\\P", " ").replace("\n", " ")
    value = " ".join(value.split())
    return value[:length] + ("…" if len(value) > length else "")


def main() -> None:
    global TEXT_SOURCE, GEOMETRY_SOURCE, FRAME_CSV, TEXT_CSV, REPORT
    parser = argparse.ArgumentParser(description="从 CAD 几何导出中识别图框并归属文字")
    parser.add_argument("--text", type=Path, help="文字 JSON 路径；省略时使用首个样本 V3 导出")
    parser.add_argument("--geometry", type=Path, help="几何 JSON 路径；省略时使用首个样本导出")
    parser.add_argument("--prefix", help="输出文件前缀，例如 业务楼结构图_t3")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="输出目录；省略时写入项目的输出目录",
    )
    parser.add_argument(
        "--supplement-large-block-frames",
        action="store_true",
        help="试验：将达到闭合图框面积阈值的大块参照作为候选外框；默认关闭，须人工抽查。",
    )
    args = parser.parse_args()
    if args.text:
        TEXT_SOURCE = args.text
    if args.geometry:
        GEOMETRY_SOURCE = args.geometry
    output_dir = args.output_dir or ROOT / "输出"
    if args.prefix:
        FRAME_CSV = output_dir / f"{args.prefix}.图框候选清单.csv"
        TEXT_CSV = output_dir / f"{args.prefix}.文字按图框归属清单.csv"
        REPORT = output_dir / f"{args.prefix}.图框几何识别与文字归属.md"
    elif args.output_dir:
        FRAME_CSV = output_dir / FRAME_CSV.name
        TEXT_CSV = output_dir / TEXT_CSV.name
        REPORT = output_dir / REPORT.name
    geometry = json.loads(GEOMETRY_SOURCE.read_text(encoding="utf-8-sig"))
    text_document = json.loads(TEXT_SOURCE.read_text(encoding="utf-8-sig"))
    closed = [
        item for item in geometry["bounds_candidates"]
        if item["entity_type"] == "closed-polyline" and item["width"] > 0 and item["height"] > 0
    ]
    blocks = [
        item for item in geometry["bounds_candidates"]
        if item["entity_type"] == "block-reference" and item["width"] > 0 and item["height"] > 0
    ]
    frames, cutoff, largest_gap, supplemental_blocks, secondary_gap, pruned_count = choose_large_boxes(
        closed, blocks, args.supplement_large_block_frames,
    )
    attach_touching_blocks(frames, geometry["bounds_candidates"])

    FRAME_CSV.parent.mkdir(parents=True, exist_ok=True)
    frame_fields = [
        "frame_id", "handle", "layer", "space", "min_x", "min_y", "max_x", "max_y",
        "width", "height", "area", "rotation_radians", "region_min_x", "region_min_y",
        "region_max_x", "region_max_y", "attached_block_handles", "selection_method",
    ]
    with FRAME_CSV.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=frame_fields)
        writer.writeheader()
        for frame in frames:
            output = dict(frame)
            output["attached_block_handles"] = ";".join(frame["attached_block_handles"])
            output["selection_method"] = "closed polyline or large block extent / area discontinuity / non-nested outer rectangle"
            writer.writerow({field: output.get(field, "") for field in frame_fields})

    text_rows = []
    assignments = Counter()
    relations = Counter()
    for record in text_document["records"]:
        frame_id, method = frame_for_point(record["x"], record["y"], record["space"], frames)
        output = dict(record)
        output["frame_id"] = frame_id
        output["assignment_method"] = method
        text_rows.append(output)
        assignments[frame_id or "未归属"] += 1
        relations[method] += 1

    text_fields = [
        "frame_id", "assignment_method", "entity_type", "origin", "text", "x", "y", "z",
        "layer", "space", "handle", "block_path",
    ]
    with TEXT_CSV.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=text_fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in text_fields} for row in text_rows)

    long_notes = [
        row for row in text_rows
        if row["entity_type"] == "MText" and len(row["text"]) >= 1000
    ]
    lines = [
        "# 图框几何识别与文字归属（几何验证）",
        "",
        "## 结论",
        "",
        (
            f"从 {len(closed)} 个闭合多段线中，按面积分布的最大断层（log10 比值 {largest_gap:.2f}）计算图框面积阈值，并补充 {supplemental_blocks} 个达到同一阈值、且不包住多张候选图纸的块参照，最终选出 {len(frames)} 个非嵌套外框候选。该筛选没有使用图号、标题栏名称、固定图幅、比例或坐标方向。"
            if args.supplement_large_block_frames
            else f"从 {len(closed)} 个闭合多段线中，按面积分布的最大断层（log10 比值 {largest_gap:.2f}）选出 {len(frames)} 个非嵌套外框候选。大块参照补充功能默认关闭，须另行人工抽查后才能启用。该筛选没有使用图号、标题栏名称、固定图幅、比例或坐标方向。"
        ),
        (
            f"候选外框自身还存在 log10 比值 {secondary_gap:.2f} 的第二面积断层，已剔除 {pruned_count} 个明显更小的附属闭合框。"
            if pruned_count
            else "候选外框自身没有满足阈值的第二面积断层，未额外剔除候选。"
        ),
        "",
        "文字归属以文字插入点是否位于候选外框内判断；若文字位于与外框边界精确相接的块参照范围内，也归属同一图纸区域。点在多个嵌套候选中时取面积最小的区域。文字跨框、图框由散线组成、旋转图框或未由闭合多段线/大图框块表示的图框，仍须后续扩展验证。",
        "",
        "## 图框候选",
        "",
        "| 图框 ID | 外框句柄 | 相接块参照 | 外框范围 | 合并图纸区域 | 归属文字数 |",
        "|---|---|---|---:|---:|---:|",
    ]
    for frame in frames:
        lines.append(
            f"| {frame['frame_id']} | `{frame['handle']}` | `{'; '.join(frame['attached_block_handles']) or '—'}` | "
            f"({frame['min_x']:.2f}, {frame['min_y']:.2f})—({frame['max_x']:.2f}, {frame['max_y']:.2f}) | "
            f"({frame['region_min_x']:.2f}, {frame['region_min_y']:.2f})—({frame['region_max_x']:.2f}, {frame['region_max_y']:.2f}) | {assignments[frame['frame_id']]} |"
        )
    lines.append(f"| 未归属 | — | — | — | — | — | {assignments['未归属']} |")
    lines += [
        "",
        "## 长说明文字抽查",
        "",
        "| 图框 ID | 文字预览 | 坐标 | 句柄 | 归属方式 |",
        "|---|---|---:|---|---|",
    ]
    for row in long_notes:
        escaped_preview = preview(row["text"]).replace("|", "\\|")
        lines.append(
            f"| {row['frame_id'] or '未归属'} | {escaped_preview} | "
            f"({row['x']:.2f}, {row['y']:.2f}) | `{row['handle']}` | {row['assignment_method']} |"
        )
    lines += [
        "",
        "## 输出与限制",
        "",
        f"- 图框候选清单：`输出\\{FRAME_CSV.name}`。",
        f"- 文字归属清单：`输出\\{TEXT_CSV.name}`。",
        f"- 当前运行的自然面积阈值：{cutoff:.2f} 平方图纸单位；它是本图几何分布的计算结果，不是固定参数。",
        f"- 归属方式统计：外框内 {relations['坐标位于外框内']} 条；相接块参照区域内 {relations['坐标位于与外框相接的块参照区域内']} 条；未归属 {assignments['未归属']} 条。",
        "- 原始 DWG 未被修改。",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"frames={len(frames)} assigned={len(text_rows) - assignments['未归属']} unassigned={assignments['未归属']}")
    print(f"wrote {FRAME_CSV}")
    print(f"wrote {TEXT_CSV}")
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    main()
