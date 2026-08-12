#!/usr/bin/env python3
"""Validate conservative routing for an optional multimodal assistant."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ALLOWED_USES = {
    "beam_section_candidate_review",
    "high_risk_roi_review",
    "axis_location_review",
    "proxy_object_review",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--base-model-multimodal", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    data = json.loads(args.config.read_text(encoding="utf-8-sig"))
    errors: list[str] = []
    if data.get("schema_version") != "multimodal-assistant-1.0":
        errors.append("unsupported schema_version")
    enabled = data.get("enabled") is True
    modes = set(data.get("input_modes") or [])
    uses = set(data.get("use_for") or [])
    if enabled and "image" not in modes:
        errors.append("enabled assistant must declare image input")
    if not uses.issubset(ALLOWED_USES):
        errors.append("unsupported use_for value")
    if data.get("formal_confirmation_allowed") is not False:
        errors.append("formal_confirmation_allowed must be false")
    key_env = str(data.get("api_key_env") or "")
    credential_present = bool(key_env and os.environ.get(key_env))
    assistant_ready = bool(
        enabled
        and "image" in modes
        and data.get("provider")
        and data.get("model")
        and key_env
        and credential_present
        and not errors
    )
    if args.base_model_multimodal:
        route = "base_model_multimodal"
    elif assistant_ready:
        route = "configured_multimodal_assistant"
    else:
        route = "text_only_conservative"
    payload = {
        "schema_version": "multimodal-routing-check-1.0",
        "valid": not errors,
        "errors": errors,
        "route": route,
        "assistant_enabled": enabled,
        "assistant_ready": assistant_ready,
        "credential_env_name": key_env,
        "credential_present": credential_present,
        "beam_dimension_warning": (
            "Without a usable multimodal model, downstream beam-section "
            "recognition has lower demonstrated reliability. Keep ambiguous "
            "results as candidates or unresolved and require human review."
        ),
        "formal_confirmation_allowed": False,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
