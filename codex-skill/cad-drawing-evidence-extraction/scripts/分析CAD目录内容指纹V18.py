#!/usr/bin/env python3
"""分析 V18 中望轻量内容指纹，决定哪些辅助 DWG 值得进入完整流程。"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


DEVICE_TEXT_RE = re.compile(
    r"阻尼器|消能器|消能减震|黏滞|粘滞|屈曲约束|防屈曲|"
    r"墙板阻尼|金属屈服|软钢阻尼|摩擦型",
    re.IGNORECASE,
)
MODEL_CODE_RE = re.compile(
    r"(?<![A-Z0-9])(?:VFD|F?BRB|XNQD|XNQB|QB)(?:[-_×xX]?[A-Z0-9.]+)?",
    re.IGNORECASE,
)
LAYOUT_RE = re.compile(
    r"平面布置|布置图|结构平面|楼层.{0,8}平面|标准层|屋面.{0,8}平面|"
    r"减震.{0,8}布置|消能.{0,8}布置",
    re.IGNORECASE,
)
QUANTITY_DIRECTION_RE = re.compile(
    r"数量|合计|总计|X\s*向|Y\s*向|型号|每层|共\s*\d+\s*(?:套|个|根)",
    re.IGNORECASE,
)
REFERENCE_RE = re.compile(
    r"总说明|设计说明|技术要求|质量要求|检验|检测|性能参数|参数表|维护",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分析CAD目录内容指纹V18")
    parser.add_argument("--route-manifest", type=Path, required=True)
    parser.add_argument("--prescan-manifest", type=Path, required=True)
    parser.add_argument("--fingerprint-dir", type=Path, required=True)
    parser.add_argument("--execution-log", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--full-summary", type=Path)
    return parser.parse_args()


def read_csv(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\\P", "\n").replace("\\~", " ")
    text = re.sub(r"\\[A-Za-z][^;{}]*;", "", text)
    text = text.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


def match_text_records(
    records: Iterable[dict[str, Any]],
    pattern: re.Pattern[str],
) -> tuple[int, list[str]]:
    occurrences = 0
    samples: list[str] = []
    for record in records:
        text = clean_text(record.get("text"))
        if not text or not pattern.search(text):
            continue
        occurrences += max(1, as_int(record.get("count")))
        if len(samples) < 5:
            samples.append(
                f"{text[:180]} @({record.get('x')},{record.get('y')})"
                f" [{record.get('origin') or ''}]"
            )
    return occurrences, samples


def matching_names(
    values: Iterable[str],
    pattern: re.Pattern[str],
) -> list[str]:
    return sorted(
        {
            clean_text(value)
            for value in values
            if clean_text(value) and pattern.search(clean_text(value))
        }
    )


def analyze_fingerprint(document: dict[str, Any]) -> dict[str, Any]:
    text_records = document.get("text_records") or []
    block_records = document.get("block_records") or []
    layers = [str(value) for value in document.get("layers") or []]

    device_text_count, device_samples = match_text_records(
        text_records, DEVICE_TEXT_RE
    )
    model_code_count, model_samples = match_text_records(
        text_records, MODEL_CODE_RE
    )
    layout_text_count, layout_samples = match_text_records(
        text_records, LAYOUT_RE
    )
    quantity_text_count, quantity_samples = match_text_records(
        text_records, QUANTITY_DIRECTION_RE
    )
    reference_text_count, reference_samples = match_text_records(
        text_records, REFERENCE_RE
    )

    device_block_names: list[str] = []
    device_block_reference_count = 0
    for record in block_records:
        joined = " ".join(
            str(record.get(field) or "")
            for field in ("name", "effective_name", "layer")
        )
        if not (DEVICE_TEXT_RE.search(joined) or MODEL_CODE_RE.search(joined)):
            continue
        name = str(record.get("effective_name") or record.get("name") or "")
        if name and name not in device_block_names and len(device_block_names) < 10:
            device_block_names.append(name)
        device_block_reference_count += as_int(
            record.get("layout_reference_count")
        )

    device_layer_names = matching_names(
        layers, re.compile(
            DEVICE_TEXT_RE.pattern + "|" + MODEL_CODE_RE.pattern,
            re.IGNORECASE,
        )
    )
    entity_count = as_int(document.get("entity_count"))
    text_occurrence_count = as_int(document.get("text_occurrence_count"))
    layout_block_reference_count = as_int(
        document.get("layout_block_reference_count")
    )
    definition_block_reference_count = as_int(
        document.get("definition_block_reference_count")
    )
    proxy_count = as_int(document.get("proxy_entity_count"))
    skipped_count = as_int(document.get("skipped_object_error_count"))
    severe_error_threshold = max(10, int(entity_count * 0.02))
    sparse_coverage = (
        text_occurrence_count == 0
        and layout_block_reference_count == 0
        and definition_block_reference_count == 0
    )
    severe_read_problem = skipped_count > severe_error_threshold

    promotion_reason = ""
    if document.get("export_status") != "success":
        decision = "content_unresolved"
        promotion_reason = "fingerprint_export_not_successful"
    elif sparse_coverage or severe_read_problem:
        decision = "content_unresolved"
        promotion_reason = (
            "no_readable_text_or_block_fingerprint"
            if sparse_coverage
            else "skipped_object_error_ratio_too_high"
        )
    elif (
        device_block_reference_count >= 2
        or (
            device_text_count >= 2
            and layout_text_count >= 1
        )
        or (
            device_text_count >= 1
            and layout_text_count >= 1
            and (model_code_count >= 1 or quantity_text_count >= 1)
        )
        or (
            model_code_count >= 2
            and (layout_text_count >= 1 or quantity_text_count >= 1)
        )
    ):
        decision = "promoted_primary"
        promotion_reason = "repeated_device_or_layout_evidence"
    elif (
        (device_text_count or model_code_count)
        and reference_text_count
        and not layout_text_count
        and device_block_reference_count < 2
    ):
        decision = "reference_hit"
        promotion_reason = "device_reference_without_layout_evidence"
    elif (
        device_text_count
        or model_code_count
        or device_block_reference_count
        or device_layer_names
    ):
        decision = "keep_supporting"
        promotion_reason = "isolated_or_weak_device_evidence"
    else:
        decision = "content_negative"
        promotion_reason = "no_device_evidence_in_current_fingerprint_scope"

    evidence_samples = (
        device_samples
        + model_samples
        + layout_samples
        + quantity_samples
        + reference_samples
    )
    return {
        "content_scan_decision": decision,
        "content_scan_reason": promotion_reason,
        "entity_count": entity_count,
        "text_occurrence_count": text_occurrence_count,
        "unique_text_record_count": as_int(
            document.get("unique_text_record_count")
        ),
        "layout_block_reference_count": layout_block_reference_count,
        "definition_block_reference_count": definition_block_reference_count,
        "proxy_entity_count": proxy_count,
        "skipped_object_error_count": skipped_count,
        "device_text_occurrence_count": device_text_count,
        "model_code_occurrence_count": model_code_count,
        "layout_text_occurrence_count": layout_text_count,
        "quantity_direction_occurrence_count": quantity_text_count,
        "reference_text_occurrence_count": reference_text_count,
        "device_block_reference_count": device_block_reference_count,
        "device_block_names": "；".join(device_block_names),
        "device_layer_names": "；".join(device_layer_names[:10]),
        "evidence_samples": " || ".join(evidence_samples[:8]),
        "absence_proven": "false",
    }


def safe_markdown(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def write_outputs(
    rows: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "V18目录内容复筛.csv"
    json_path = output_dir / "V18目录内容复筛.json"
    md_path = output_dir / "V18目录内容复筛.md"
    fieldnames = sorted({key for row in rows for key in row})
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(
        json.dumps({"records": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    counts = Counter(row["content_scan_decision"] for row in rows)
    scanned = [
        row
        for row in rows
        if row["content_scan_decision"]
        not in {"already_selected", "not_scanned"}
    ]
    lines = [
        "# V18 CAD目录内容复筛\n\n",
        "## 状态概览\n\n",
        "；".join(
            f"`{key}`={value}" for key, value in sorted(counts.items())
        ),
        "\n\n",
        "## 扫描结果\n\n",
        "| 文件 | 原路由 | 内容决定 | 设备文字 | 型号代码 | 布置语境 | 设备块实例 | 错误/代理 | 完整流程状态 | 证据 |\n",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |\n",
    ]
    for row in scanned:
        lines.append(
            "| {file} | {route} | {decision} | {device} | {model} | "
            "{layout} | {blocks} | {errors}/{proxy} | {full} | {evidence} |\n".format(
                file=safe_markdown(Path(row["source_path"]).name),
                route=safe_markdown(row.get("route_status")),
                decision=safe_markdown(row.get("content_scan_decision")),
                device=row.get("device_text_occurrence_count") or 0,
                model=row.get("model_code_occurrence_count") or 0,
                layout=row.get("layout_text_occurrence_count") or 0,
                blocks=row.get("device_block_reference_count") or 0,
                errors=row.get("skipped_object_error_count") or 0,
                proxy=row.get("proxy_entity_count") or 0,
                full=safe_markdown(row.get("full_pipeline_status") or "—"),
                evidence=safe_markdown(row.get("evidence_samples") or "—"),
            )
        )
    lines.extend(
        [
            "\n## 解释边界\n\n",
            "- `promoted_primary` 表示轻量指纹有足够证据，文件应进入 V16 完整六导出流程；它本身不是数量结果。\n",
            "- `keep_supporting` 和 `reference_hit` 保留相关证据，但默认不累加数量。\n",
            "- `content_unresolved` 表示指纹缺失、内容过少或读取错误过多，禁止解释为图中没有阻尼器。\n",
            "- `content_negative` 只表示当前文字、块名和图层指纹没有命中设备证据，`absence_proven` 始终为 false。\n",
            "- 纯几何匿名符号、代理对象和专业自定义对象仍可能超出本次轻量扫描范围；主图出现漏项疑点时必须回查辅助图。\n",
        ]
    )
    md_path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    route_rows = read_csv(args.route_manifest)
    prescan_rows = read_csv(args.prescan_manifest)
    execution_rows = read_csv(args.execution_log)
    full_rows = read_csv(args.full_summary)

    prescan_by_source = {
        row.get("source_path") or "": row for row in prescan_rows
    }
    execution_by_path = {
        str(Path(row.get("drawing_path") or "").resolve()).lower(): row
        for row in execution_rows
        if row.get("drawing_path")
    }
    full_by_source = {
        str(Path(row.get("source_path") or "").resolve()).lower(): row
        for row in full_rows
        if row.get("source_path")
    }

    output_rows: list[dict[str, Any]] = []
    for route in route_rows:
        source_path = route.get("source_path") or ""
        row: dict[str, Any] = dict(route)
        full_record = full_by_source.get(
            str(Path(source_path).resolve()).lower(), {}
        )
        full_status = full_record.get("status") or ""
        full_route_class = full_record.get("route_class") or ""
        if full_status == "not_selected" and full_route_class == "exact_duplicate":
            full_status = "exact_duplicate_in_full_pipeline"
        row["full_pipeline_status"] = full_status
        row["full_pipeline_route_class"] = full_route_class
        if route.get("route_status") == "selected":
            row["content_scan_decision"] = "already_selected"
            row["content_scan_reason"] = "V17_primary_or_explicit_selection"
            row["absence_proven"] = "false"
            output_rows.append(row)
            continue

        prescan = prescan_by_source.get(source_path)
        if not prescan:
            row["content_scan_decision"] = "not_scanned"
            row["content_scan_reason"] = "route_not_eligible_for_V18_prescan"
            row["absence_proven"] = "false"
            output_rows.append(row)
            continue

        stem = prescan.get("copied_stem") or ""
        fingerprint_path = (
            args.fingerprint_dir
            / f"{stem}.cad_content_fingerprint_v18.json"
        )
        execution = execution_by_path.get(
            str(Path(prescan.get("copied_path") or "").resolve()).lower(),
            {},
        )
        row.update(
            {
                "prescan_copied_path": prescan.get("copied_path") or "",
                "prescan_copied_stem": stem,
                "prescan_sha256": prescan.get("sha256") or "",
                "fingerprint_path": str(fingerprint_path),
                "prescan_execution_status": execution.get("status") or "",
                "prescan_execution_message": execution.get("message") or "",
            }
        )
        document = read_json(fingerprint_path)
        if not document:
            row.update(
                {
                    "content_scan_decision": "content_unresolved",
                    "content_scan_reason": "fingerprint_missing_or_invalid",
                    "evidence_samples": "",
                    "absence_proven": "false",
                }
            )
        else:
            row.update(analyze_fingerprint(document))
        output_rows.append(row)

    write_outputs(output_rows, args.output_dir)
    print(
        json.dumps(
            {
                "records": len(output_rows),
                "decisions": dict(
                    Counter(
                        row["content_scan_decision"] for row in output_rows
                    )
                ),
                "promoted": sum(
                    row["content_scan_decision"] == "promoted_primary"
                    for row in output_rows
                ),
                "output_dir": str(args.output_dir),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
