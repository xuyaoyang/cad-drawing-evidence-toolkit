import copy
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "validate_cad_backend_route.py"
SPEC = importlib.util.spec_from_file_location("validate_cad_backend_route", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def valid_document():
    return {
        "schema_version": "cad-backend-route/0.1",
        "route_order": ["ACadSharp", "ZWCAD", "AutoCAD2023"],
        "status": "autocad_2023_native_fallback_selected",
        "selected_backend": "AutoCAD2023",
        "selected_output": "work/03-autocad-2023/output",
        "source_name": "sample.dwg",
        "source_sha256_before": "A" * 64,
        "source_sha256_after": "A" * 64,
        "source_unchanged": True,
        "portable_fallback_reasons": ["portable_status_unresolved"],
        "absence_proven": False,
        "attempts": [
            {
                "backend": "ACadSharp",
                "status": "fallback_required",
                "message": "unresolved",
                "output": "portable.json",
                "reasons": ["portable_status_unresolved"],
            },
            {
                "backend": "ZWCAD",
                "status": "not_available",
                "message": "not installed",
                "output": None,
                "reasons": ["zwcad_not_available"],
            },
            {
                "backend": "AutoCAD2023",
                "status": "success",
                "message": "ok",
                "output": "work/03-autocad-2023/output",
                "reasons": [],
            },
        ],
    }


def test_valid_autocad_fallback_route_passes():
    assert MODULE.validate_document(valid_document()) == []


def test_route_order_and_absence_claim_fail_closed():
    document = valid_document()
    document["route_order"] = ["AutoCAD2023", "ZWCAD", "ACadSharp"]
    document["absence_proven"] = True
    errors = MODULE.validate_document(document)
    assert any("route_order" in error for error in errors)
    assert "absence_proven must remain false" in errors


def test_selected_backend_requires_matching_successful_attempt():
    document = valid_document()
    document["attempts"][-1]["status"] = "failed"
    errors = MODULE.validate_document(document)
    assert "selected backend has no successful attempt" in errors


def test_attempts_cannot_skip_backwards_and_hash_flag_must_match():
    document = copy.deepcopy(valid_document())
    document["attempts"][1], document["attempts"][2] = (
        document["attempts"][2],
        document["attempts"][1],
    )
    document["source_unchanged"] = False
    errors = MODULE.validate_document(document)
    assert "attempts do not follow the declared route order" in errors
    assert "source_unchanged does not match the two source hashes" in errors
