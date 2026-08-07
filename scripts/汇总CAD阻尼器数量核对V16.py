#!/usr/bin/env python3
"""汇总 V16 每张 DWG 的导出、识别、可见性和安全停止状态。"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总CAD阻尼器数量核对V16")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--analysis-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def reconcile_quantity(analysis_dir: Path, stem: str) -> tuple[int | None, str]:
    families = [
        (
            "multi_building_standard_floor",
            analysis_dir / f"{stem}.多栋共用标准层调和.csv",
            "expanded_quantity_candidate",
        ),
        (
            "direction_model_table",
            analysis_dir / f"{stem}.型号数量调和.csv",
            "reconciled_quantity_candidate",
        ),
        (
            "shared_floor_layout",
            analysis_dir / f"{stem}.楼层数量调和.csv",
            "reconciled_quantity_candidate",
        ),
    ]
    for family, path, quantity_field in families:
        values: dict[tuple[str, str], int] = {}
        for row in read_csv(path):
            quantity = as_int(row.get(quantity_field))
            status = row.get("status") or ""
            frame = row.get("layout_frame_id") or row.get("frame_id") or ""
            if quantity > 0 and "consistent" in status:
                values[(frame, status)] = quantity
        if values:
            return sum(values.values()), family
    return None, ""


def v13_blocking_errors(document: dict[str, Any]) -> int:
    fields = (
        "unknown_visibility_instance_count",
        "skipped_object_error_count",
        "dynamic_property_read_error_count",
        "layer_read_error_count",
        "entity_visibility_read_error_count",
        "viewport_read_error_count",
        "viewport_frozen_layer_read_error_count",
    )
    return sum(as_int(document.get(field)) for field in fields)


def detect_v14_status(report_path: Path) -> str:
    if not report_path.exists():
        return "not_run"
    text = report_path.read_text(encoding="utf-8-sig")
    if "`layout_viewport_visibility_consistent`" in text:
        return "layout_viewport_visibility_consistent"
    if "`layout_viewport_evidence_unresolved`" in text:
        return "layout_viewport_evidence_unresolved"
    return "unknown"


def overall_status(
    missing_exports: int,
    blocking_errors: int,
    semantic_leaf_count: int,
    v14_status: str,
    needs_frame: int,
    manual_review: int,
    counted_quantity: int,
    reconciled_quantity: int | None,
    damper_candidates: int,
) -> str:
    if missing_exports:
        return "export_incomplete"
    if blocking_errors:
        return "visibility_export_unresolved"
    if (
        semantic_leaf_count
        and v14_status != "layout_viewport_visibility_consistent"
    ):
        return "layout_viewport_unresolved"
    if needs_frame:
        return "frame_evidence_required"
    if manual_review:
        return "manual_review_required"
    if reconciled_quantity is not None:
        return "design_quantity_candidate_reconciled"
    if counted_quantity:
        return "layout_instance_candidate_ready"
    if not damper_candidates:
        return "no_damper_candidate"
    return "no_countable_layout_candidate"


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    output = [
        "| " + " | ".join(rows[0]) + " |\n",
        "| " + " | ".join("---" for _ in rows[0]) + " |\n",
    ]
    output.extend("| " + " | ".join(row) + " |\n" for row in rows[1:])
    return "".join(output)


def main() -> int:
    args = parse_args()
    manifest = read_csv(args.manifest)
    rows: list[dict[str, Any]] = []

    for item in manifest:
        if item.get("route_status") != "selected":
            rows.append(
                {
                    "source_path": item.get("source_path") or "",
                    "copied_stem": item.get("copied_stem") or "",
                    "route_status": item.get("route_status") or "",
                    "route_class": item.get("route_class") or "",
                    "profession": item.get("profession") or "",
                    "drawing_role": item.get("drawing_role") or "",
                    "embedded_revision_date": item.get(
                        "embedded_revision_date"
                    )
                    or "",
                    "route_reason": item.get("route_reason") or "",
                    "status": "not_selected",
                }
            )
            continue

        stem = item["copied_stem"]
        analysis_dir = args.analysis_root / stem
        export_paths = {
            "text": args.export_dir / f"{stem}.cad_text_export_v5.json",
            "frame": args.export_dir / f"{stem}.cad_frame_export_v5.json",
            "symbol": args.export_dir / f"{stem}.cad_symbol_export_v6.json",
            "oriented": args.export_dir
            / f"{stem}.cad_oriented_text_export_v7.json",
            "primitive": args.export_dir
            / f"{stem}.cad_primitive_export_v10.json",
            "visibility": args.export_dir
            / f"{stem}.cad_visibility_export_v13.json",
        }
        missing = [name for name, path in export_paths.items() if not path.exists()]
        candidate_path = analysis_dir / f"{stem}.阻尼器实例候选.csv"
        candidates = read_csv(candidate_path)
        decisions = Counter(row.get("decision") or "" for row in candidates)
        semantic_leaf_count = sum(
            (row.get("semantic_leaf_symbol") or "").lower() == "true"
            for row in candidates
        )
        counted_quantity = sum(
            as_int(row.get("count_value") or 1)
            for row in candidates
            if row.get("decision") == "counted"
        )
        needs_frame = decisions.get("candidate_needs_frame", 0)
        manual_review = sum(
            count
            for decision, count in decisions.items()
            if "manual" in decision or "unresolved" in decision
        )
        frame_count = max(
            0,
            len(read_csv(analysis_dir / f"{stem}.图框候选清单.csv")),
        )
        visibility = read_json(export_paths["visibility"])
        blocking_errors = v13_blocking_errors(visibility)
        v14_status = detect_v14_status(
            analysis_dir / f"{stem}.V14布局视口可见性.md"
        )
        layout_rows = read_csv(
            analysis_dir / f"{stem}.V14布局视口可见性.candidate_viewport.csv"
        )
        visible_candidates = sum(
            row.get("visibility_state") == "visible_in_layout_viewport"
            for row in layout_rows
        )
        duplicate_viewport_candidates = sum(
            as_int(row.get("visible_viewport_count")) > 1
            for row in layout_rows
        )
        reconciled_quantity, reconciliation_family = reconcile_quantity(
            analysis_dir, stem
        )
        status = overall_status(
            len(missing),
            blocking_errors,
            semantic_leaf_count,
            v14_status,
            needs_frame,
            manual_review,
            counted_quantity,
            reconciled_quantity,
            len(candidates),
        )
        rows.append(
            {
                "source_path": item.get("source_path") or "",
                "copied_path": item.get("copied_path") or "",
                "copied_stem": stem,
                "sha256": item.get("sha256") or "",
                "route_status": "selected",
                "route_class": item.get("route_class") or "",
                "profession": item.get("profession") or "",
                "drawing_role": item.get("drawing_role") or "",
                "embedded_revision_date": item.get(
                    "embedded_revision_date"
                )
                or "",
                "route_reason": item.get("route_reason") or "",
                "exports_missing": ",".join(missing),
                "frame_count": frame_count,
                "damper_candidate_count": len(candidates),
                "semantic_leaf_count": semantic_leaf_count,
                "counted_layout_quantity": counted_quantity,
                "reconciled_quantity_candidate": (
                    reconciled_quantity if reconciled_quantity is not None else ""
                ),
                "reconciliation_family": reconciliation_family,
                "candidate_needs_frame_count": needs_frame,
                "manual_review_count": manual_review,
                "v13_blocking_error_count": blocking_errors,
                "v14_status": v14_status,
                "visible_layout_candidate_count": visible_candidates,
                "duplicate_viewport_candidate_count": (
                    duplicate_viewport_candidates
                ),
                "status": status,
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "V16运行汇总.csv"
    json_path = args.output_dir / "V16运行汇总.json"
    report_path = args.output_dir / "V16运行汇总.md"
    fieldnames = sorted({key for row in rows for key in row})
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(
        json.dumps({"records": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    status_counts = Counter(row["status"] for row in rows)
    table = [
        [
            "图纸",
            "路由",
            "分类",
            "角色",
            "图框",
            "候选",
            "图面计入",
            "调和候选",
            "V13错误",
            "状态",
        ]
    ]
    for row in rows:
        table.append(
            [
                Path(row["source_path"]).name,
                row.get("route_status") or "",
                row.get("route_class") or "",
                row.get("drawing_role") or "",
                str(row.get("frame_count") or 0),
                str(row.get("damper_candidate_count") or 0),
                str(row.get("counted_layout_quantity") or 0),
                str(row.get("reconciled_quantity_candidate") or "—"),
                str(row.get("v13_blocking_error_count") or 0),
                row["status"],
            ]
        )
    report = [
        "# V16 CAD阻尼器数量核对运行汇总\n\n",
        "## 状态概览\n\n",
        "；".join(f"`{key}`={value}" for key, value in sorted(status_counts.items())),
        "\n\n",
        markdown_table(table),
        "\n## 结论边界\n\n",
        "- `design_quantity_candidate_reconciled` 只表示图面、楼层/型号表与可见性证据内部一致，不是合同供货或生产放行数量。\n",
        "- `frame_evidence_required`、`layout_viewport_unresolved`、`visibility_export_unresolved` 和 `manual_review_required` 均为安全停止，不得用表格数量补齐。\n",
        "- `not_selected` 表示目录专业路由没有把该文件送入结构数量主流程，原文件未被修改。\n",
        "- `supporting`、`reference`、`older_revision`、`exact_duplicate` 和 `uncertain` 默认不进入设备主计数，但均保留在目录筛选清单中。\n",
    ]
    report_path.write_text("".join(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "records": len(rows),
                "status_counts": dict(status_counts),
                "csv": str(csv_path),
                "json": str(json_path),
                "report": str(report_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
