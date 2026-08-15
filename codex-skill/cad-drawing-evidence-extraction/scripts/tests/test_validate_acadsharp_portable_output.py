import copy
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "validate_acadsharp_portable_output.py"
SPEC = importlib.util.spec_from_file_location("validate_acadsharp_portable_output", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def valid_document():
    return {
        "metadata": {
            "schema_version": "acadsharp-portable-evidence/0.1",
            "backend": "ACadSharp",
            "backend_version": "3.6.51",
            "source_name": "sample.dwg",
            "source_sha256": "A" * 64,
            "status": "portable_readonly_candidate_unresolved",
            "formal_backend_equivalent": False,
            "absence_proven": False,
            "original_dwg_opened_by_parser": False,
            "analysis_copy_only": True,
            "coordinate_evidence_status": "candidate_requires_field_comparison",
            "attribute_coordinate_status": "parser_value_not_backend_equivalent",
            "effective_layer_status": "not_implemented_unverified",
            "layout_viewport_visibility_status": "not_implemented_unverified",
        },
        "summary": {"evidence_record_count": 1},
        "notifications": [],
        "traversal_issues": [],
        "limitations": [],
        "evidence_records": [
            {
                "entity_type": "TEXT",
                "handle": "1A",
                "instance_key": "*Model_Space||1A",
                "root_space": "*Model_Space",
                "owner_block": "*Model_Space",
                "block_path": "",
                "layer": "0",
                "position": [1.0, 2.0, 0.0],
            }
        ],
    }


def test_valid_candidate_document_passes():
    assert MODULE.validate_document(valid_document()) == []


def test_formal_or_absence_claims_fail_closed():
    document = valid_document()
    document["metadata"]["formal_backend_equivalent"] = True
    document["metadata"]["absence_proven"] = True
    errors = MODULE.validate_document(document)
    assert "formal_backend_equivalent must remain false" in errors
    assert "absence_proven must remain false" in errors


def test_duplicate_instance_keys_and_count_mismatch_are_rejected():
    document = valid_document()
    document["evidence_records"].append(copy.deepcopy(document["evidence_records"][0]))
    errors = MODULE.validate_document(document)
    assert "summary.evidence_record_count does not match evidence_records length" in errors
    assert any(error.startswith("duplicate instance_key:") for error in errors)


def test_non_finite_coordinates_are_rejected():
    document = valid_document()
    document["evidence_records"][0]["position"][0] = float("nan")
    errors = MODULE.validate_document(document)
    assert any("non-finite number" in error for error in errors)
