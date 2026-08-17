import copy
import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "validate_cad_backend_route.py"
SPEC = importlib.util.spec_from_file_location("validate_cad_backend_route", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def attempt(backend, status, *, release=None, api=None, file_version=None, maximum=None):
    return {
        "backend": backend,
        "status": status,
        "message": "ok" if status == "success" else status,
        "output": "work/output" if status == "success" else None,
        "reasons": [],
        "host_release": release,
        "host_api_version": api,
        "host_file_version": file_version,
        "max_dwg_version": maximum,
    }


def valid_document():
    return {
        "schema_version": "cad-backend-route/0.2",
        "route_order": [
            "ACadSharp",
            "ZWCAD",
            "AutoCAD2023",
            "AutoCAD2020",
            "AutoCAD2018",
            "AutoCAD2014",
        ],
        "status": "autocad_2020_native_fallback_selected",
        "selected_backend": "AutoCAD2020",
        "selected_output": "work/output",
        "source_name": "sample.dwg",
        "source_dwg_version": "AC1032",
        "source_sha256_before": "A" * 64,
        "source_sha256_after": "A" * 64,
        "source_unchanged": True,
        "portable_fallback_reasons": ["portable_status_unresolved"],
        "absence_proven": False,
        "attempts": [
            attempt("ACadSharp", "fallback_required"),
            attempt("ZWCAD", "not_available"),
            attempt(
                "AutoCAD2023",
                "failed",
                release="2023",
                api="R24.2",
                file_version="R24.2.0",
                maximum="AC1032",
            ),
            attempt(
                "AutoCAD2020",
                "success",
                release="2020",
                api="R23.1",
                file_version="R23.1.0",
                maximum="AC1032",
            ),
        ],
    }


def test_valid_autocad_2020_fallback_route_passes():
    assert MODULE.validate_document(valid_document()) == []


def test_route_order_and_absence_claim_fail_closed():
    document = valid_document()
    document["route_order"] = list(reversed(document["route_order"]))
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
    document["attempts"][2], document["attempts"][3] = (
        document["attempts"][3],
        document["attempts"][2],
    )
    document["source_unchanged"] = False
    errors = MODULE.validate_document(document)
    assert "attempts do not follow the declared route order" in errors
    assert "source_unchanged does not match the two source hashes" in errors


def test_autocad_2014_cannot_claim_success_for_ac1032():
    document = valid_document()
    document["status"] = "autocad_2014_native_fallback_selected"
    document["selected_backend"] = "AutoCAD2014"
    document["attempts"] = document["attempts"][:2] + [
        attempt(
            "AutoCAD2014",
            "success",
            release="2014",
            api="R19.1",
            file_version="R19.1.0",
            maximum="AC1027",
        )
    ]
    errors = MODULE.validate_document(document)
    assert "AutoCAD2014 cannot succeed for source AC1032" in errors


def test_autocad_2014_can_succeed_for_ac1027():
    document = valid_document()
    document["source_dwg_version"] = "AC1027"
    document["status"] = "autocad_2014_native_fallback_selected"
    document["selected_backend"] = "AutoCAD2014"
    document["attempts"] = document["attempts"][:2] + [
        attempt(
            "AutoCAD2014",
            "success",
            release="2014",
            api="R19.1",
            file_version="R19.1.0",
            maximum="AC1027",
        )
    ]
    assert MODULE.validate_document(document) == []


def test_autocad_attempt_metadata_must_match_backend():
    document = valid_document()
    document["attempts"][-1]["host_api_version"] = "R22.0"
    errors = MODULE.validate_document(document)
    assert any("host_api_version" in error for error in errors)
