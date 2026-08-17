#!/usr/bin/env python3
"""Validate fail-closed invariants of a CAD backend route record."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROUTE_ORDER = [
    "ACadSharp",
    "ZWCAD",
    "AutoCAD2023",
    "AutoCAD2020",
    "AutoCAD2018",
    "AutoCAD2014",
]
STATUS_BACKEND = {
    "portable_candidate_selected": "ACadSharp",
    "zwcad_native_fallback_selected": "ZWCAD",
    "autocad_2023_native_fallback_selected": "AutoCAD2023",
    "autocad_2020_native_fallback_selected": "AutoCAD2020",
    "autocad_2018_native_fallback_selected": "AutoCAD2018",
    "autocad_2014_native_fallback_selected": "AutoCAD2014",
}
AUTOCAD_POLICIES = {
    "AutoCAD2023": {"release": "2023", "api": "R24.2", "max_dwg": "AC1032"},
    "AutoCAD2020": {"release": "2020", "api": "R23.1", "max_dwg": "AC1032"},
    "AutoCAD2018": {"release": "2018", "api": "R22.0", "max_dwg": "AC1032"},
    "AutoCAD2014": {"release": "2014", "api": "R19.1", "max_dwg": "AC1027"},
}
TERMINAL_STATUSES = set(STATUS_BACKEND) | {
    "manual_review_required_no_backend",
    "source_hash_changed_safe_stop",
}


def validate_document(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != "cad-backend-route/0.2":
        errors.append("schema_version must be cad-backend-route/0.2")
    if document.get("route_order") != ROUTE_ORDER:
        errors.append(
            "route_order must be ACadSharp, ZWCAD, AutoCAD2023, "
            "AutoCAD2020, AutoCAD2018, AutoCAD2014"
        )
    if document.get("absence_proven") is not False:
        errors.append("absence_proven must remain false")

    source_dwg = document.get("source_dwg_version")
    if not isinstance(source_dwg, str) or re.fullmatch(
        r"(?:AC\d{4}|UNRECOGNIZED)", source_dwg
    ) is None:
        errors.append("source_dwg_version must be an AC code or UNRECOGNIZED")

    before = document.get("source_sha256_before")
    after = document.get("source_sha256_after")
    for name, value in (("source_sha256_before", before), ("source_sha256_after", after)):
        if not isinstance(value, str) or re.fullmatch(r"[0-9A-F]{64}", value) is None:
            errors.append(f"{name} must be an uppercase SHA-256")
    expected_unchanged = isinstance(before, str) and before == after
    if document.get("source_unchanged") is not expected_unchanged:
        errors.append("source_unchanged does not match the two source hashes")

    status = document.get("status")
    if status not in TERMINAL_STATUSES:
        errors.append("status is not recognized")
    selected = document.get("selected_backend")
    selected_output = document.get("selected_output")
    expected_backend = STATUS_BACKEND.get(str(status))
    if expected_backend is None:
        if selected is not None or selected_output is not None:
            errors.append("safe-stop/manual-review status cannot select a backend")
    else:
        if selected != expected_backend:
            errors.append("selected_backend does not match status")
        if not isinstance(selected_output, str) or not selected_output:
            errors.append("selected backend must have a selected_output")

    attempts = document.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        errors.append("attempts must be a non-empty array")
        return errors
    backend_positions: list[int] = []
    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            errors.append(f"attempts[{index}] must be an object")
            continue
        backend = attempt.get("backend")
        if backend not in ROUTE_ORDER:
            errors.append(f"attempts[{index}].backend is not recognized")
            continue
        backend_positions.append(ROUTE_ORDER.index(backend))
        policy = AUTOCAD_POLICIES.get(str(backend))
        if policy is not None:
            if attempt.get("host_release") != policy["release"]:
                errors.append(f"attempts[{index}].host_release does not match backend")
            if attempt.get("host_api_version") != policy["api"]:
                errors.append(f"attempts[{index}].host_api_version does not match backend")
            if attempt.get("max_dwg_version") != policy["max_dwg"]:
                errors.append(f"attempts[{index}].max_dwg_version does not match backend")
            if attempt.get("status") == "success":
                if source_dwg == "UNRECOGNIZED":
                    errors.append("AutoCAD cannot succeed for an unrecognized DWG version")
                elif isinstance(source_dwg, str) and re.fullmatch(
                    r"AC\d{4}", source_dwg
                ) is not None:
                    source_number = int(source_dwg[2:])
                    maximum_number = int(policy["max_dwg"][2:])
                    if source_number > maximum_number:
                        errors.append(
                            f"{backend} cannot succeed for source {source_dwg}"
                        )
        else:
            for field in (
                "host_release",
                "host_api_version",
                "host_file_version",
                "max_dwg_version",
            ):
                if attempt.get(field) is not None:
                    errors.append(f"attempts[{index}].{field} must be null for {backend}")
    if backend_positions and backend_positions != sorted(backend_positions):
        errors.append("attempts do not follow the declared route order")
    if attempts and isinstance(attempts[0], dict) and attempts[0].get("backend") != "ACadSharp":
        errors.append("ACadSharp must be attempted first")
    if expected_backend is not None and not any(
        isinstance(attempt, dict)
        and attempt.get("backend") == expected_backend
        and attempt.get("status") == "success"
        for attempt in attempts
    ):
        errors.append("selected backend has no successful attempt")
    if status == "source_hash_changed_safe_stop" and expected_unchanged:
        errors.append("source_hash_changed_safe_stop requires a changed hash")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("route_record", type=Path)
    args = parser.parse_args()
    document = json.loads(args.route_record.read_text(encoding="utf-8-sig"))
    errors = validate_document(document)
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
