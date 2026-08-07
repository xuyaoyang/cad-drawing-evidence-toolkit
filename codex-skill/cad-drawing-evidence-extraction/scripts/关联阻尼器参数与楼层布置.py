#!/usr/bin/env python3
"""把总说明参数表与另一 DWG 的楼层阻尼器布置证据关联。

本脚本只读输入文件，不修改 DWG。关联至少同时要求：
1. 布置图图签中的楼栋名称与总说明参数表楼栋行一致；
2. 布置图各楼层参数块中的型号与该楼栋参数行型号一致。

文件名相似、出现“住院楼”等局部词或型号恰好相同，均不足以单独确认关联。
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BUILDING_NAME = re.compile(r"^\s*\d+\s*[#＃].*(?:楼|门诊|医院|中心)\s*$")
MODEL = re.compile(
    r"(?<![A-Z0-9])(?:L?VFD|F?BRB|MYD|XNQD|VAD)"
    r"[-_－—]?[A-Z0-9]+(?:[-_－—+×xX*][A-Z0-9.]+)*",
    re.IGNORECASE,
)


def clean(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("\\P", "")


def normalize_model(value: str) -> str:
    text = clean(value).upper()
    text = text.replace("－", "-").replace("—", "-").replace("_", "-")
    text = text.replace("×", "-").replace("X", "-").replace("*", "-")
    return re.sub(r"-+", "-", text).strip("-")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError(f"JSON 缺少 records 数组：{path}")
    return value


def read_layout_identity(path: Path) -> tuple[str, list[dict[str, Any]]]:
    source = read_json(path)
    matches: list[dict[str, Any]] = []
    for record in source["records"]:
        text = clean(record.get("text"))
        if not BUILDING_NAME.fullmatch(text):
            continue
        matches.append(
            {
                "text": text,
                "x": record.get("x"),
                "y": record.get("y"),
                "handle": (
                    record.get("handle")
                    or record.get("entity_handle")
                    or record.get("record_key")
                    or ""
                ),
                "block_path": record.get("block_path") or "",
            }
        )
    counts = Counter(item["text"] for item in matches)
    identity = counts.most_common(1)[0][0] if counts else ""
    return identity, matches


def read_layout_summary(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_parameter_rows(path: Path) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            grouped[(row["table_id"], row["row_index"])].append(row)

    results: list[dict[str, Any]] = []
    for (table_id, row_index), cells in grouped.items():
        ordered = sorted(cells, key=lambda row: int(row["column_order"]))
        model_cell: tuple[int, str] | None = None
        for index, cell in enumerate(ordered):
            match = MODEL.search(clean(cell["text"]))
            if match:
                model_cell = (index, match.group(0))
                break
        if model_cell is None:
            continue
        model_index, model = model_cell
        building = ""
        building_cell: dict[str, str] | None = None
        for cell in ordered[:model_index]:
            candidate = clean(cell["text"])
            if BUILDING_NAME.fullmatch(candidate):
                building = candidate
                building_cell = cell
                break
        if not building:
            continue
        values = [clean(cell["text"]) for cell in ordered[model_index + 1 :]]
        values += [""] * max(0, 4 - len(values))
        displacement = values[3].replace("%%P", "±").replace("%%p", "±")
        results.append(
            {
                "table_id": table_id,
                "row_index": row_index,
                "building": building,
                "model": model,
                "canonical_model": normalize_model(model),
                "damping_coefficient_c": values[0],
                "damping_exponent_alpha": values[1],
                "maximum_damping_force_kn": values[2],
                "limit_displacement_mm": displacement,
                "building_x": building_cell["x"] if building_cell else "",
                "building_y": building_cell["y"] if building_cell else "",
                "raw_row": " / ".join(clean(cell["text"]) for cell in ordered),
            }
        )
    return results


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return "无\n"
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="跨 DWG 关联阻尼器参数表与楼层布置"
    )
    parser.add_argument("--layout-summary", type=Path, required=True)
    parser.add_argument("--layout-text-json", type=Path, required=True)
    parser.add_argument("--parameter-cells", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()

    identity, identity_evidence = read_layout_identity(args.layout_text_json)
    layouts = read_layout_summary(args.layout_summary)
    parameter_rows = read_parameter_rows(args.parameter_cells)
    layout_models = sorted(
        {
            normalize_model(row.get("model", ""))
            for row in layouts
            if normalize_model(row.get("model", ""))
        }
    )
    matched_building_rows = [
        row for row in parameter_rows if clean(row["building"]) == clean(identity)
    ]
    matched_rows = [
        row
        for row in matched_building_rows
        if row["canonical_model"] in layout_models
    ]

    if not identity:
        status = "layout_identity_missing"
    elif not matched_building_rows:
        status = "building_row_missing"
    elif not matched_rows:
        status = "building_found_model_mismatch"
    elif len(matched_rows) > 1:
        status = "multiple_parameter_rows_ambiguous"
    else:
        status = "building_and_model_consistent"

    total = sum(int(row.get("placement_count") or 0) for row in layouts)
    x_total = sum(int(row.get("x_quantity") or 0) for row in layouts)
    y_total = sum(int(row.get("y_quantity") or 0) for row in layouts)
    ambiguous_total = sum(
        int(row.get("ambiguous_direction_count") or 0) for row in layouts
    )
    parameter = matched_rows[0] if len(matched_rows) == 1 else None

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / f"{args.prefix}.跨DWG楼层参数关联.csv"
    report_path = args.output_dir / f"{args.prefix}.跨DWG楼层参数关联.md"
    fields = [
        "floor",
        "status",
        "building",
        "model",
        "placement_count",
        "x_quantity",
        "y_quantity",
        "ambiguous_direction_count",
        "definition_handle",
        "canonical_root_handle",
        "parameter_table_id",
        "parameter_row_index",
        "damping_coefficient_c",
        "damping_exponent_alpha",
        "maximum_damping_force_kn",
        "limit_displacement_mm",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in layouts:
            writer.writerow(
                {
                    "floor": row.get("floor", ""),
                    "status": status,
                    "building": identity,
                    "model": row.get("model", ""),
                    "placement_count": row.get("placement_count", ""),
                    "x_quantity": row.get("x_quantity", ""),
                    "y_quantity": row.get("y_quantity", ""),
                    "ambiguous_direction_count": row.get(
                        "ambiguous_direction_count", ""
                    ),
                    "definition_handle": row.get("definition_handle", ""),
                    "canonical_root_handle": row.get(
                        "canonical_root_handle", ""
                    ),
                    "parameter_table_id": (
                        parameter["table_id"] if parameter else ""
                    ),
                    "parameter_row_index": (
                        parameter["row_index"] if parameter else ""
                    ),
                    "damping_coefficient_c": (
                        parameter["damping_coefficient_c"] if parameter else ""
                    ),
                    "damping_exponent_alpha": (
                        parameter["damping_exponent_alpha"] if parameter else ""
                    ),
                    "maximum_damping_force_kn": (
                        parameter["maximum_damping_force_kn"] if parameter else ""
                    ),
                    "limit_displacement_mm": (
                        parameter["limit_displacement_mm"] if parameter else ""
                    ),
                }
            )

    report = [
        f"# {args.prefix}：跨 DWG 阻尼器参数与楼层布置关联\n",
        "## 结论\n",
        f"- 关联状态：`{status}`。\n",
        f"- 布置图楼栋：`{identity or '未提取'}`；图签命中 {len(identity_evidence)} 次。\n",
        f"- 楼层布置型号：`{', '.join(layout_models) or '未提取'}`。\n",
        f"- 去重后布置数量候选：{total}；X向 {x_total}，Y向 {y_total}，方向不明 {ambiguous_total}。\n",
    ]
    if parameter:
        report.extend(
            [
                f"- 总说明参数行：`{parameter['building']}` / "
                f"`{parameter['model']}` / C={parameter['damping_coefficient_c']} / "
                f"α={parameter['damping_exponent_alpha']} / "
                f"F={parameter['maximum_damping_force_kn']} kN / "
                f"极限位移={parameter['limit_displacement_mm']} mm。\n",
            ]
        )
    else:
        report.append("- 未形成唯一的楼栋—型号参数行，不把参数自动写入楼层数量。\n")

    report.extend(
        [
            "\n## 楼层结果\n",
            markdown_table(
                [
                    ["楼层", "型号", "数量", "X向", "Y向", "块定义/计数根"]
                ]
                + [
                    [
                        row.get("floor", ""),
                        row.get("model", "") or "待确认",
                        row.get("placement_count", ""),
                        row.get("x_quantity", ""),
                        row.get("y_quantity", ""),
                        f"`{row.get('definition_handle', '') or '—'}` / "
                        f"`{row.get('canonical_root_handle', '') or '—'}`",
                    ]
                    for row in layouts
                ]
            ),
            "\n## 总说明参数表全部楼栋行\n",
            markdown_table(
                [["楼栋", "型号", "C", "α", "F(kN)", "极限位移(mm)", "表格行"]]
                + [
                    [
                        row["building"],
                        row["model"],
                        row["damping_coefficient_c"],
                        row["damping_exponent_alpha"],
                        row["maximum_damping_force_kn"],
                        row["limit_displacement_mm"],
                        f"{row['table_id']}/{row['row_index']}",
                    ]
                    for row in parameter_rows
                ]
            ),
            "\n## 关联边界\n",
            "- 楼栋名称取自布置图图签，不从文件名猜测；型号同时由各楼层参数块和总说明参数行核对。\n",
            "- 总说明中其他楼栋的型号没有在本布置图出现，不等于数量为零，只表示本次输入未包含其对应平面图。\n",
            "- 165 是设计布置数量候选，不自动等于供货、备品或生产放行数量；仍需合同清单和变更记录调和。\n",
            "- “阻尼器支撑墙”与设备数量按一一对应候选处理；若大样显示一墙多机，必须回退人工复核。\n",
            "\n## 输入证据\n",
            f"- 楼层去重：`{args.layout_summary}`\n",
            f"- 布置图 V5 文字：`{args.layout_text_json}`\n",
            f"- 总说明线框单元格：`{args.parameter_cells}`\n",
            "- 原始 DWG 均只读，未修改。\n",
        ]
    )
    report_path.write_text("".join(report), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": status,
                "building": identity,
                "layout_models": layout_models,
                "floor_groups": len(layouts),
                "quantity_candidate": total,
                "direction_counts": {
                    "X": x_total,
                    "Y": y_total,
                    "ambiguous": ambiguous_total,
                },
                "matched_parameter_row": parameter,
                "csv": str(csv_path),
                "report": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
