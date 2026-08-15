import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "compare_native_backend_exports.py"
SPEC = importlib.util.spec_from_file_location("compare_native_backend_exports", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_exports(directory: Path, stem: str, *, text="A", off_number=5, paper_center=10.0):
    documents = {
        "cad_text_export_v5": {"records": [{"text": text, "handle": "1"}]},
        "cad_frame_export_v5": {"line_segments": [], "bounds_candidates": []},
        "cad_symbol_export_v6": {"records": [], "bounds_unavailable_count": 0},
        "cad_oriented_text_export_v7": {"records": [], "bounds_unavailable_count": 0},
        "cad_primitive_export_v10": {"records": []},
        "cad_visibility_export_v13": {
            "records": [],
            "layers": [],
            "layouts": [],
            "viewports": [
                {
                    "handle": "PAPER",
                    "number": 1,
                    "is_paper_viewport": True,
                    "view_center_x": paper_center,
                },
                {
                    "handle": "MODEL",
                    "number": 2,
                    "is_paper_viewport": False,
                    "on": True,
                    "view_center_x": 100.0,
                },
                {
                    "handle": "OFF",
                    "number": off_number,
                    "is_paper_viewport": False,
                    "on": False,
                    "view_center_x": 200.0,
                },
            ],
        },
    }
    directory.mkdir(parents=True)
    for suffix, document in documents.items():
        (directory / f"{stem}.{suffix}.json").write_text(
            json.dumps(document), encoding="utf-8"
        )


def test_paper_camera_and_runtime_number_are_diagnostic_only(tmp_path):
    stem = "sample"
    zwcad = tmp_path / "zwcad"
    autocad = tmp_path / "autocad"
    write_exports(zwcad, stem, off_number=5, paper_center=10.0)
    write_exports(autocad, stem, off_number=-1, paper_center=999.0)
    result = MODULE.compare_exports(zwcad, autocad, stem, 1e-6)
    assert result["status"] == "native_core_fields_consistent"
    assert result["comparisons"]["visibility_viewports"]["matched"] == 2
    diagnostic = result["host_extent_diagnostics"]["viewport_number_diagnostics"]
    assert diagnostic["field_mismatch"] == 1


def test_evidence_core_mismatch_remains_unresolved(tmp_path):
    stem = "sample"
    zwcad = tmp_path / "zwcad"
    autocad = tmp_path / "autocad"
    write_exports(zwcad, stem, text="A")
    write_exports(autocad, stem, text="B")
    result = MODULE.compare_exports(zwcad, autocad, stem, 1e-6)
    assert result["status"] == "native_core_field_comparison_unresolved"
    assert not result["comparisons"]["text_core"]["consistent"]
    assert result["backend_equivalent"] is False
    assert result["absence_proven"] is False
