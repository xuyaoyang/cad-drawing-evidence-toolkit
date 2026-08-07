#!/usr/bin/env python3
"""Turn table-like CAD TEXT/MTEXT entities into coordinate-backed table matrices.

The input is the tab-separated entity list produced by the CAD extraction
workflow.  It never infers missing cell values: each output cell contains the
original entity text and each table retains source coordinates.  Table regions
are candidates only; a downstream product parser must still confirm headers
and parameter-to-value relationships.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Cell:
    entity_type: str
    layer: str
    x: float
    y: float
    text: str


def clean(text: str) -> str:
    return " ".join(text.replace("\\P", " ").replace("\n", " ").split())


def read_entities(path: Path) -> list[Cell]:
    """Read either Core Console TSV or ZWCAD V5 recursive-text JSON.

    The JSON route is essential when an institute puts parameter-table text in
    an inserted block.  Both sources carry world coordinates; this function
    intentionally normalizes only those common fields and does not try to
    reconstruct entity bounds or alter drawing content.
    """
    rows: list[Cell] = []
    if path.suffix.lower() == ".json":
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        for record in payload.get("records", []):
            if record.get("entity_type") not in {"DBText", "MText", "AttributeReference"}:
                continue
            text = clean(str(record.get("text", "")))
            try:
                x, y = float(record["x"]), float(record["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if text:
                rows.append(Cell(str(record.get("entity_type", "")), str(record.get("layer", "")), x, y, text))
        return rows

    # Core Console entity TSV is GBK/936 on this workstation.
    with path.open(encoding="gbk", errors="replace", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        for row in reader:
            if row.get("TYPE") not in {"TEXT", "MTEXT"}:
                continue
            text = clean(row.get("VALUE", ""))
            if not text or "," not in row.get("POINT", ""):
                continue
            x_text, y_text, *_ = row["POINT"].split(",")
            try:
                rows.append(Cell(row["TYPE"], row.get("LAYER", ""), float(x_text), float(y_text), text))
            except ValueError:
                continue
    return rows


def is_table_like(cell: Cell) -> bool:
    layer = cell.layer.lower()
    return (
        "tab" in layer
        or "表格" in cell.layer
        or "table" in layer
        or any(word in cell.text for word in ("参数表", "性能表", "型号", "数量", "单位", "规格"))
    )


def cluster(values: list[float], tolerance: float) -> list[list[float]]:
    groups: list[list[float]] = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return groups


def assign_group(value: float, groups: list[list[float]]) -> int:
    centers = [sum(group) / len(group) for group in groups]
    return min(range(len(centers)), key=lambda index: abs(value - centers[index]))


def split_by_y_gap(rows: list[Cell], row_tolerance: float) -> list[list[Cell]]:
    """Split one layer into candidate table bands using its row spacing."""
    row_groups = cluster([cell.y for cell in rows], row_tolerance)
    centers = sorted((sum(group) / len(group) for group in row_groups), reverse=True)
    if len(centers) < 3:
        return []
    gaps = [abs(centers[index] - centers[index + 1]) for index in range(len(centers) - 1)]
    typical = statistics.median(gaps) if gaps else 0.0
    # An interruption several times larger than local row spacing indicates a
    # different table or a separate annotation band.  This is computed from
    # the drawing itself; no sheet size or fixed table dimension is assumed.
    split_gap = typical * 3.0 if typical > row_tolerance else row_tolerance * 3.0
    bands: list[list[float]] = [[]]
    for index, center in enumerate(centers):
        if index and abs(centers[index - 1] - center) > split_gap:
            bands.append([])
        bands[-1].append(center)
    result: list[list[Cell]] = []
    for band in bands:
        if len(band) < 3:
            continue
        min_y, max_y = min(band) - row_tolerance, max(band) + row_tolerance
        candidate = [cell for cell in rows if min_y <= cell.y <= max_y]
        if len(candidate) >= 9:
            result.append(candidate)
    return result


def anchor_table_band(anchor: Cell, nearby: list[Cell], row_tolerance: float, min_span: float) -> list[Cell]:
    """Keep the compact multi-column band adjacent to a table heading.

    Recursive JSON contains nearby prose from the same block definition.  A
    fixed rectangle is insufficient: a one-cell maintenance sentence can sit
    very close to a real parameter table.  Starting at the heading, select the
    direction whose following rows are repeatedly multi-cell and horizontally
    spread out.  This is deliberately a *candidate band*, not a semantic
    parser; if no such band exists the caller retains the original neighbourhood.
    """
    groups: list[list[Cell]] = []
    for cell in sorted(nearby, key=lambda value: -value.y):
        if not groups:
            groups.append([cell])
            continue
        center = sum(item.y for item in groups[-1]) / len(groups[-1])
        if abs(cell.y - center) <= row_tolerance:
            groups[-1].append(cell)
        else:
            groups.append([cell])
    if len(groups) < 3:
        return nearby
    anchor_index = min(range(len(groups)), key=lambda index: min(abs(cell.y - anchor.y) for cell in groups[index]))

    def dense(group: list[Cell]) -> bool:
        return len(group) >= 2 and max(cell.x for cell in group) - min(cell.x for cell in group) >= min_span

    candidates: list[list[Cell]] = []
    for direction in (-1, 1):
        band = list(groups[anchor_index])
        started = False
        index = anchor_index + direction
        while 0 <= index < len(groups):
            group = groups[index]
            if dense(group):
                started = True
                band.extend(group)
                index += direction
                continue
            # Once a table row sequence has begun, a sparse prose row is a
            # boundary.  Before it begins, do not jump over prose to search
            # for another unrelated table.
            break
        if started:
            candidates.append(band)
    if not candidates:
        return nearby
    return max(candidates, key=len)


def write_table(markdown: list[str], identifier: str, rows: list[Cell], row_tolerance: float, column_tolerance: float) -> None:
    row_groups = cluster([cell.y for cell in rows], row_tolerance)
    col_groups = cluster([cell.x for cell in rows], column_tolerance)
    if len(row_groups) < 3 or len(col_groups) < 3:
        return
    matrix: dict[tuple[int, int], list[str]] = defaultdict(list)
    for cell in rows:
        row_index = assign_group(cell.y, row_groups)
        col_index = assign_group(cell.x, col_groups)
        matrix[(row_index, col_index)].append(cell.text)
    row_order = sorted(range(len(row_groups)), key=lambda index: sum(row_groups[index]) / len(row_groups[index]), reverse=True)
    col_order = sorted(range(len(col_groups)), key=lambda index: sum(col_groups[index]) / len(col_groups[index]))
    min_x, max_x = min(cell.x for cell in rows), max(cell.x for cell in rows)
    min_y, max_y = min(cell.y for cell in rows), max(cell.y for cell in rows)
    markdown += [
        f"## {identifier}", "",
        f"- 图层：`{rows[0].layer}`；实体：{len(rows)}；行×列：{len(row_order)}×{len(col_order)}。",
        f"- 坐标范围：({min_x:.3f}, {min_y:.3f})—({max_x:.3f}, {max_y:.3f})。",
        "",
        "| 行\\列 | " + " | ".join(str(index + 1) for index in range(len(col_order))) + " |",
        "|---|" + "|".join("---" for _ in col_order) + "|",
    ]
    for output_row, row_index in enumerate(row_order, 1):
        values = []
        for col_index in col_order:
            value = " / ".join(matrix.get((row_index, col_index), [])) or ""
            values.append(value.replace("|", "\\|"))
        markdown.append("| " + str(output_row) + " | " + " | ".join(values) + " |")
    markdown.append("")


def main() -> None:
    parser = argparse.ArgumentParser(description="识别 CAD 实体文字中的表格候选并输出矩阵")
    parser.add_argument("entities", type=Path, help="Core Console 的 input.tsv 或中望 V5 递归文字 JSON")
    parser.add_argument("--prefix", required=True, help="输出文件前缀")
    parser.add_argument("--row-tolerance", type=float, default=100.0, help="同一行文字的 Y 坐标容差")
    parser.add_argument("--column-tolerance", type=float, default=100.0, help="同一列文字的 X 坐标容差")
    parser.add_argument(
        "--header-pattern",
        default=r"性能及技术参数|参数表|性能表|连接参数表|支墩表|阻尼器.*参数|消能器.*参数|力学参数",
        help="表头锚点正则；锚点邻域可识别不在 TAB 图层的表格",
    )
    parser.add_argument("--anchor-half-width", type=float, default=18000.0, help="表头锚点 X 邻域半宽")
    parser.add_argument("--anchor-half-height", type=float, default=9000.0, help="表头锚点 Y 邻域半高")
    args = parser.parse_args()

    all_cells = read_entities(args.entities)
    candidates = [cell for cell in all_cells if is_table_like(cell)]
    by_layer: dict[str, list[Cell]] = defaultdict(list)
    for cell in candidates:
        by_layer[cell.layer].append(cell)
    report: list[str] = [
        "# CAD 文字表格候选", "",
        f"- 输入：`{args.entities.name}`（{'中望 V5 递归文字 JSON' if args.entities.suffix.lower() == '.json' else 'Core Console 实体 TSV'}）。",
        f"- 文字实体：{len(all_cells)}；表格候选文字：{len(candidates)}；行/列坐标容差：{args.row_tolerance:g}。",
        "- 本输出保留原始文字及坐标矩阵，未自动把相邻数字认定为某参数的值。", "",
    ]
    summary_rows = []
    table_index = 0
    for layer, cells in sorted(by_layer.items()):
        for band in split_by_y_gap(cells, args.row_tolerance):
            row_groups = cluster([cell.y for cell in band], args.row_tolerance)
            col_groups = cluster([cell.x for cell in band], args.column_tolerance)
            if len(row_groups) < 3 or len(col_groups) < 3:
                continue
            table_index += 1
            identifier = f"TABLE-{table_index:03d}"
            write_table(report, identifier, band, args.row_tolerance, args.column_tolerance)
            summary_rows.append({
                "table_id": identifier, "layer": layer, "entities": len(band),
                "rows": len(row_groups), "columns": len(col_groups),
                "min_x": min(cell.x for cell in band), "min_y": min(cell.y for cell in band),
                "max_x": max(cell.x for cell in band), "max_y": max(cell.y for cell in band),
            })
    # Some design institutes place table text on a generic annotation layer
    # (for example NUM).  A nearby explicit table heading is stronger evidence
    # than a layer name, so create an additional, separately labelled matrix.
    header_regex = re.compile(args.header_pattern, re.IGNORECASE)
    anchor_bounds: list[tuple[float, float, float, float]] = []
    for anchor in all_cells:
        if not header_regex.search(anchor.text):
            continue
        min_x, max_x = anchor.x - args.anchor_half_width, anchor.x + args.anchor_half_width
        min_y, max_y = anchor.y - args.anchor_half_height, anchor.y + args.anchor_half_height
        # Avoid duplicate headings that point at the same table region.
        if any(
            min_x <= (old_min_x + old_max_x) / 2 <= max_x
            and min_y <= (old_min_y + old_max_y) / 2 <= max_y
            for old_min_x, old_min_y, old_max_x, old_max_y in anchor_bounds
        ):
            continue
        nearby = [cell for cell in all_cells if min_x <= cell.x <= max_x and min_y <= cell.y <= max_y]
        # Only recursive JSON needs prose-band suppression.  Plain TSV has no
        # block provenance and may place unrelated dense tables on either side
        # of a heading; its precise path is the separate LINE/LWPOLYLINE table
        # boundary recognizer, so keep this broad candidate intact.
        band = (
            anchor_table_band(anchor, nearby, args.row_tolerance, args.column_tolerance * 4.0)
            if args.entities.suffix.lower() == ".json"
            else nearby
        )
        row_groups = cluster([cell.y for cell in band], args.row_tolerance)
        col_groups = cluster([cell.x for cell in band], args.column_tolerance)
        if len(band) < 9 or len(row_groups) < 3 or len(col_groups) < 3:
            continue
        table_index += 1
        identifier = f"ANCHOR-{table_index:03d}"
        write_table(report, identifier + "：" + anchor.text, band, args.row_tolerance, args.column_tolerance)
        summary_rows.append({
            "table_id": identifier, "layer": "header-anchor:" + anchor.layer, "entities": len(band),
            "rows": len(row_groups), "columns": len(col_groups),
            "min_x": min(cell.x for cell in band), "min_y": min(cell.y for cell in band),
            "max_x": max(cell.x for cell in band), "max_y": max(cell.y for cell in band),
        })
        anchor_bounds.append((min_x, min_y, max_x, max_y))
    out_dir = ROOT / "输出"
    out_dir.mkdir(exist_ok=True)
    markdown_path = out_dir / f"{args.prefix}.CAD文字表格候选.md"
    csv_path = out_dir / f"{args.prefix}.CAD文字表格候选.csv"
    markdown_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["table_id", "layer", "entities", "rows", "columns", "min_x", "min_y", "max_x", "max_y"])
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"tables={len(summary_rows)} wrote {markdown_path}")
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
