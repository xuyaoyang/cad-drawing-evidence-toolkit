import json
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "validate_multimodal_assistant_config.py"
)


def run_config(tmp_path: Path, payload: dict, env: dict | None = None):
    config = tmp_path / "config.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return result, json.loads(result.stdout)


def base_payload() -> dict:
    return {
        "schema_version": "multimodal-assistant-1.0",
        "enabled": False,
        "provider": "openai-compatible",
        "model": "vision-model",
        "api_key_env": "CAD_TEST_VISION_KEY",
        "input_modes": ["text", "image"],
        "use_for": ["high_risk_roi_review"],
        "formal_confirmation_allowed": False,
    }


def test_disabled_config_routes_to_conservative_text(tmp_path):
    result, payload = run_config(tmp_path, base_payload())
    assert result.returncode == 0
    assert payload["route"] == "text_only_conservative"
    assert payload["formal_confirmation_allowed"] is False


def test_enabled_ready_assistant_uses_environment_secret(tmp_path):
    config = base_payload()
    config["enabled"] = True
    env = dict(os.environ)
    env["CAD_TEST_VISION_KEY"] = "not-written-to-output"
    result, payload = run_config(tmp_path, config, env)
    assert result.returncode == 0
    assert payload["route"] == "configured_multimodal_assistant"
    assert payload["credential_present"] is True
    assert "not-written-to-output" not in result.stdout


def test_formal_multimodal_confirmation_is_rejected(tmp_path):
    config = base_payload()
    config["formal_confirmation_allowed"] = True
    result, payload = run_config(tmp_path, config)
    assert result.returncode == 2
    assert payload["valid"] is False
