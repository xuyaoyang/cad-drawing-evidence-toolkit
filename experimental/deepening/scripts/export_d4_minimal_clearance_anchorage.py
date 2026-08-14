#!/usr/bin/env python3
"""Export the minimum per-device clearance and beam-anchorage deliverable.

This is deliberately a reporting/consolidation stage.  It consumes an existing
read-only D4 current-state document and its shared formal-beam ledger; it does
not reopen or modify DWG files and does not promote missing evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


OUTPUT_SCHEMA = "D4-minimal-clearance-anchorage-report-1.0"
SUPPORTED_STATE_SCHEMAS = {
    "D4-unified-formal-support-beam-integrated-clearance-state-1.0",
    "D4-formal-beam-integrated-clearance-state-1.0",
}
SUPPORTED_LEDGER_SCHEMAS = {
    "D4-unified-formal-support-beam-ledger-1.0",
    "D4-formal-shared-beam-ledger-1.0",
}
TOLERANCE_MM = 1.0


class SafetyStop(RuntimeError):
    """Raised when an input contract or coverage invariant is broken."""


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows(value: Any) -> list[Mapping[str, Any]]:
    return [row for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _hash_value(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _snapshot(path: Path, role: str) -> dict[str, Any]:
    return {"role": role, "path": str(path.resolve()), "sha256": _sha(path), "bytes": path.stat().st_size}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise SafetyStop(f"JSON顶层必须是对象：{path}")
    return value


def _quantity(record: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    inputs = record.get("inputs") if isinstance(record.get("inputs"), Mapping) else {}
    value = inputs.get(key)
    return value if isinstance(value, Mapping) else {}


def _confirmed_number(quantity: Mapping[str, Any]) -> float | None:
    value = _number(quantity.get("value_mm"))
    return value if _clean(quantity.get("status")) == "已确认" and value is not None else None


def _lower_beam_top_offset(record: Mapping[str, Any]) -> tuple[float | None, str, str]:
    direct = _quantity(record, "lower_beam_top_offset_mm")
    value = _confirmed_number(direct)
    if value is not None:
        return value, "已确认", _clean(direct.get("reason")) or "direct_lower_beam_top_offset"
    legacy = _quantity(record, "lower_installation_surface_offset_mm")
    reason = _clean(legacy.get("reason"))
    value = _confirmed_number(legacy)
    if value is not None and "beam_top" in reason.lower():
        return value, "已确认", reason
    return None, _clean(direct.get("status") or legacy.get("status")) or "资料不足", reason or "missing_confirmed_lower_beam_top_offset"


def _drop(offset: float | None, status: str) -> tuple[str, float | None, str]:
    if status != "已确认" or offset is None:
        return "资料不足", None, "缺少已确认的梁顶/板面偏移"
    if offset < -TOLERANCE_MM:
        return "有", abs(offset), f"梁顶相对楼层标高降低{abs(offset):g}mm"
    if offset > TOLERANCE_MM:
        return "升板", abs(offset), f"梁顶相对楼层标高升高{abs(offset):g}mm"
    return "无", 0.0, "梁顶与楼层标高一致"


def _normal_and_along(envelope: Sequence[Any], orientation: str) -> tuple[list[float], list[float]] | None:
    if len(envelope) != 4:
        return None
    values = [_number(value) for value in envelope]
    if any(value is None for value in values):
        return None
    xmin, ymin, xmax, ymax = (float(value) for value in values)
    if orientation == "horizontal":
        return sorted([ymin, ymax]), sorted([xmin, xmax])
    if orientation == "vertical":
        return sorted([xmin, xmax]), sorted([ymin, ymax])
    return None


def _same_support_identity(device: Mapping[str, Any], link: Mapping[str, Any], beam: Mapping[str, Any]) -> bool:
    identity = beam.get("identity") if isinstance(beam.get("identity"), Mapping) else {}
    return (
        _clean(identity.get("building_id")),
        _clean(identity.get("support_floor")),
        _clean(identity.get("component_id")),
        _clean(identity.get("actual_insert_root")).upper(),
        _clean(identity.get("orientation")),
    ) == (
        _clean(device.get("building_id")),
        _clean(link.get("support_floor")),
        _clean(link.get("component_id")),
        _clean(link.get("actual_insert_root")).upper(),
        _clean(link.get("orientation")),
    )


def _formal_geometry_result(
    device: Mapping[str, Any], role: str, link: Mapping[str, Any], beam: Mapping[str, Any]
) -> tuple[str, str, str]:
    if not _same_support_identity(device, link, beam):
        return "冲突", "待人工核实", "正式梁台账与支承楼层/分区/块实例/方向不一致"
    projection = _normal_and_along(link.get("mapped_envelope") or [], _clean(link.get("orientation")))
    identity = beam.get("identity") if isinstance(beam.get("identity"), Mapping) else {}
    faces = identity.get("face_coordinates_mm") if isinstance(identity.get("face_coordinates_mm"), list) else []
    if projection is None or len(faces) != 2:
        return "资料不足", "待人工核实", "缺少可复核的设备投影或双梁面坐标"
    normal, along = projection
    face_values = sorted(float(value) for value in faces)
    if normal[0] < face_values[0] - TOLERANCE_MM or normal[1] > face_values[1] + TOLERANCE_MM:
        return "冲突", "待人工核实", "设备法向投影未落入正式梁双梁面范围"
    consumers = [
        row for row in _rows(beam.get("consumer_supports"))
        if _clean(row.get("registry_device_id")) == _clean(device.get("registry_device_id"))
        and _clean(row.get("support_role")) == role
    ]
    if not consumers:
        return "候选", "待人工核实", "正式梁几何匹配但台账缺少该设备支承角色绑定"
    intervals = [interval for interval in beam.get("target_along_intervals_mm") or [] if isinstance(interval, list) and len(interval) == 2]
    if not intervals:
        return "候选", "待人工核实", "法向投影闭合，但缺少梁长度方向有效区间"
    if any(along[0] >= min(interval) - TOLERANCE_MM and along[1] <= max(interval) + TOLERANCE_MM for interval in intervals):
        return "已确认", "已生根", "设备投影完整落入正式梁双梁面及长度方向有效区间"
    return "冲突", "待人工核实", "设备长度方向投影未完整落入正式梁有效区间"


def _anchorage(
    device: Mapping[str, Any], role: str, beam_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    links = device.get("support_links") if isinstance(device.get("support_links"), Mapping) else {}
    link = links.get(role) if isinstance(links.get(role), Mapping) else {}
    references = device.get("beam_references") if isinstance(device.get("beam_references"), Mapping) else {}
    direct_id = _clean(references.get(f"{role}_beam_id"))
    segment_id = _clean(link.get("p5_beam_segment_id"))
    if _clean(link.get("mapping_status")) != "已确认":
        return {"status": "资料不足", "conclusion": "待人工核实", "reason": "跨楼层设备投影尚未确认", "beam_id": direct_id or segment_id}
    if direct_id and direct_id != segment_id:
        return {"status": "冲突", "conclusion": "待人工核实", "reason": "设备梁引用与支承链梁段ID不一致", "beam_id": direct_id}
    if direct_id and _clean(link.get("p5_physical_status")) == "已确认" and _clean(link.get("beam_reference_status")) == "已确认":
        return {
            "status": "已确认",
            "conclusion": "已生根",
            "reason": "设备投影已唯一绑定已确认物理梁段",
            "beam_id": direct_id,
            "evidence_ids": sorted({_clean(value) for value in link.get("dependency_ids") or [] if _clean(value)}),
        }
    formal_refs = device.get("formal_shared_beam_references") if isinstance(device.get("formal_shared_beam_references"), Mapping) else {}
    formal = formal_refs.get(role) if isinstance(formal_refs.get(role), Mapping) else {}
    formal_id = _clean(formal.get("formal_beam_id"))
    if _clean(formal.get("status")) == "已确认" and formal_id:
        beam = beam_by_id.get(formal_id)
        if beam is None:
            return {"status": "候选", "conclusion": "待人工核实", "reason": "存在正式梁文字引用，但共享梁台账缺少对应记录", "beam_id": formal_id}
        status, conclusion, reason = _formal_geometry_result(device, role, link, beam)
        # Formal text/face recognition was developed to recover sections.  It is
        # valuable supporting evidence, but without a confirmed P5 physical
        # segment it must not independently promote geometric anchorage.
        if status == "已确认":
            reason = f"{reason}；但缺少已确认物理梁段，保持候选"
        elif status == "冲突":
            reason = f"正式梁辅助几何与设备支承链不一致：{reason}；不据此判定未生根"
        return {"status": "候选", "conclusion": "待人工核实", "reason": reason, "beam_id": formal_id}
    physical_status = _clean(link.get("p5_physical_status"))
    if physical_status == "候选" or direct_id or segment_id:
        return {"status": "候选", "conclusion": "待人工核实", "reason": "存在梁段候选，但物理梁或引用尚未确认", "beam_id": direct_id or segment_id}
    return {"status": "资料不足", "conclusion": "待人工核实", "reason": "未形成可回查的支承梁证据链", "beam_id": ""}


def _beam_reference(device: Mapping[str, Any], role: str) -> dict[str, Any]:
    containers = (
        "formal_shared_beam_references",
        "reviewed_beam_references",
        "evidence_augmented_beam_references",
    )
    for name in containers:
        container = device.get(name) if isinstance(device.get(name), Mapping) else {}
        ref = container.get(role) if isinstance(container.get(role), Mapping) else {}
        if _clean(ref.get("status")) == "已确认":
            return {
                "beam_number": _clean(ref.get("beam_number")),
                "width_mm": _number(ref.get("width_mm")),
                "height_mm": _number(ref.get("height_mm")),
                "source": name,
            }
    if role == "upper":
        value = _quantity(device, "upper_beam_height_mm")
        return {
            "beam_number": _clean(value.get("beam_number")),
            "width_mm": None,
            "height_mm": _confirmed_number(value),
            "source": _clean(value.get("source_stage")),
        }
    return {"beam_number": "", "width_mm": None, "height_mm": None, "source": ""}


def build_report(
    state: Mapping[str, Any], ledger: Mapping[str, Any], *, state_snapshot: Mapping[str, Any], ledger_snapshot: Mapping[str, Any], expected_count: int | None
) -> dict[str, Any]:
    state_schema = _clean(state.get("schema_version"))
    ledger_schema = _clean(ledger.get("schema_version"))
    if state_schema not in SUPPORTED_STATE_SCHEMAS:
        raise SafetyStop(f"不支持的当前状态版本：{state_schema}")
    if ledger_schema not in SUPPORTED_LEDGER_SCHEMAS:
        raise SafetyStop(f"不支持的共享梁台账版本：{ledger_schema}")
    devices = _rows(state.get("device_current_state"))
    ids = [_clean(row.get("registry_device_id")) for row in devices]
    if not devices or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise SafetyStop("设备ID缺失或重复")
    if expected_count is not None and len(devices) != expected_count:
        raise SafetyStop(f"设备数量不符合预期：预期{expected_count}，实际{len(devices)}")
    beams = _rows(ledger.get("beam_records"))
    beam_ids = [_clean(row.get("formal_beam_id")) for row in beams]
    if any(not value for value in beam_ids) or len(set(beam_ids)) != len(beam_ids):
        raise SafetyStop("共享梁ID缺失或重复")
    beam_by_id = dict(zip(beam_ids, beams))
    output_rows: list[dict[str, Any]] = []
    for device in sorted(devices, key=lambda row: _clean(row.get("registry_device_id"))):
        lower_level_q = _quantity(device, "z_lower_level_mm")
        upper_level_q = _quantity(device, "z_upper_level_mm")
        upper_offset_q = _quantity(device, "upper_beam_top_offset_mm")
        upper_height_q = _quantity(device, "upper_beam_height_mm")
        lower_level = _confirmed_number(lower_level_q)
        upper_level = _confirmed_number(upper_level_q)
        upper_offset = _confirmed_number(upper_offset_q)
        upper_height = _confirmed_number(upper_height_q)
        lower_offset, lower_offset_status, lower_offset_reason = _lower_beam_top_offset(device)
        lower_top = lower_level + lower_offset if lower_level is not None and lower_offset is not None and lower_offset_status == "已确认" else None
        upper_top = upper_level + upper_offset if upper_level is not None and upper_offset is not None else None
        clearance = upper_top - upper_height - lower_top if upper_top is not None and upper_height is not None and lower_top is not None else None
        lower_drop, lower_drop_height, lower_drop_reason = _drop(lower_offset, lower_offset_status)
        upper_drop, upper_drop_height, upper_drop_reason = _drop(upper_offset, _clean(upper_offset_q.get("status")))
        lower_anchor = _anchorage(device, "lower", beam_by_id)
        upper_anchor = _anchorage(device, "upper", beam_by_id)
        lower_beam = _beam_reference(device, "lower")
        upper_beam = _beam_reference(device, "upper")
        missing: list[str] = []
        for name, value in (
            ("下梁顶标高", lower_top), ("上梁顶标高", upper_top), ("上梁高度", upper_height), ("梁间净空", clearance)
        ):
            if value is None:
                missing.append(name)
        for role_name, anchor in (("下梁生根", lower_anchor), ("上梁生根", upper_anchor)):
            if anchor["status"] != "已确认":
                missing.append(f"{role_name}:{anchor['status']}")
        if any(anchor["status"] == "冲突" for anchor in (lower_anchor, upper_anchor)):
            overall = "冲突"
        elif missing:
            overall = "待人工核实"
        else:
            overall = "已确认"
        baseline = _number(device.get("clear_height_mm"))
        delta = None if clearance is None or baseline is None else clearance - baseline
        output_rows.append(
            {
                "registry_device_id": _clean(device.get("registry_device_id")),
                "device_type": _clean(device.get("device_type")),
                "building_id": _clean(device.get("building_id")),
                "floor": _clean(device.get("floor")),
                "component_id": _clean(device.get("component_id")),
                "axis_position": _clean(device.get("axis_position")),
                "orientation": _clean(device.get("orientation")),
                "device_model": _clean(device.get("device_model") or device.get("model")),
                "lower_beam": lower_beam,
                "lower_beam_top_elevation_mm": lower_top,
                "lower_beam_top_offset_mm": lower_offset,
                "lower_beam_top_offset_status": lower_offset_status,
                "lower_beam_top_offset_reason": lower_offset_reason,
                "lower_drop_status": lower_drop,
                "lower_drop_height_mm": lower_drop_height,
                "lower_drop_reason": lower_drop_reason,
                "lower_anchorage": lower_anchor,
                "upper_beam": upper_beam,
                "upper_beam_top_elevation_mm": upper_top,
                "upper_beam_top_offset_mm": upper_offset,
                "upper_beam_top_offset_status": _clean(upper_offset_q.get("status")) or "资料不足",
                "upper_drop_status": upper_drop,
                "upper_drop_height_mm": upper_drop_height,
                "upper_drop_reason": upper_drop_reason,
                "upper_beam_height_mm": upper_height,
                "upper_beam_height_status": _clean(upper_height_q.get("status")) or "资料不足",
                "upper_anchorage": upper_anchor,
                "beam_clearance_excluding_upturn_mm": None if clearance is None else round(clearance, 6),
                "clearance_status": "已确认" if clearance is not None else "资料不足",
                "baseline_clear_height_mm": baseline,
                "baseline_delta_mm": None if delta is None else round(delta, 6),
                "upturn_included": False,
                "upturn_review_status": "后续处理",
                "overall_status": overall,
                "unresolved_reasons": missing,
            }
        )
    status_counts = Counter(row["overall_status"] for row in output_rows)
    lower_counts = Counter(row["lower_anchorage"]["status"] for row in output_rows)
    upper_counts = Counter(row["upper_anchorage"]["status"] for row in output_rows)
    report: dict[str, Any] = {
        "schema_version": OUTPUT_SCHEMA,
        "report_kind": "per_device_minimal_clearance_and_geometric_beam_anchorage",
        "source_manifest": {"inputs": [dict(state_snapshot), dict(ledger_snapshot)]},
        "devices": output_rows,
        "stats": {
            "input_device_count": len(devices),
            "output_device_count": len(output_rows),
            "unique_output_device_count": len({row["registry_device_id"] for row in output_rows}),
            "confirmed_clearance_count": sum(row["clearance_status"] == "已确认" for row in output_rows),
            "lower_anchorage_status_counts": dict(sorted(lower_counts.items())),
            "upper_anchorage_status_counts": dict(sorted(upper_counts.items())),
            "overall_status_counts": dict(sorted(status_counts.items())),
            "baseline_clearance_mismatch_count": sum(
                row["baseline_delta_mm"] is not None and abs(row["baseline_delta_mm"]) > TOLERANCE_MM for row in output_rows
            ),
        },
        "boundary": {
            "one_row_per_input_device": True,
            "clearance_is_beam_to_beam_and_excludes_upturn": True,
            "anchorage_is_geometric_envelope_closure_only": True,
            "bearing_capacity_checked": False,
            "reinforcement_checked": False,
            "wall_concealment_checked": False,
            "upturn_dimensions_checked": False,
            "missing_values_filled_with_zero": False,
            "original_dwg_modified": False,
            "production_release": False,
        },
    }
    report["report_hash"] = _hash_value(report)
    return report


CSV_FIELDS = [
    "设备ID", "设备类型", "楼栋", "楼层", "分区", "轴号", "方向", "型号",
    "下梁编号", "下梁顶标高(mm)", "下支承降板状态", "下支承降板高度(mm)", "下梁生根结论", "下梁生根状态", "下梁生根说明",
    "上梁编号", "上梁顶标高(mm)", "上梁高度(mm)", "上支承降板状态", "上支承降板高度(mm)", "上梁生根结论", "上梁生根状态", "上梁生根说明",
    "梁间净空-未计上翻墩(mm)", "净空状态", "上翻墩复核状态", "总体状态", "未决原因",
]


def render_csv(report: Mapping[str, Any]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for row in _rows(report.get("devices")):
        lower_beam = row.get("lower_beam") or {}
        upper_beam = row.get("upper_beam") or {}
        lower_anchor = row.get("lower_anchorage") or {}
        upper_anchor = row.get("upper_anchorage") or {}
        writer.writerow(
            {
                "设备ID": row.get("registry_device_id"), "设备类型": row.get("device_type"), "楼栋": row.get("building_id"),
                "楼层": row.get("floor"), "分区": row.get("component_id"), "轴号": row.get("axis_position"), "方向": row.get("orientation"), "型号": row.get("device_model"),
                "下梁编号": lower_beam.get("beam_number"), "下梁顶标高(mm)": row.get("lower_beam_top_elevation_mm"),
                "下支承降板状态": row.get("lower_drop_status"), "下支承降板高度(mm)": row.get("lower_drop_height_mm"),
                "下梁生根结论": lower_anchor.get("conclusion"), "下梁生根状态": lower_anchor.get("status"), "下梁生根说明": lower_anchor.get("reason"),
                "上梁编号": upper_beam.get("beam_number"), "上梁顶标高(mm)": row.get("upper_beam_top_elevation_mm"), "上梁高度(mm)": row.get("upper_beam_height_mm"),
                "上支承降板状态": row.get("upper_drop_status"), "上支承降板高度(mm)": row.get("upper_drop_height_mm"),
                "上梁生根结论": upper_anchor.get("conclusion"), "上梁生根状态": upper_anchor.get("status"), "上梁生根说明": upper_anchor.get("reason"),
                "梁间净空-未计上翻墩(mm)": row.get("beam_clearance_excluding_upturn_mm"), "净空状态": row.get("clearance_status"),
                "上翻墩复核状态": row.get("upturn_review_status"), "总体状态": row.get("overall_status"), "未决原因": "|".join(row.get("unresolved_reasons") or []),
            }
        )
    return stream.getvalue()


def render_markdown(report: Mapping[str, Any]) -> str:
    stats = report.get("stats") or {}
    lower = stats.get("lower_anchorage_status_counts") or {}
    upper = stats.get("upper_anchorage_status_counts") or {}
    overall = stats.get("overall_status_counts") or {}
    return "\n".join(
        [
            "# 逐台净空与上下梁几何生根结果", "", "## 结果", "",
            f"- 输入/输出/唯一设备：{stats.get('input_device_count')}/{stats.get('output_device_count')}/{stats.get('unique_output_device_count')}。",
            f"- 梁间净空（未计上翻墩）已确认：{stats.get('confirmed_clearance_count')}台。",
            f"- 下梁生根证据状态：{json.dumps(lower, ensure_ascii=False, sort_keys=True)}。",
            f"- 上梁生根证据状态：{json.dumps(upper, ensure_ascii=False, sort_keys=True)}。",
            f"- 总体状态：{json.dumps(overall, ensure_ascii=False, sort_keys=True)}。",
            f"- 与输入状态既有净空差异超过{TOLERANCE_MM:g}mm：{stats.get('baseline_clearance_mismatch_count')}台。", "",
            "## 解释边界", "",
            "- 净空为下梁顶至上梁底的梁间净空，明确未计上翻墩；上翻墩尺寸留待后续版本处理。",
            "- 生根只判断设备投影是否闭合到上下物理梁，不判断承载力、配筋、节点做法或设计是否安全。",
            "- 本阶段不判断墙内隐蔽；候选、资料不足和冲突均未自动提升为已确认。",
            "- 原始DWG未修改，本结果不是生产放行文件。", "",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--beam-ledger", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-device-count", type=int)
    args = parser.parse_args(argv)
    state = _load(args.state)
    ledger = _load(args.beam_ledger)
    report = build_report(
        state,
        ledger,
        state_snapshot=_snapshot(args.state, "current_state"),
        ledger_snapshot=_snapshot(args.beam_ledger, "formal_beam_ledger"),
        expected_count=args.expected_device_count,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "逐台净空与上下梁几何生根.json"
    csv_path = args.output_dir / "逐台净空与上下梁几何生根.csv"
    md_path = args.output_dir / "逐台净空与上下梁几何生根说明.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path.write_text("\ufeff" + render_csv(report), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), "report": str(md_path), "stats": report["stats"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
