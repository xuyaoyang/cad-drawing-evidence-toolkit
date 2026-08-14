from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
MODULE_PATH = SCRIPTS / "export_d4_minimal_clearance_anchorage.py"
SPEC = importlib.util.spec_from_file_location("export_d4_minimal_clearance_anchorage", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _q(value: float | None, status: str = "已确认", reason: str = "fixture") -> dict:
    return {"value_mm": value, "status": status, "reason": reason, "source_stage": "fixture"}


def _device(device_id: str = "D-001", *, direct: bool = True) -> dict:
    lower_id = "SEG-LOWER" if direct else None
    upper_id = "SEG-UPPER" if direct else None
    return {
        "registry_device_id": device_id,
        "device_type": "wall_panel_damper",
        "building_id": "B1",
        "floor": "1F",
        "component_id": "A",
        "axis_position": "B轴交17/18轴",
        "orientation": "horizontal",
        "beam_references": {"lower_beam_id": lower_id, "upper_beam_id": upper_id, "status": "已确认" if direct else "资料不足"},
        "support_links": {
            "lower": {
                "support_floor": "1F", "component_id": "A", "actual_insert_root": "R1", "orientation": "horizontal",
                "mapped_envelope": [100, 0, 900, 200], "mapping_status": "已确认",
                "p5_beam_segment_id": lower_id, "p5_physical_status": "已确认" if direct else "候选",
                "beam_reference_status": "已确认" if direct else "资料不足", "dependency_ids": ["E-L"],
            },
            "upper": {
                "support_floor": "2F", "component_id": "A", "actual_insert_root": "R2", "orientation": "horizontal",
                "mapped_envelope": [100, 0, 900, 200], "mapping_status": "已确认",
                "p5_beam_segment_id": upper_id, "p5_physical_status": "已确认" if direct else "候选",
                "beam_reference_status": "已确认" if direct else "资料不足", "dependency_ids": ["E-U"],
            },
        },
        "inputs": {
            "z_lower_level_mm": _q(0),
            "z_upper_level_mm": _q(4500),
            "lower_installation_surface_offset_mm": _q(-100, reason="confirmed_lower_beam_top"),
            "upper_beam_top_offset_mm": _q(-50),
            "upper_beam_height_mm": {**_q(800), "beam_number": "A-KL1(3)"},
        },
        "clear_height_mm": 3650,
    }


def _docs(device: dict) -> tuple[dict, dict]:
    state = {"schema_version": "D4-unified-formal-support-beam-integrated-clearance-state-1.0", "device_current_state": [device]}
    ledger = {"schema_version": "D4-unified-formal-support-beam-ledger-1.0", "beam_records": []}
    return state, ledger


def _snapshot(role: str) -> dict:
    return {"role": role, "path": role, "sha256": "A" * 64, "bytes": 1}


def test_direct_physical_beam_links_confirm_clearance_and_anchorage() -> None:
    state, ledger = _docs(_device())
    report = module.build_report(state, ledger, state_snapshot=_snapshot("state"), ledger_snapshot=_snapshot("ledger"), expected_count=1)
    row = report["devices"][0]
    assert row["lower_beam_top_elevation_mm"] == -100
    assert row["upper_beam_top_elevation_mm"] == 4450
    assert row["beam_clearance_excluding_upturn_mm"] == 3750
    assert row["lower_drop_status"] == "有"
    assert row["lower_drop_height_mm"] == 100
    assert row["upper_drop_status"] == "有"
    assert row["upper_drop_height_mm"] == 50
    assert row["lower_anchorage"]["status"] == "已确认"
    assert row["upper_anchorage"]["conclusion"] == "已生根"
    assert row["overall_status"] == "已确认"
    assert report["stats"]["baseline_clearance_mismatch_count"] == 1


def test_candidate_physical_links_are_not_promoted() -> None:
    device = _device(direct=False)
    state, ledger = _docs(device)
    report = module.build_report(state, ledger, state_snapshot=_snapshot("state"), ledger_snapshot=_snapshot("ledger"), expected_count=1)
    row = report["devices"][0]
    assert row["clearance_status"] == "已确认"
    assert row["lower_anchorage"]["status"] == "候选"
    assert row["upper_anchorage"]["status"] == "候选"
    assert row["overall_status"] == "待人工核实"


def test_formal_beam_supports_but_does_not_promote_without_confirmed_physical_segment() -> None:
    device = _device(direct=False)
    device["formal_shared_beam_references"] = {
        "lower": {"status": "已确认", "formal_beam_id": "FB-L", "beam_number": "A-KL1", "height_mm": 700},
        "upper": {"status": "已确认", "formal_beam_id": "FB-U", "beam_number": "A-KL2", "height_mm": 800},
    }
    beams = []
    for beam_id, floor, root, role in (("FB-L", "1F", "R1", "lower"), ("FB-U", "2F", "R2", "upper")):
        beams.append(
            {
                "formal_beam_id": beam_id,
                "identity": {
                    "building_id": "B1", "support_floor": floor, "component_id": "A", "actual_insert_root": root,
                    "orientation": "horizontal", "face_coordinates_mm": [0, 400],
                },
                "consumer_supports": [{"registry_device_id": "D-001", "support_role": role}],
                "target_along_intervals_mm": [[0, 1000]],
            }
        )
    state = {"schema_version": "D4-unified-formal-support-beam-integrated-clearance-state-1.0", "device_current_state": [device]}
    ledger = {"schema_version": "D4-unified-formal-support-beam-ledger-1.0", "beam_records": beams}
    report = module.build_report(state, ledger, state_snapshot=_snapshot("state"), ledger_snapshot=_snapshot("ledger"), expected_count=1)
    row = report["devices"][0]
    assert row["lower_anchorage"]["status"] == "候选"
    assert row["upper_anchorage"]["status"] == "候选"
    assert "缺少已确认物理梁段" in row["upper_anchorage"]["reason"]


def test_missing_beam_top_evidence_stays_blank_instead_of_zero() -> None:
    device = _device()
    device["inputs"]["lower_installation_surface_offset_mm"] = _q(0, reason="installation_surface_only")
    state, ledger = _docs(device)
    report = module.build_report(state, ledger, state_snapshot=_snapshot("state"), ledger_snapshot=_snapshot("ledger"), expected_count=1)
    row = report["devices"][0]
    assert row["lower_beam_top_elevation_mm"] is None
    assert row["beam_clearance_excluding_upturn_mm"] is None
    assert row["lower_drop_status"] == "资料不足"
    assert "下梁顶标高" in row["unresolved_reasons"]


def test_duplicate_devices_and_expected_count_fail_closed() -> None:
    device = _device()
    state, ledger = _docs(device)
    state["device_current_state"].append(device)
    with pytest.raises(module.SafetyStop):
        module.build_report(state, ledger, state_snapshot=_snapshot("state"), ledger_snapshot=_snapshot("ledger"), expected_count=2)
    state["device_current_state"] = [device]
    with pytest.raises(module.SafetyStop):
        module.build_report(state, ledger, state_snapshot=_snapshot("state"), ledger_snapshot=_snapshot("ledger"), expected_count=114)


def test_schema_and_csv() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    state, ledger = _docs(_device())
    report = module.build_report(state, ledger, state_snapshot=_snapshot("state"), ledger_snapshot=_snapshot("ledger"), expected_count=1)
    schema = json.loads((Path(__file__).parents[1] / "schemas" / "d4-minimal-clearance-anchorage-report.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.Draft202012Validator(schema).validate(report)
    text = module.render_csv(report)
    assert text.count("\n") == 2
    assert "梁间净空-未计上翻墩(mm)" in text
