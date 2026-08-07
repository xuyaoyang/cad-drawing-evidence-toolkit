#!/usr/bin/env python3
"""对比合成 DWG 的真值、V6 候选和 V14 布局视口分析结果。"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="验证布局视口合成测试图")
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("candidate_csv", type=Path)
    parser.add_argument("candidate_viewport_csv", type=Path)
    parser.add_argument("viewport_summary_csv", type=Path)
    parser.add_argument("v14_report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def as_float(value: Any) -> float:
    return float(value or 0.0)


def coordinate_key(x: Any, y: Any, layer: Any) -> tuple[float, float, str]:
    return (
        round(as_float(x), 6),
        round(as_float(y), 6),
        str(layer or "").casefold(),
    )


def main() -> int:
    args = parse_args()
    truth = json.loads(args.ground_truth.read_text(encoding="utf-8-sig"))
    expected = truth["expected"]
    candidates = [
        row
        for row in read_csv(args.candidate_csv)
        if (row.get("semantic_leaf_symbol") or "").lower() == "true"
    ]
    viewport_rows = read_csv(args.candidate_viewport_csv)
    viewport_summary = read_csv(args.viewport_summary_csv)
    report_text = args.v14_report.read_text(encoding="utf-8-sig")

    truth_keys = {
        coordinate_key(row["x"], row["y"], row["layer"])
        for row in truth["devices"]
    }
    candidate_keys = {
        coordinate_key(row["x"], row["y"], row["layer"])
        for row in candidates
    }
    viewport_keys = {
        coordinate_key(row["x"], row["y"], row["effective_layer"])
        for row in viewport_rows
    }

    actual = {
        "semantic_leaf_count": len(candidates),
        "database_visible_count": sum(
            (row.get("database_visible") or "").lower() == "true"
            for row in viewport_rows
        ),
        "visible_candidate_count": sum(
            row.get("visibility_state") == "visible_in_layout_viewport"
            for row in viewport_rows
        ),
        "duplicate_display_candidate_count": sum(
            int(row.get("visible_viewport_count") or 0) > 1
            for row in viewport_rows
        ),
        "viewport_layer_frozen_occurrence_count": sum(
            int(row.get("candidate_layer_frozen") or 0)
            for row in viewport_summary
        ),
        "disabled_viewport_count": sum(
            (row.get("on") or "").lower() != "true"
            for row in viewport_summary
        ),
        "overall_status": (
            "layout_viewport_visibility_consistent"
            if "`layout_viewport_visibility_consistent`" in report_text
            else "layout_viewport_evidence_unresolved"
        ),
    }

    checks: list[dict[str, Any]] = []
    for field, expected_value in expected.items():
        if field == "layout_name":
            actual_value = sorted(
                {
                    row.get("layout_name") or ""
                    for row in viewport_summary
                    if row.get("layout_name")
                }
            )
            passed = expected_value in actual_value
        else:
            actual_value = actual.get(field)
            passed = actual_value == expected_value
        checks.append(
            {
                "check": field,
                "expected": expected_value,
                "actual": actual_value,
                "passed": passed,
            }
        )

    checks.extend(
        [
            {
                "check": "candidate_coordinates_and_layers",
                "expected": len(truth_keys),
                "actual": len(candidate_keys & truth_keys),
                "passed": candidate_keys == truth_keys,
            },
            {
                "check": "viewport_coordinates_and_layers",
                "expected": len(truth_keys),
                "actual": len(viewport_keys & truth_keys),
                "passed": viewport_keys == truth_keys,
            },
        ]
    )

    passed = all(row["passed"] for row in checks)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 合成布局视口图纸自动验证\n\n",
        f"- 合成标记：`{truth.get('synthetic')}`\n",
        "- 工程证据用途：禁止；仅用于算法回归\n",
        f"- 总体验证：`{'passed' if passed else 'failed'}`\n\n",
        "| 检查项 | 预期 | 实际 | 结果 |\n",
        "| --- | ---: | ---: | --- |\n",
    ]
    for row in checks:
        lines.append(
            f"| {row['check']} | `{row['expected']}` | "
            f"`{row['actual']}` | {'通过' if row['passed'] else '失败'} |\n"
        )
    args.output.write_text("".join(lines), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": "passed" if passed else "failed",
                "checks": checks,
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
