#!/usr/bin/env python3
"""从 V23 证据包生成风险分层抽样清单和中望 CAD 只读回查定位脚本。

V24 不修改 V23 的定位、数量或证据状态。它只负责：
1. 按未决、OUT、复杂轴网、跨视图偏差、边界接近程度等因素排序；
2. 全选强制复核项，并对普通项做确定性分层抽样；
3. 生成轻量 AI 清单、SVG 抽查索引和可在中望工作副本中定位的 AutoLISP。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从V23证据包生成V24风险分层抽样与中望回查包"
    )
    parser.add_argument("v23_json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--medium-sample-rate", type=float, default=0.10)
    parser.add_argument("--low-sample-rate", type=float, default=0.05)
    parser.add_argument("--medium-sample-cap", type=int, default=20)
    parser.add_argument("--low-sample-cap", type=int, default=12)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"JSON顶层不是对象：{path}")
    return value


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(
    path: Path, rows: list[dict[str, Any]], fields: list[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def optional_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def flatten_boundaries(evidence: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(evidence, dict):
        return
    for key in (
        "low_boundary",
        "high_boundary",
        "outer_boundary",
        "spacing_reference_boundary",
    ):
        value = evidence.get(key)
        if isinstance(value, dict):
            yield value


def axis_spacing(evidence: Any) -> float | None:
    if not isinstance(evidence, dict):
        return None
    low = optional_float(evidence.get("distance_to_low"))
    high = optional_float(evidence.get("distance_to_high"))
    if low is None or high is None:
        return None
    spacing = low + high
    return spacing if spacing > 0 else None


def max_match_ratio(template: dict[str, Any]) -> float:
    ratios: list[float] = []
    for key in ("x_evidence", "y_evidence"):
        evidence = template.get(key)
        spacing = axis_spacing(evidence)
        if spacing is None:
            continue
        for node in flatten_boundaries(evidence):
            distances = node.get("geometry_match_distances") or []
            if not isinstance(distances, list):
                distances = [distances]
            for value in distances:
                distance = optional_float(value)
                if distance is not None:
                    ratios.append(abs(distance) / spacing)
            label_distance = optional_float(node.get("label_match_distance"))
            if label_distance is not None and str(
                (evidence or {}).get("kind") or ""
            ).startswith("curved_axis"):
                ratios.append(abs(label_distance) / spacing)
    return max(ratios, default=0.0)


def fraction_edge_distance(template: dict[str, Any]) -> float:
    values: list[float] = []
    key = str(template.get("axis_position_key") or "")
    if "OUT" in key.upper():
        return 0.0
    for part in key.split("|")[1:]:
        if "@" not in part:
            continue
        value = optional_float(part.rsplit("@", 1)[1])
        if value is not None:
            values.append(min(abs(value), abs(1.0 - value)))
    return min(values, default=1.0)


def review_drawing_from_source(template: dict[str, Any]) -> str:
    explicit = str(template.get("review_drawing_path") or "")
    if explicit:
        return explicit
    for field in ("primitive_geometry_json",):
        value = str(template.get(field) or "")
        if not value:
            continue
        path = Path(value)
        if not path.is_file():
            continue
        try:
            drawing = str(load_json(path).get("drawing") or "")
        except (OSError, ValueError, json.JSONDecodeError):
            drawing = ""
        if drawing:
            return drawing
    manifest_value = str(template.get("manifest_path") or "")
    if not manifest_value:
        return ""
    manifest_path = Path(manifest_value)
    if not manifest_path.is_file():
        return ""
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    source_id = str(template.get("primary_source_id") or "")
    sources = manifest.get("sources") or []
    source = next(
        (
            row
            for row in sources
            if not source_id
            or str(row.get("source_id") or "") == source_id
        ),
        {},
    )
    for field in ("visibility_json", "primitive_geometry_json"):
        value = str(source.get(field) or "")
        if not value:
            continue
        path = Path(value)
        if not path.is_file():
            continue
        try:
            drawing = str(load_json(path).get("drawing") or "")
        except (OSError, ValueError, json.JSONDecodeError):
            drawing = ""
        if drawing:
            return drawing
    return ""


def safe_review_copy(path_value: str) -> tuple[str, str]:
    if not path_value:
        return "", "未找到中望导出所用工作副本"
    path = Path(path_value)
    normalized = str(path.resolve()) if path.exists() else str(path)
    lowered = normalized.lower()
    if path.suffix.lower() != ".dwg":
        return "", "回查来源不是DWG工作副本"
    if "onedrive" in lowered:
        return "", "来源位于OneDrive，禁止自动打开"
    if not path.is_file():
        return "", "回查工作副本不存在"
    return normalized, ""


def occurrence_candidates(manifest_path: Path) -> list[Path]:
    if not manifest_path.is_file():
        return []
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    prefix = str(manifest.get("output_prefix") or "")
    candidates: list[Path] = []
    if prefix:
        candidates.append(manifest_path.parent / f"{prefix}.device_occurrence.csv")
    candidates.extend(
        sorted(manifest_path.parent.glob("*.device_occurrence.csv"))
    )
    return list(dict.fromkeys(path for path in candidates if path.is_file()))


def load_cross_view_metrics(
    templates: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    metrics: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "occurrence_count": 0,
            "max_axis_fraction_delta": 0.0,
            "mapping_issue_count": 0,
        }
    )
    by_manifest: dict[Path, set[str]] = defaultdict(set)
    for template in templates:
        value = str(template.get("manifest_path") or "")
        if value:
            by_manifest[Path(value)].add(
                str(template.get("physical_template_id") or "")
            )
    for manifest_path, template_ids in by_manifest.items():
        candidates = occurrence_candidates(manifest_path)
        if not candidates:
            continue
        rows = read_csv(candidates[0])
        for row in rows:
            template_id = str(row.get("physical_template_id") or "")
            if template_id not in template_ids:
                continue
            key = (str(manifest_path), template_id)
            item = metrics[key]
            item["occurrence_count"] += 1
            deltas = [
                value
                for field in ("axis_fraction_delta_x", "axis_fraction_delta_y")
                if (value := optional_float(row.get(field))) is not None
            ]
            if deltas:
                item["max_axis_fraction_delta"] = max(
                    item["max_axis_fraction_delta"],
                    max(abs(value) for value in deltas),
                )
            mapping = str(row.get("mapping_status") or "")
            if mapping and not (
                mapping.startswith("matched_")
                or mapping.startswith("primary_")
                or mapping in {"mapped", "primary", "primary_instance"}
            ):
                item["mapping_issue_count"] += 1
    return metrics


def kinds(template: dict[str, Any]) -> set[str]:
    return {
        str((template.get(key) or {}).get("kind") or "")
        for key in ("x_evidence", "y_evidence")
        if isinstance(template.get(key), dict)
    }


def risk_record(
    template: dict[str, Any],
    occurrence: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    score = 0
    mandatory = False
    tier = "P3"
    status = str(template.get("evidence_status") or "")
    group_status = str(template.get("group_status") or "")
    axis_key = str(template.get("axis_position_key") or "")
    method = str(template.get("location_method") or "")
    evidence_kinds = kinds(template)
    edge = fraction_edge_distance(template)
    match_ratio = max_match_ratio(template)
    delta = float(occurrence.get("max_axis_fraction_delta") or 0.0)
    mapping_issues = int(occurrence.get("mapping_issue_count") or 0)

    if status != "evidence_trace_complete":
        score += 1000
        reasons.append("V23证据链未闭合")
        mandatory = True
        tier = "P0"
    if mapping_issues:
        score += 500
        reasons.append(f"跨视图映射异常{mapping_issues}项")
        mandatory = True
        tier = "P0"
    if "OUT" in axis_key.upper():
        score += 180
        reasons.append("最外轴OUT位置")
        mandatory = True
        if tier != "P0":
            tier = "P1"
    if any(value.startswith("curved_axis") for value in evidence_kinds):
        score += 150
        reasons.append("圆弧/切向延伸轴网")
        mandatory = True
        if tier != "P0":
            tier = "P1"
    if group_status not in {
        "cross_view_quantity_closed",
        "single_primary_device_location_complete",
        "direct_v12_result",
    }:
        score += 120
        reasons.append(f"上游状态:{group_status or '空'}")
        mandatory = True
        if tier != "P0":
            tier = "P1"
    if delta >= 0.06:
        score += 120
        reasons.append(f"跨视图轴间比例偏差{delta:.4f}")
        mandatory = True
        if tier != "P0":
            tier = "P1"
    elif delta >= 0.03:
        score += 55
        reasons.append(f"跨视图轴间比例偏差{delta:.4f}")
        if tier == "P3":
            tier = "P2"
    if match_ratio >= 0.10:
        score += 100
        reasons.append(f"轴线/轴号匹配距离比{match_ratio:.3f}")
        mandatory = True
        if tier != "P0":
            tier = "P1"
    elif match_ratio >= 0.03:
        score += 40
        reasons.append(f"轴线匹配距离比{match_ratio:.3f}")
        if tier == "P3":
            tier = "P2"
    if "line_strip" in evidence_kinds or method == "building_axis_grid_geometry":
        score += 35
        reasons.append("旋转/扇形直轴几何定位")
        if tier == "P3":
            tier = "P2"
    if edge <= 0.01:
        score += 90
        reasons.append(f"极靠近轴线边界{edge:.4f}")
        mandatory = True
        if tier != "P0":
            tier = "P1"
    elif edge <= 0.05:
        score += 30
        reasons.append(f"靠近轴线边界{edge:.4f}")
        if tier == "P3":
            tier = "P2"
    if not reasons:
        reasons.append("普通正交轴网低风险抽样")

    drawing_value = review_drawing_from_source(template)
    review_copy, drawing_issue = safe_review_copy(drawing_value)
    if drawing_issue:
        score += 20
        reasons.append(drawing_issue)

    primary_key = str(template.get("primary_instance_key") or "")
    highlight_handles = sorted(
        set(
            [
                *(str(value) for value in template.get("label_handles") or []),
                *(str(value) for value in template.get("geometry_handles") or []),
                *(part for part in primary_key.split("/") if part),
            ]
        )
    )
    spacings = [
        value
        for key in ("x_evidence", "y_evidence")
        if (value := axis_spacing(template.get(key))) is not None
    ]
    zoom_width = max(2000.0, min(25000.0, max(spacings, default=6000.0) * 1.4))
    stable_id = hashlib.sha1(
        (
            str(template.get("group_id") or "")
            + "|"
            + str(template.get("physical_template_id") or "")
        ).encode("utf-8")
    ).hexdigest()[:10].upper()
    return {
        "review_task_id": f"V24-{stable_id}",
        "risk_tier": tier,
        "risk_score": score,
        "mandatory_review": mandatory,
        "selected_for_review": False,
        "selection_reason": "",
        "risk_reasons": "；".join(reasons),
        "group_id": str(template.get("group_id") or ""),
        "group_status": group_status,
        "physical_template_id": str(
            template.get("physical_template_id") or ""
        ),
        "building_id": str(template.get("building_id") or ""),
        "floors": "、".join(str(value) for value in template.get("floors") or []),
        "axis_position_key": axis_key,
        "primary_instance_key": primary_key,
        "primary_world_x": str(template.get("primary_world_x") or ""),
        "primary_world_y": str(template.get("primary_world_y") or ""),
        "location_method": method,
        "evidence_status": status,
        "edge_fraction": f"{edge:.6f}",
        "max_match_ratio": f"{match_ratio:.6f}",
        "cross_view_occurrence_count": int(
            occurrence.get("occurrence_count") or 0
        ),
        "max_axis_fraction_delta": f"{delta:.6f}",
        "mapping_issue_count": mapping_issues,
        "preview_relative": str(template.get("preview_relative") or ""),
        "v23_source_file": str(template.get("source_file") or ""),
        "review_drawing_path": drawing_value,
        "safe_review_copy": review_copy,
        "drawing_issue": drawing_issue,
        "zoom_width": f"{zoom_width:.3f}",
        "highlight_handles": ";".join(highlight_handles),
    }


def stratified_sample(
    rows: list[dict[str, Any]], rate: float, cap: int
) -> list[dict[str, Any]]:
    if not rows or rate <= 0 or cap <= 0:
        return []
    buildings = sorted(
        {str(row.get("building_id") or "(空)") for row in rows}
    )
    target = max(len(buildings), math.ceil(len(rows) * rate))
    target = min(len(rows), cap, target)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(
        rows,
        key=lambda item: (
            -int(item["risk_score"]),
            str(item["group_id"]),
            str(item["physical_template_id"]),
        ),
    ):
        buckets[str(row.get("building_id") or "(空)")].append(row)
    selected: list[dict[str, Any]] = []
    index = 0
    while len(selected) < target:
        added = False
        for building in buildings:
            bucket = buckets[building]
            if index < len(bucket):
                selected.append(bucket[index])
                added = True
                if len(selected) >= target:
                    break
        if not added:
            break
        index += 1
    return selected


def select_rows(
    rows: list[dict[str, Any]],
    medium_rate: float,
    low_rate: float,
    medium_cap: int,
    low_cap: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        if row["mandatory_review"]:
            row["selected_for_review"] = True
            row["selection_reason"] = "强制复核"
            selected.append(row)
    already = {row["review_task_id"] for row in selected}
    for tier, rate, cap, reason in (
        ("P2", medium_rate, medium_cap, "中风险分层抽样"),
        ("P3", low_rate, low_cap, "低风险分层抽样"),
    ):
        candidates = [
            row
            for row in rows
            if row["risk_tier"] == tier
            and row["review_task_id"] not in already
        ]
        for row in stratified_sample(candidates, rate, cap):
            row["selected_for_review"] = True
            row["selection_reason"] = reason
            selected.append(row)
            already.add(row["review_task_id"])
    return sorted(
        selected,
        key=lambda row: (
            {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[row["risk_tier"]],
            -int(row["risk_score"]),
            str(row["building_id"]),
            str(row["physical_template_id"]),
        ),
    )


def lisp_string(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", " ")
        .replace("\n", " ")
    )


def build_lisp(path: Path, rows: list[dict[str, Any]]) -> int:
    tasks = [
        row
        for row in rows
        if row.get("safe_review_copy")
        and optional_float(row.get("primary_world_x")) is not None
        and optional_float(row.get("primary_world_y")) is not None
    ]
    entries: list[str] = []
    for row in tasks:
        handles = " ".join(
            f'"{lisp_string(value)}"'
            for value in str(row.get("highlight_handles") or "").split(";")
            if value
        )
        entries.append(
            '  (list "{task}" "{drawing}" "{path}" {x:.12g} {y:.12g} '
            '{zoom:.12g} (list {handles}) "{tier}" "{reason}")'.format(
                task=lisp_string(row["review_task_id"]),
                drawing=lisp_string(Path(row["safe_review_copy"]).name),
                path=lisp_string(row["safe_review_copy"]),
                x=float(row["primary_world_x"]),
                y=float(row["primary_world_y"]),
                zoom=float(row["zoom_width"]),
                handles=handles,
                tier=lisp_string(row["risk_tier"]),
                reason=lisp_string(row["risk_reasons"]),
            )
        )
    body = """;;; V24 中望CAD只读工作副本回查定位
;;; 命令：V24LIST、V24GOTO、V24OPEN。脚本不保存、不修改DWG。
(vl-load-com)
(setq *V24-TASKS*
  (list
{entries}
  )
)

(defun v24-find (task-id / found item)
  (setq found nil)
  (foreach item *V24-TASKS*
    (if (= (strcase task-id) (strcase (car item)))
      (setq found item)
    )
  )
  found
)

(defun v24-highlight (handles / ss ent)
  (setq ss (ssadd))
  (foreach h handles
    (setq ent (handent h))
    (if ent (ssadd ent ss))
  )
  (if (> (sslength ss) 0)
    (sssetfirst nil ss)
    (sssetfirst nil nil)
  )
  (sslength ss)
)

(defun v24-show (item / expected current point selected)
  (setq expected (nth 1 item))
  (setq current (getvar "DWGNAME"))
  (if (/= (strcase expected) (strcase current))
    (prompt (strcat "\\nV24安全停止：当前图纸 " current
                    " 与任务图纸 " expected " 不一致。"))
    (progn
      (setq point (list (nth 3 item) (nth 4 item) 0.0))
      (command "_.ZOOM" "_C" point (nth 5 item))
      (setq selected (v24-highlight (nth 6 item)))
      (prompt (strcat "\\nV24已定位 " (car item)
                      "，风险 " (nth 7 item)
                      "，高亮句柄数 " (itoa selected)
                      "。\\n原因：" (nth 8 item)))
    )
  )
  (princ)
)

(defun c:V24LIST (/ item)
  (foreach item *V24-TASKS*
    (prompt (strcat "\\n" (car item) " | " (nth 7 item)
                    " | " (nth 1 item) " | " (nth 8 item)))
  )
  (prompt (strcat "\\n共 " (itoa (length *V24-TASKS*)) " 个可回查任务。"))
  (princ)
)

(defun c:V24GOTO (/ task-id item)
  (setq task-id (getstring T "\\n输入V24任务ID: "))
  (setq item (v24-find task-id))
  (if item
    (v24-show item)
    (prompt "\\n未找到该V24任务ID。")
  )
  (princ)
)

(defun c:V24OPEN (/ task-id item docs doc)
  (setq task-id (getstring T "\\n输入V24任务ID并只读打开工作副本: "))
  (setq item (v24-find task-id))
  (if (not item)
    (prompt "\\n未找到该V24任务ID。")
    (progn
      (setq docs (vla-get-Documents (vlax-get-acad-object)))
      (setq doc (vla-Open docs (nth 2 item) :vlax-true))
      (vla-Activate doc)
      (v24-show item)
    )
  )
  (princ)
)

(prompt "\\nV24回查脚本已加载。使用 V24LIST / V24GOTO / V24OPEN。")
(princ)
""".format(entries="\n".join(entries))
    path.write_text(body, encoding="utf-8")
    return len(tasks)


def build_html(
    output_path: Path,
    selected: list[dict[str, Any]],
    v23_root: Path,
) -> None:
    cards: list[str] = []
    for row in selected:
        preview_value = str(row.get("preview_relative") or "")
        preview_path = v23_root / preview_value
        relative = (
            os.path.relpath(preview_path, output_path.parent).replace("\\", "/")
            if preview_value and preview_path.is_file()
            else ""
        )
        image = (
            f'<a href="{html.escape(relative)}"><img src="{html.escape(relative)}" '
            'alt="V23局部证据"></a>'
            if relative
            else '<div class="missing">无SVG预览</div>'
        )
        cards.append(
            "<article>"
            f"<h2>{html.escape(row['review_task_id'])} / "
            f"{html.escape(row['physical_template_id'])}</h2>"
            f"<p><b>{html.escape(row['risk_tier'])}</b>　"
            f"分值 {row['risk_score']}　{html.escape(row['selection_reason'])}</p>"
            f"<p>楼栋 {html.escape(row['building_id'])}　"
            f"楼层 {html.escape(row['floors'])}</p>"
            f"<p>{html.escape(row['axis_position_key'])}</p>"
            f"<p>{html.escape(row['risk_reasons'])}</p>"
            f"{image}</article>"
        )
    output_path.write_text(
        """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>V24风险分层抽查索引</title><style>
body{margin:0;background:#eef2f7;color:#172033;font:15px/1.55 Arial,"Microsoft YaHei",sans-serif}
header{padding:24px 32px;background:#172033;color:white}main{padding:24px 32px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:18px}
article{background:white;border-radius:10px;padding:18px;box-shadow:0 2px 10px #0001}
article h2{margin:0 0 8px;font-size:18px}img{width:100%;border:1px solid #d7deea}
.missing{padding:40px;background:#fee2e2;color:#991b1b;text-align:center}
</style></head><body>"""
        + f"<header><h1>V24风险分层抽查索引</h1><p>抽查模板 {len(selected)}</p></header>"
        + '<main><section class="grid">'
        + "\n".join(cards)
        + "</section></main></body></html>\n",
        encoding="utf-8",
    )


FIELDS = [
    "review_task_id",
    "risk_tier",
    "risk_score",
    "mandatory_review",
    "selected_for_review",
    "selection_reason",
    "risk_reasons",
    "group_id",
    "group_status",
    "physical_template_id",
    "building_id",
    "floors",
    "axis_position_key",
    "primary_instance_key",
    "primary_world_x",
    "primary_world_y",
    "location_method",
    "evidence_status",
    "edge_fraction",
    "max_match_ratio",
    "cross_view_occurrence_count",
    "max_axis_fraction_delta",
    "mapping_issue_count",
    "preview_relative",
    "v23_source_file",
    "review_drawing_path",
    "safe_review_copy",
    "drawing_issue",
    "zoom_width",
    "highlight_handles",
]


def main() -> int:
    args = parse_args()
    v23_path = args.v23_json.resolve()
    if not v23_path.is_file():
        raise FileNotFoundError(v23_path)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = load_json(v23_path)
    templates = payload.get("templates") or []
    if not isinstance(templates, list):
        raise ValueError("V23 JSON缺少templates数组")
    cross_view = load_cross_view_metrics(templates)
    rows: list[dict[str, Any]] = []
    for template in templates:
        manifest = str(template.get("manifest_path") or "")
        template_id = str(template.get("physical_template_id") or "")
        rows.append(
            risk_record(
                template,
                cross_view.get((manifest, template_id), {}),
            )
        )
    rows.sort(
        key=lambda row: (
            {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[row["risk_tier"]],
            -int(row["risk_score"]),
            str(row["building_id"]),
            str(row["physical_template_id"]),
        )
    )
    selected = select_rows(
        rows,
        args.medium_sample_rate,
        args.low_sample_rate,
        args.medium_sample_cap,
        args.low_sample_cap,
    )
    write_csv(output_dir / "V24风险分层全量.csv", rows, FIELDS)
    write_csv(output_dir / "V24抽查任务.csv", selected, FIELDS)
    unresolved_groups = payload.get("unresolved_groups") or []
    write_csv(
        output_dir / "V24未定位证据组.csv",
        [
            {
                "risk_tier": "P0",
                "group_id": str(row.get("group_id") or ""),
                "status": str(row.get("status") or ""),
                "reason": str(row.get("reason") or ""),
            }
            for row in unresolved_groups
        ],
        ["risk_tier", "group_id", "status", "reason"],
    )
    ai_rows = [
        {
            key: row[key]
            for key in (
                "review_task_id",
                "risk_tier",
                "risk_score",
                "selection_reason",
                "risk_reasons",
                "building_id",
                "floors",
                "axis_position_key",
                "primary_world_x",
                "primary_world_y",
                "preview_relative",
                "safe_review_copy",
            )
        }
        for row in selected
    ]
    (output_dir / "V24AI轻量抽查清单.json").write_text(
        json.dumps(
            {
                "version": "V24",
                "source_v23": str(v23_path),
                "selected_template_count": len(selected),
                "unresolved_group_count": len(unresolved_groups),
                "tasks": ai_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    lisp_count = build_lisp(
        output_dir / "V24中望回查定位.lsp",
        selected,
    )
    build_html(
        output_dir / "V24风险抽查索引.html",
        selected,
        v23_path.parent,
    )
    tier_counts = Counter(row["risk_tier"] for row in rows)
    selected_tier_counts = Counter(row["risk_tier"] for row in selected)
    missing_copy_count = sum(not row["safe_review_copy"] for row in selected)
    status = (
        "v24_sampling_ready"
        if not unresolved_groups and not missing_copy_count
        else "v24_sampling_ready_project_partial"
    )
    summary = {
        "version": "V24",
        "status": status,
        "source_v23": str(v23_path),
        "template_count": len(rows),
        "selected_template_count": len(selected),
        "mandatory_template_count": sum(
            bool(row["mandatory_review"]) for row in rows
        ),
        "lisp_review_task_count": lisp_count,
        "selected_missing_safe_copy_count": missing_copy_count,
        "unresolved_group_count": len(unresolved_groups),
        "tier_counts": dict(tier_counts),
        "selected_tier_counts": dict(selected_tier_counts),
        "full_csv": str(output_dir / "V24风险分层全量.csv"),
        "selected_csv": str(output_dir / "V24抽查任务.csv"),
        "ai_manifest": str(output_dir / "V24AI轻量抽查清单.json"),
        "review_lisp": str(output_dir / "V24中望回查定位.lsp"),
        "review_html": str(output_dir / "V24风险抽查索引.html"),
    }
    (output_dir / "V24风险抽查汇总.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# V24 风险分层抽样与中望自动回查",
        "",
        "## 结果",
        "",
        f"- 状态：`{status}`。",
        f"- V23模板：{len(rows)}；抽查：{len(selected)}；"
        f"强制复核：{summary['mandatory_template_count']}。",
        f"- 可由中望LISP自动打开/定位的只读工作副本任务：{lisp_count}；"
        f"缺安全工作副本：{missing_copy_count}。",
        f"- 未形成逐台坐标的上游证据组：{len(unresolved_groups)}，"
        "此类问题不能用SVG或坐标回查替代。",
        "",
        "## 风险层级",
        "",
        "- P0：证据链或跨视图映射未闭合，全部复核。",
        "- P1：OUT、弧形轴网、上游未闭合、极靠近边界或高偏差，全部复核。",
        "- P2：旋转/扇形轴网、较大跨视图偏差或靠近边界，分层抽样。",
        "- P3：普通正交轴网低风险项，按楼栋少量抽样。",
        "",
        "## 中望回查",
        "",
        "1. 仅使用非OneDrive目录中的分析副本；原始DWG不自动打开。",
        "2. 中望执行 `APPLOAD`，加载 `V24中望回查定位.lsp`。",
        "3. `V24LIST`列出任务；`V24OPEN`只读打开任务工作副本并定位；"
        "`V24GOTO`在当前正确图纸中定位。",
        "4. LISP只缩放并尝试按句柄高亮，不保存、不修改图纸。",
        "",
        "## AI读取",
        "",
        "- AI优先读取`V24AI轻量抽查清单.json`，只在任务需要时读取对应SVG。",
        "- 不要把V23全部SVG、完整JSON和CSV同时塞入上下文。",
    ]
    (output_dir / "V24风险抽查说明.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
