import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "compare_acadsharp_portable_with_zwcad.py"
SPEC = importlib.util.spec_from_file_location("compare_acadsharp_portable_with_zwcad", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def documents():
    portable = {
        "metadata": {
            "source_sha256": "A" * 64,
            "effective_layer_status": "not_implemented_unverified",
            "layout_viewport_visibility_status": "not_implemented_unverified",
        },
        "evidence_records": [
            {"entity_type": "TEXT", "handle": "1", "text": "A", "position": [1, 2, 0]},
            {"entity_type": "ATTDEF", "handle": "1A", "text": "T", "position": [1, 3, 0], "definition_template_not_placed_value": True},
            {"entity_type": "INSERT", "handle": "2", "block_name": "B", "position": [3, 4, 0], "rotation_radians": 0, "scale": [1, 1, 1]},
            {"entity_type": "LINE", "handle": "3", "start": [0, 0, 0], "end": [1, 1, 0]},
        ],
    }
    text = {"records": [
        {"entity_type": "DBText", "handle": "1", "text": "A", "x": 1, "y": 2, "z": 0},
        {"entity_type": "DBText", "handle": "1A", "text": "T", "x": 1, "y": 3, "z": 0},
    ]}
    symbols = {"records": [{"instance_handle": "2", "block_name": "B", "x": 3, "y": 4, "z": 0, "local_rotation_radians": 0, "scale_x": 1, "scale_y": 1, "scale_z": 1}]}
    primitives = {"records": [{"entity_type": "Line", "handle": "3", "min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1}]}
    visibility = {"records": [{"is_dynamic": True, "dynamic_properties": [{"name": "D"}]}], "layouts": [{}, {}], "viewports": [{}]}
    return portable, text, symbols, primitives, visibility


def test_exact_comparable_fields_match_but_boundary_stays_unresolved():
    result = MODULE.compare_documents(*documents(), 1e-6)
    assert result["formal_backend_equivalent"] is False
    assert result["absence_proven"] is False
    assert result["text_v5"]["TEXT"]["matched"] == 1
    assert result["text_v5"]["TEXT_PLUS_ATTDEF_V5_COMPAT"]["matched"] == 2
    assert result["insert_v6"]["world_position"]["matched"] == 1
    assert result["insert_v6"]["root_local_transform_tuple"]["matched"] == 1
    assert result["insert_v6"]["local_transform_tuple"]["matched"] == 1
    assert result["primitive_v10"]["LINE"]["matched"] == 1
    assert result["visibility_v13"]["status"] == "not_compared_portable_fields_unverified"


def test_coordinate_difference_is_not_counted_as_match():
    portable, text, symbols, primitives, visibility = documents()
    portable["evidence_records"][0]["position"][0] = 1.01
    result = MODULE.compare_documents(portable, text, symbols, primitives, visibility, 1e-6)
    assert result["text_v5"]["TEXT"]["matched"] == 0
    assert result["text_v5"]["TEXT"]["candidate_only"] == 1
    assert result["text_v5"]["TEXT"]["zwcad_only"] == 2
