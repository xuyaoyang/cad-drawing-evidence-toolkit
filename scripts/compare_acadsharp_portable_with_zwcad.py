#!/usr/bin/env python3
"""Field-level comparison of ACadSharp candidate evidence with ZWCAD exports.

The comparison is intentionally fail-closed.  It reports matched multisets for
fields both backends expose; it never promotes the portable backend to formal
equivalence and never treats an unmatched/unsupported record as absence proof.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "acadsharp-zwcad-field-comparison/0.1"
TEXT_TYPE_MAP = {"DBText": "TEXT", "MText": "MTEXT", "AttributeReference": "ATTRIB"}
PRIMITIVE_TYPE_MAP = {"Line": "LINE", "Polyline": "LWPOLYLINE", "Circle": "CIRCLE", "Arc": "ARC"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def quantize(value: Any, tolerance: float) -> int | str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return "non_finite"
    return int(round(float(value) / tolerance))


def counter_result(candidate: Counter[tuple[Any, ...]], reference: Counter[tuple[Any, ...]]) -> dict[str, int]:
    matched = sum((candidate & reference).values())
    return {
        "candidate": sum(candidate.values()),
        "zwcad_reference": sum(reference.values()),
        "matched": matched,
        "candidate_only": sum((candidate - reference).values()),
        "zwcad_only": sum((reference - candidate).values()),
    }


def text_signature(record: dict[str, Any], entity_type: str, tolerance: float) -> tuple[Any, ...]:
    if "position" in record:
        position = record.get("position") or []
        xyz = list(position) + [None, None, None]
        x, y, z = xyz[:3]
    else:
        x, y, z = record.get("x"), record.get("y"), record.get("z")
    return (
        entity_type,
        str(record.get("handle", "")).upper(),
        str(record.get("text", "")),
        quantize(x, tolerance),
        quantize(y, tolerance),
        quantize(z, tolerance),
    )


def compare_text(portable_records: list[dict[str, Any]], zwcad_records: list[dict[str, Any]], tolerance: float) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for entity_type in ("TEXT", "MTEXT", "ATTRIB"):
        candidate = Counter(
            text_signature(record, entity_type, tolerance)
            for record in portable_records
            if record.get("entity_type") == entity_type
        )
        reference = Counter(
            text_signature(record, entity_type, tolerance)
            for record in zwcad_records
            if TEXT_TYPE_MAP.get(record.get("entity_type")) == entity_type
        )
        output[entity_type] = counter_result(candidate, reference)
    combined_candidate = Counter(
        text_signature(record, "TEXT", tolerance)
        for record in portable_records
        if record.get("entity_type") in {"TEXT", "ATTDEF"}
    )
    combined_reference = Counter(
        text_signature(record, "TEXT", tolerance)
        for record in zwcad_records
        if record.get("entity_type") == "DBText"
    )
    output["TEXT_PLUS_ATTDEF_V5_COMPAT"] = counter_result(combined_candidate, combined_reference)
    output["boundary"] = (
        "ZWCAD V5 DBText includes some AttributeDefinition templates. Portable ATTDEF remains a distinct "
        "template type; the combined row is compatibility-only and does not turn a template into a placed value."
    )
    return output


def insert_presence_signature(record: dict[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("handle", record.get("instance_handle", ""))).upper(),
        str(record.get("block_name", "")),
    )


def insert_transform_signature(record: dict[str, Any], tolerance: float) -> tuple[Any, ...]:
    if "position" in record:
        position = list(record.get("position") or []) + [None, None, None]
        x, y, z = position[:3]
        rotation = record.get("rotation_radians")
        scale = list(record.get("scale") or []) + [None, None, None]
        sx, sy, sz = scale[:3]
    else:
        x, y, z = record.get("x"), record.get("y"), record.get("z")
        rotation = record.get("local_rotation_radians")
        sx, sy, sz = record.get("scale_x"), record.get("scale_y"), record.get("scale_z")
    return insert_presence_signature(record) + (
        quantize(x, tolerance),
        quantize(y, tolerance),
        quantize(z, tolerance),
        quantize(rotation, tolerance),
        quantize(sx, tolerance),
        quantize(sy, tolerance),
        quantize(sz, tolerance),
    )


def insert_position_signature(record: dict[str, Any], tolerance: float) -> tuple[Any, ...]:
    if "position" in record:
        position = list(record.get("position") or []) + [None, None, None]
        x, y, z = position[:3]
    else:
        x, y, z = record.get("x"), record.get("y"), record.get("z")
    return insert_presence_signature(record) + (
        quantize(x, tolerance), quantize(y, tolerance), quantize(z, tolerance)
    )


def compare_inserts(portable_records: list[dict[str, Any]], zwcad_records: list[dict[str, Any]], tolerance: float) -> dict[str, Any]:
    candidates = [record for record in portable_records if record.get("entity_type") == "INSERT"]
    root_candidates = [record for record in candidates if not record.get("block_path")]
    root_reference = [record for record in zwcad_records if not record.get("parent_instance_key")]
    return {
        "identity_by_handle_and_name": counter_result(
            Counter(insert_presence_signature(record) for record in candidates),
            Counter(insert_presence_signature(record) for record in zwcad_records),
        ),
        "world_position": counter_result(
            Counter(insert_position_signature(record, tolerance) for record in candidates),
            Counter(insert_position_signature(record, tolerance) for record in zwcad_records),
        ),
        "root_local_transform_tuple": counter_result(
            Counter(insert_transform_signature(record, tolerance) for record in root_candidates),
            Counter(insert_transform_signature(record, tolerance) for record in root_reference),
        ),
        "local_transform_tuple": counter_result(
            Counter(insert_transform_signature(record, tolerance) for record in candidates),
            Counter(insert_transform_signature(record, tolerance) for record in zwcad_records),
        ),
        "interpretation": (
            "ZWCAD V6 exposes local rotation/scale and world insertion coordinates. Root transform tuples are "
            "comparable. Nested portable INSERT clones may carry composed transforms, so the all-instance transform "
            "tuple is diagnostic only."
        ),
    }


def primitive_bounds(record: dict[str, Any], entity_type: str) -> tuple[float, float, float, float] | None:
    if "min_x" in record:
        values = (record.get("min_x"), record.get("min_y"), record.get("max_x"), record.get("max_y"))
        if all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            return tuple(float(value) for value in values)  # type: ignore[return-value]
        return None
    if entity_type == "LINE":
        start, end = record.get("start") or [], record.get("end") or []
        if len(start) < 2 or len(end) < 2:
            return None
        return min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1])
    if entity_type == "LWPOLYLINE":
        vertices = record.get("vertices") or []
        if not vertices:
            return None
        xs = [vertex.get("x") for vertex in vertices]
        ys = [vertex.get("y") for vertex in vertices]
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in xs + ys):
            return None
        return min(xs), min(ys), max(xs), max(ys)
    if entity_type == "CIRCLE":
        center = record.get("center") or []
        radius = record.get("radius")
        if len(center) < 2 or not isinstance(radius, (int, float)):
            return None
        return center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius
    return None


def primitive_signature(record: dict[str, Any], entity_type: str, tolerance: float) -> tuple[Any, ...] | None:
    if entity_type == "CIRCLE":
        if "center" in record:
            center = list(record.get("center") or []) + [None, None]
            x, y = center[:2]
        else:
            x, y = record.get("center_x"), record.get("center_y")
        return (entity_type, str(record.get("handle", "")).upper(), quantize(x, tolerance), quantize(y, tolerance))
    bounds = primitive_bounds(record, entity_type)
    if bounds is None:
        return None
    signature: tuple[Any, ...] = (
        entity_type,
        str(record.get("handle", "")).upper(),
        *(quantize(value, tolerance) for value in bounds),
    )
    if entity_type == "LWPOLYLINE":
        vertex_count = record.get("vertex_count", len(record.get("vertices") or []))
        signature += (int(vertex_count), bool(record.get("closed", False)))
    return signature


def signatures(records: Iterable[dict[str, Any]], entity_type: str, tolerance: float) -> Counter[tuple[Any, ...]]:
    output: Counter[tuple[Any, ...]] = Counter()
    for record in records:
        signature = primitive_signature(record, entity_type, tolerance)
        if signature is not None:
            output[signature] += 1
    return output


def compare_primitives(portable_records: list[dict[str, Any]], zwcad_records: list[dict[str, Any]], tolerance: float) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for entity_type in ("LINE", "LWPOLYLINE", "CIRCLE"):
        candidate_records = [record for record in portable_records if record.get("entity_type") == entity_type]
        reference_records = [
            record for record in zwcad_records if PRIMITIVE_TYPE_MAP.get(record.get("entity_type")) == entity_type
        ]
        output[entity_type] = counter_result(
            signatures(candidate_records, entity_type, tolerance),
            signatures(reference_records, entity_type, tolerance),
        )
    output["ARC"] = {
        "candidate": sum(record.get("entity_type") == "ARC" for record in portable_records),
        "zwcad_reference": sum(record.get("entity_type") == "Arc" for record in zwcad_records),
        "matched": None,
        "status": "not_compared_bounds_need_independent_arc_extents_validation",
    }
    output["boundary"] = (
        "LWPOLYLINE bounds use listed vertices only; nonzero bulges may extend beyond those bounds and remain unresolved. "
        "CIRCLE compares center only because rotated block extents and non-uniform transforms do not expose an "
        "equivalent radius/bounds field. HATCH and unsupported primitive types are outside this comparison."
    )
    return output


def compare_documents(
    portable: dict[str, Any],
    text_v5: dict[str, Any],
    symbol_v6: dict[str, Any],
    primitive_v10: dict[str, Any],
    visibility_v13: dict[str, Any],
    tolerance: float,
) -> dict[str, Any]:
    portable_records = portable.get("evidence_records") or []
    text_records = text_v5.get("records") or []
    symbol_records = symbol_v6.get("records") or []
    primitive_records = primitive_v10.get("records") or []
    visibility_records = visibility_v13.get("records") or []
    dynamic = sum(bool(record.get("is_dynamic")) for record in visibility_records)
    dynamic_properties = sum(bool(record.get("dynamic_properties")) for record in visibility_records)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "candidate_field_comparison_unresolved",
        "formal_backend_equivalent": False,
        "absence_proven": False,
        "tolerance": tolerance,
        "source_sha256": portable.get("metadata", {}).get("source_sha256", ""),
        "text_v5": compare_text(portable_records, text_records, tolerance),
        "insert_v6": compare_inserts(portable_records, symbol_records, tolerance),
        "primitive_v10": compare_primitives(portable_records, primitive_records, tolerance),
        "visibility_v13": {
            "zwcad_instance_records": len(visibility_records),
            "zwcad_layouts": len(visibility_v13.get("layouts") or []),
            "zwcad_viewports": len(visibility_v13.get("viewports") or []),
            "zwcad_dynamic_instances": dynamic,
            "zwcad_instances_with_dynamic_properties": dynamic_properties,
            "portable_effective_layer_status": portable.get("metadata", {}).get("effective_layer_status"),
            "portable_layout_viewport_visibility_status": portable.get("metadata", {}).get(
                "layout_viewport_visibility_status"
            ),
            "status": "not_compared_portable_fields_unverified",
        },
    }


def markdown_report(result: dict[str, Any]) -> str:
    rows = []
    for name, value in result["text_v5"].items():
        if not isinstance(value, dict):
            continue
        rows.append((f"V5 {name}", value))
    rows.append(("V6 INSERT identity", result["insert_v6"]["identity_by_handle_and_name"]))
    rows.append(("V6 INSERT world position", result["insert_v6"]["world_position"]))
    rows.append(("V6 root INSERT transform", result["insert_v6"]["root_local_transform_tuple"]))
    rows.append(("V6 all INSERT transform diagnostic", result["insert_v6"]["local_transform_tuple"]))
    for name in ("LINE", "LWPOLYLINE", "CIRCLE"):
        label = "CIRCLE center" if name == "CIRCLE" else name
        rows.append((f"V10 {label}", result["primitive_v10"][name]))
    lines = [
        "# ACadSharp portable candidate vs ZWCAD field comparison",
        "",
        f"- Status: `{result['status']}`",
        f"- Source SHA-256: `{result['source_sha256']}`",
        f"- Coordinate tolerance: `{result['tolerance']}`",
        "- Formal backend equivalent: `false`",
        "- Absence proven: `false`",
        "",
        "| Field set | Candidate | ZWCAD | Matched | Candidate only | ZWCAD only |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, value in rows:
        lines.append(
            f"| {label} | {value['candidate']} | {value['zwcad_reference']} | {value['matched']} | "
            f"{value['candidate_only']} | {value['zwcad_only']} |"
        )
    visibility = result["visibility_v13"]
    lines += [
        "",
        "## Unverified boundaries",
        "",
        f"- ZWCAD V13: {visibility['zwcad_layouts']} layouts, {visibility['zwcad_viewports']} viewports, "
        f"{visibility['zwcad_dynamic_instances']} dynamic instances.",
        "- Portable effective-layer inheritance, viewport clipping/frozen-layer visibility and dynamic properties are not compared.",
        "- ATTRIB, nested INSERT transforms, non-uniform circular/arc transforms, MINSERT expansion and unsupported entities remain unresolved.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portable", required=True, type=Path)
    parser.add_argument("--text-v5", required=True, type=Path)
    parser.add_argument("--symbol-v6", required=True, type=Path)
    parser.add_argument("--primitive-v10", required=True, type=Path)
    parser.add_argument("--visibility-v13", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    parser.add_argument("--output-md", required=True, type=Path)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    args = parser.parse_args()
    if not math.isfinite(args.tolerance) or args.tolerance <= 0:
        parser.error("--tolerance must be finite and positive")
    result = compare_documents(
        load_json(args.portable),
        load_json(args.text_v5),
        load_json(args.symbol_v6),
        load_json(args.primitive_v10),
        load_json(args.visibility_v13),
        args.tolerance,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown_report(result), encoding="utf-8")
    print(f"WROTE {args.output_json}")
    print(f"WROTE {args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
