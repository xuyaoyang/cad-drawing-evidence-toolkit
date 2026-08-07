#!/usr/bin/env python3
"""Find CAD text tables from their nearby LINE grid, without assuming a layer.

The Core Console export supplies text coordinates and the two endpoints of
LINE entities.  A heading is only a search anchor: cells are included only
when their insertion points are inside a rectangular line enclosure.  The
result remains a candidate because merged cells, rotated text and non-LINE
table borders need visual confirmation.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Text:
    layer: str
    x: float
    y: float
    value: str


@dataclass(frozen=True)
class Line:
    layer: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def min_x(self) -> float: return min(self.x1, self.x2)
    @property
    def max_x(self) -> float: return max(self.x1, self.x2)
    @property
    def min_y(self) -> float: return min(self.y1, self.y2)
    @property
    def max_y(self) -> float: return max(self.y1, self.y2)


def point(value: str) -> tuple[float, float] | None:
    try:
        x, y, *_ = value.split(",")
        return float(x), float(y)
    except (AttributeError, ValueError):
        return None


def bounds(value: str) -> tuple[float, float, float, float] | None:
    try:
        x0, y0, x1, y1 = value.split(",")
        return float(x0), float(y0), float(x1), float(y1)
    except (AttributeError, ValueError):
        return None


def clean(value: str) -> str:
    return " ".join(value.replace("\\P", " ").replace("\n", " ").split())


def read(path: Path) -> tuple[list[Text], list[Line]]:
    texts, lines = [], []
    with path.open(encoding="gbk", errors="replace", newline="") as source:
        for row in csv.DictReader(source, delimiter="\t"):
            start = point(row.get("POINT", ""))
            if not start:
                continue
            typ = row.get("TYPE", "")
            if typ in {"TEXT", "MTEXT"}:
                value = clean(row.get("VALUE", ""))
                if value:
                    texts.append(Text(row.get("LAYER", ""), *start, value))
            elif typ == "LINE" and row.get("EXTRA", "").startswith("end="):
                end = point(row["EXTRA"][4:])
                if end:
                    lines.append(Line(row.get("LAYER", ""), *start, *end))
            elif typ == "LWPOLYLINE" and row.get("EXTRA", "").startswith("bounds="):
                # Some CADs use a two-vertex LWPOLYLINE for each table rule.
                # The exporter records its vertex bounds.  Treat only a
                # degenerate bound (one dimension is zero) as a straight rule;
                # a closed rectangular polyline must not be mistaken for one.
                box = bounds(row["EXTRA"][7:])
                if box and (abs(box[0] - box[2]) < 1e-6 or abs(box[1] - box[3]) < 1e-6):
                    lines.append(Line(row.get("LAYER", ""), box[0], box[1], box[2], box[3]))
    return texts, lines


def is_horizontal(line: Line, tol: float) -> bool:
    return abs(line.y1 - line.y2) <= tol and line.max_x - line.min_x > tol * 3


def is_vertical(line: Line, tol: float) -> bool:
    return abs(line.x1 - line.x2) <= tol and line.max_y - line.min_y > tol * 3


def covers_vertical(line: Line, x: float, y0: float, y1: float, tol: float) -> bool:
    return abs(line.x1 - x) <= tol and line.min_y <= y0 + tol and line.max_y >= y1 - tol


def inside(text: Text, box: tuple[float, float, float, float], tol: float) -> bool:
    x0, y0, x1, y1 = box
    return x0 - tol <= text.x <= x1 + tol and y0 - tol <= text.y <= y1 + tol


def boxes_for_anchor(anchor: Text, texts: list[Text], lines: list[Line], args: argparse.Namespace) -> list[tuple[tuple[float, float, float, float], list[Text], float]]:
    x_min, x_max = anchor.x - args.search_width, anchor.x + args.search_width
    y_min, y_max = anchor.y - args.search_height, anchor.y + args.search_height
    local = [line for line in lines if line.max_x >= x_min and line.min_x <= x_max and line.max_y >= y_min and line.min_y <= y_max]
    hs = [line for line in local if is_horizontal(line, args.line_tolerance)]
    vs = [line for line in local if is_vertical(line, args.line_tolerance)]
    found: dict[tuple[int, int, int, int], tuple[tuple[float, float, float, float], list[Text], float]] = {}
    for upper in hs:
        for lower in hs:
            if upper is lower:
                continue
            y0, y1 = sorted(((upper.y1 + upper.y2) / 2, (lower.y1 + lower.y2) / 2))
            if y1 - y0 < args.min_height:
                continue
            # Both horizontal borders must overlap; the verticals then define
            # the exact outer x edges of the enclosure.
            overlap0, overlap1 = max(upper.min_x, lower.min_x), min(upper.max_x, lower.max_x)
            if overlap1 - overlap0 < args.min_width:
                continue
            lefts = [line for line in vs if overlap0 - args.line_tolerance <= line.x1 <= overlap1 + args.line_tolerance and covers_vertical(line, line.x1, y0, y1, args.line_tolerance)]
            for left in lefts:
                rights = [line for line in vs if line.x1 > left.x1 + args.min_width and line.x1 <= overlap1 + args.line_tolerance and covers_vertical(line, line.x1, y0, y1, args.line_tolerance)]
                for right in rights:
                    box = (left.x1, y0, right.x1, y1)
                    if not (box[0] - args.heading_x_tolerance <= anchor.x <= box[2] + args.heading_x_tolerance):
                        continue
                    gap = 0.0 if box[1] <= anchor.y <= box[3] else min(abs(anchor.y - box[1]), abs(anchor.y - box[3]))
                    if gap > args.heading_gap:
                        continue
                    cells = [text for text in texts if inside(text, box, args.text_tolerance)]
                    if len(cells) < args.min_cells:
                        continue
                    key = tuple(round(value / args.dedup_tolerance) for value in box)
                    old = found.get(key)
                    if old is None or len(cells) > len(old[1]):
                        found[key] = (box, cells, gap)
    # Prefer the enclosure that holds the most cells.  A smaller inner cell
    # never replaces the full table merely because it is closer to the title.
    return sorted(found.values(), key=lambda item: (-len(item[1]), item[2], -(item[0][2]-item[0][0])*(item[0][3]-item[0][1])))


def row_groups(cells: list[Text], tolerance: float) -> list[list[Text]]:
    groups: list[list[Text]] = []
    for cell in sorted(cells, key=lambda c: -c.y):
        if not groups or abs(cell.y - sum(c.y for c in groups[-1]) / len(groups[-1])) > tolerance:
            groups.append([cell])
        else:
            groups[-1].append(cell)
    return groups


def write_matrix(report: list[str], ident: str, anchor: Text, box: tuple[float, float, float, float], cells: list[Text], gap: float) -> None:
    report += [
        f"## {ident}", "",
        f"- 表头锚点：`{anchor.value}` @ ({anchor.x:.3f}, {anchor.y:.3f})。",
        f"- 线框范围：({box[0]:.3f}, {box[1]:.3f})—({box[2]:.3f}, {box[3]:.3f})；表头到边框距离：{gap:.3f}。",
        f"- 框内文字实体：{len(cells)}。只采用线框内文字；不把相邻说明文字并入。", "",
        "| 行 | X坐标排序后的原始文字 |", "|---|---|",
    ]
    for index, group in enumerate(row_groups(cells, 100.0), 1):
        content = " / ".join(cell.value.replace("|", "\\|") for cell in sorted(group, key=lambda c: c.x))
        report.append(f"| {index} | {content} |")
    report.append("")


def main() -> None:
    parser = argparse.ArgumentParser(description="用 CAD LINE 表格线约束文字表格候选")
    parser.add_argument("entities", type=Path)
    parser.add_argument("--prefix", required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="输出目录；省略时使用项目根目录下的“输出”",
    )
    parser.add_argument("--header-pattern", default=r"阻尼器.*参数表|消能器.*参数|性能及技术参数|参数表|性能表")
    parser.add_argument("--search-width", type=float, default=30000.0)
    parser.add_argument("--search-height", type=float, default=12000.0)
    parser.add_argument("--heading-gap", type=float, default=5000.0)
    parser.add_argument("--heading-x-tolerance", type=float, default=2000.0)
    parser.add_argument("--line-tolerance", type=float, default=5.0)
    parser.add_argument("--text-tolerance", type=float, default=30.0)
    parser.add_argument("--min-width", type=float, default=500.0)
    parser.add_argument("--min-height", type=float, default=300.0)
    parser.add_argument("--min-cells", type=int, default=6)
    parser.add_argument("--dedup-tolerance", type=float, default=20.0)
    args = parser.parse_args()
    texts, lines = read(args.entities)
    regex = re.compile(args.header_pattern, re.IGNORECASE)
    report = ["# CAD 线框约束表格候选", "", f"- 输入：`{args.entities.name}`。", f"- 文字实体：{len(texts)}；LINE 实体：{len(lines)}。", "- 仅将闭合直线框内的文字纳入候选；框外技术说明、节点文字不会自动合并。", "- 候选仍需人工核对：合并单元格、旋转文字、非 LINE 边框和参数语义均未自动判定。", ""]
    results = []
    cell_results = []
    for anchor in (text for text in texts if regex.search(text.value)):
        candidates = boxes_for_anchor(anchor, texts, lines, args)
        if not candidates:
            continue
        box, cells, gap = candidates[0]
        ident = f"GRID-{len(results)+1:03d}"
        write_matrix(report, ident, anchor, box, cells, gap)
        for row_index, group in enumerate(row_groups(cells, 100.0), 1):
            for column_order, cell in enumerate(
                sorted(group, key=lambda item: item.x), 1
            ):
                cell_results.append(
                    {
                        "table_id": ident,
                        "row_index": row_index,
                        "column_order": column_order,
                        "text": cell.value,
                        "x": cell.x,
                        "y": cell.y,
                        "layer": cell.layer,
                    }
                )
        results.append({"table_id": ident, "header": anchor.value, "header_x": anchor.x, "header_y": anchor.y, "min_x": box[0], "min_y": box[1], "max_x": box[2], "max_y": box[3], "heading_gap": gap, "cells": len(cells)})
    out = args.output_dir or (ROOT / "输出")
    out.mkdir(parents=True, exist_ok=True)
    md = out / f"{args.prefix}.CAD线框表格候选.md"
    csv_path = out / f"{args.prefix}.CAD线框表格候选.csv"
    cell_csv_path = out / f"{args.prefix}.CAD线框表格单元格.csv"
    md.write_text("\n".join(report) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["table_id", "header", "header_x", "header_y", "min_x", "min_y", "max_x", "max_y", "heading_gap", "cells"])
        writer.writeheader(); writer.writerows(results)
    with cell_csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "table_id",
                "row_index",
                "column_order",
                "text",
                "x",
                "y",
                "layer",
            ],
        )
        writer.writeheader()
        writer.writerows(cell_results)
    print(f"tables={len(results)} wrote {md}")
    print(f"wrote {csv_path}")
    print(f"wrote {cell_csv_path}")


if __name__ == "__main__":
    main()
