#!/usr/bin/env python3
"""Validate the fail-closed boundary of ACadSharp portable candidate output."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "acadsharp-portable-evidence/0.1"
ALLOWED_STATUSES = {
    "portable_readonly_candidate_ready_for_comparison",
    "portable_readonly_candidate_unresolved",
}
SHA256_RE = re.compile(r"^[0-9A-F]{64}$")


def validate_document(document: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["root must be a JSON object"]

    metadata = document.get("metadata")
    summary = document.get("summary")
    records = document.get("evidence_records")
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object")
        metadata = {}
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
        summary = {}
    if not isinstance(records, list):
        errors.append("evidence_records must be an array")
        records = []

    if metadata.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"metadata.schema_version must equal {SCHEMA_VERSION}")
    if metadata.get("backend") != "ACadSharp":
        errors.append("metadata.backend must equal ACadSharp")
    if metadata.get("status") not in ALLOWED_STATUSES:
        errors.append("metadata.status is not an allowed candidate state")
    if metadata.get("formal_backend_equivalent") is not False:
        errors.append("formal_backend_equivalent must remain false")
    if metadata.get("absence_proven") is not False:
        errors.append("absence_proven must remain false")
    if metadata.get("original_dwg_opened_by_parser") is not False:
        errors.append("original_dwg_opened_by_parser must remain false")
    if metadata.get("analysis_copy_only") is not True:
        errors.append("analysis_copy_only must be true")
    if metadata.get("coordinate_evidence_status") != "candidate_requires_field_comparison":
        errors.append("coordinate evidence must remain a field-comparison candidate")
    if metadata.get("attribute_coordinate_status") != "parser_value_not_backend_equivalent":
        errors.append("attribute coordinates must remain explicitly non-equivalent")
    if metadata.get("effective_layer_status") != "not_implemented_unverified":
        errors.append("effective layer inheritance must remain explicitly unverified")
    if metadata.get("layout_viewport_visibility_status") != "not_implemented_unverified":
        errors.append("layout viewport visibility must remain explicitly unverified")

    source_hash = metadata.get("source_sha256")
    if not isinstance(source_hash, str) or not SHA256_RE.fullmatch(source_hash):
        errors.append("metadata.source_sha256 must be uppercase hexadecimal SHA-256")

    if summary.get("evidence_record_count") != len(records):
        errors.append("summary.evidence_record_count does not match evidence_records length")

    seen_keys: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"evidence_records[{index}] must be an object")
            continue
        for field in ("entity_type", "handle", "instance_key", "root_space", "block_path", "layer"):
            if not isinstance(record.get(field), str):
                errors.append(f"evidence_records[{index}].{field} must be a string")
        instance_key = record.get("instance_key")
        if isinstance(instance_key, str):
            if instance_key in seen_keys:
                errors.append(f"duplicate instance_key: {instance_key}")
            seen_keys.add(instance_key)
        _validate_finite(record, index, errors)

    return errors


def _validate_finite(record: dict[str, Any], index: int, errors: list[str]) -> None:
    def walk(value: Any, path: str) -> None:
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                errors.append(f"evidence_records[{index}].{path} contains a non-finite number")
            return
        if isinstance(value, list):
            for child_index, child in enumerate(value):
                walk(child, f"{path}[{child_index}]")
            return
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}")

    for key, value in record.items():
        walk(value, key)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    with args.input.open("r", encoding="utf-8-sig") as handle:
        document = json.load(handle)
    errors = validate_document(document)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        "VALID "
        f"status={document['metadata']['status']} "
        f"records={len(document['evidence_records'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
