import importlib.util
import csv
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "组织跨DWG阻尼器证据组V19.py"
)
SPEC = importlib.util.spec_from_file_location("v19", MODULE_PATH)
V19 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = V19
SPEC.loader.exec_module(V19)
V12_PATH = Path(__file__).resolve().parents[1] / "跨视图阻尼器物理设备归一.py"
V12_SPEC = importlib.util.spec_from_file_location("v12", V12_PATH)
V12 = importlib.util.module_from_spec(V12_SPEC)
assert V12_SPEC.loader is not None
sys.modules[V12_SPEC.name] = V12
V12_SPEC.loader.exec_module(V12)


class ScopeExtractionTests(unittest.TestCase):
    def test_composite_buildings_are_not_split(self):
        self.assertEqual(
            V19.extract_buildings("03-1#3-2#3-5#3-6#楼"),
            ("3-1", "3-2", "3-5", "3-6"),
        )

    def test_simple_building(self):
        self.assertEqual(V19.extract_buildings("1#、2#楼"), ("1", "2"))

    def test_floor_range(self):
        self.assertEqual(V19.extract_floors("阻尼器-1F~2F"), ("1F~2F",))

    def test_equal_floor_range_is_single_floor(self):
        self.assertEqual(V19.extract_floors("阻尼器-1F~1F"), ("1F",))

    def test_chinese_floor_and_roof(self):
        self.assertEqual(
            V19.extract_floors("三层及屋面"),
            ("3F", "ROOF"),
        )

    def test_file_role_prefers_specific_views(self):
        self.assertEqual(V19.infer_file_role("某楼梁平法施工图"), "beam_plan")
        self.assertEqual(
            V19.infer_file_role("某楼空心楼盖布置图"), "slab_plan"
        )
        self.assertEqual(
            V19.infer_file_role("某楼结构平面图"), "structural_plan"
        )

    def test_filename_buildings_take_precedence_over_row_noise(self):
        self.assertEqual(
            V19.choose_buildings(("3-7",), ("3-8", "300")),
            ("3-7",),
        )

    def test_plain_axis_labels_are_safe_for_one_numeric_building(self):
        artifact = V19.DrawingArtifacts(
            source_path=Path("1#楼结构平面图.dwg"),
            copied_stem="D0001",
            route_status="selected",
            content_decision="already_primary",
            analysis_dir=Path("."),
            candidate_csv=Path("candidate.csv"),
            frame_texts_csv=Path("frame.csv"),
            visibility_json=None,
            shared_layout_csv=None,
            rows=[],
            frame_texts=[
                {
                    "frame_id": "FRAME-001",
                    "text": label,
                    "x": str(index * 10),
                    "y": str(index * 10),
                }
                for index, label in enumerate(("1", "2", "3", "A", "B"))
            ],
        )
        unit = V19.EvidenceUnit(
            artifact=artifact,
            frame_id="FRAME-001",
            role="structural_plan",
            buildings=("1",),
            floors=("1F",),
        )
        evidence = V19.axis_evidence(unit)
        self.assertTrue(evidence["ready"])
        self.assertEqual(
            evidence["mode"],
            "plain_single_building_normalized",
        )
        normalized = V19.normalized_frame_rows(unit)
        self.assertEqual(
            {row["text"] for row in normalized},
            {"1-1", "1-2", "1-3", "1-A", "1-B"},
        )

    def test_plain_axis_labels_are_not_assigned_to_multiple_buildings(self):
        artifact = V19.DrawingArtifacts(
            source_path=Path("1#2#楼结构平面图.dwg"),
            copied_stem="D0001",
            route_status="selected",
            content_decision="already_primary",
            analysis_dir=Path("."),
            candidate_csv=Path("candidate.csv"),
            frame_texts_csv=Path("frame.csv"),
            visibility_json=None,
            shared_layout_csv=None,
            rows=[],
            frame_texts=[
                {"frame_id": "FRAME-001", "text": label}
                for label in ("1", "2", "3", "A", "B")
            ],
        )
        unit = V19.EvidenceUnit(
            artifact=artifact,
            frame_id="FRAME-001",
            role="structural_plan",
            buildings=("1", "2"),
            floors=("1F",),
        )
        self.assertFalse(V19.axis_evidence(unit)["ready"])

    def test_namespace_suffix_axes_normalize_to_compound_building(self):
        artifact = V19.DrawingArtifacts(
            source_path=Path("3-7#楼结构平面图.dwg"),
            copied_stem="D0001",
            route_status="selected",
            content_decision="already_primary",
            analysis_dir=Path("."),
            candidate_csv=Path("candidate.csv"),
            frame_texts_csv=Path("frame.csv"),
            visibility_json=None,
            shared_layout_csv=None,
            rows=[],
            frame_texts=[
                {"frame_id": "FRAME-001", "text": f"02a-2-{label}"}
                for label in ("1", "2", "3", "A", "B")
            ],
        )
        unit = V19.EvidenceUnit(
            artifact=artifact,
            frame_id="FRAME-001",
            role="structural_plan",
            buildings=("3-7",),
            floors=("1F",),
        )
        evidence = V19.axis_evidence(unit)
        self.assertTrue(evidence["ready"])
        self.assertEqual(evidence["mode"], "namespace_suffix_single_scope")
        self.assertEqual(evidence["namespace"], "02a-2-")
        normalized = V19.normalized_frame_rows(unit)
        self.assertEqual(
            {row["text"] for row in normalized},
            {"3-7-1", "3-7-2", "3-7-3", "3-7-A", "3-7-B"},
        )

    def test_namespace_suffix_axes_are_not_assigned_to_multiple_buildings(self):
        artifact = V19.DrawingArtifacts(
            source_path=Path("3-1#3-2#楼结构平面图.dwg"),
            copied_stem="D0001",
            route_status="selected",
            content_decision="already_primary",
            analysis_dir=Path("."),
            candidate_csv=Path("candidate.csv"),
            frame_texts_csv=Path("frame.csv"),
            visibility_json=None,
            shared_layout_csv=None,
            rows=[],
            frame_texts=[
                {"frame_id": "FRAME-001", "text": f"02a-2-{label}"}
                for label in ("1", "2", "3", "A", "B")
            ],
        )
        unit = V19.EvidenceUnit(
            artifact=artifact,
            frame_id="FRAME-001",
            role="structural_plan",
            buildings=("3-1", "3-2"),
            floors=("1F",),
        )
        self.assertFalse(V19.axis_evidence(unit)["ready"])

    def test_v12_accepts_compound_building_axis_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "axes.csv"
            rows = [
                {
                    "frame_id": "FRAME-001",
                    "text": f"3-7-{label}",
                    "x": str(index * 10 if label.isdigit() else 0),
                    "y": str(index * 10 if label.isalpha() else 0),
                }
                for index, label in enumerate(("1", "2", "3", "A", "B"))
            ]
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            systems = V12.build_axis_systems(path)
        self.assertIn("3-7", systems["FRAME-001"])
        self.assertEqual(
            set(systems["FRAME-001"]["3-7"]["x"]),
            {"1", "2", "3"},
        )
        self.assertEqual(
            set(systems["FRAME-001"]["3-7"]["y"]),
            {"A", "B"},
        )
        self.assertEqual(
            V12.extract_building_ids("3-7#楼阻尼器"),
            ["3-7"],
        )

    def test_v12_expands_explicit_floor_ranges(self):
        floors, unresolved = V12.expand_floor_tokens(
            ["1F~3F", "ROOF", "B2F~B1F"]
        )
        self.assertEqual(
            floors,
            ["1F", "2F", "3F", "ROOF", "B2F", "B1F"],
        )
        self.assertEqual(unresolved, [])

    def test_v12_does_not_assign_multi_building_scope_without_evidence(self):
        mapping, source, issues = V12.resolve_floors_by_building(
            [],
            ["1", "2"],
            ["1F~2F"],
        )
        self.assertEqual(mapping, {})
        self.assertEqual(source, "unresolved")
        self.assertIn("scope_floor_assignment_unresolved", issues)

    def test_v12_allows_multi_axis_mapping_only_with_shared_evidence(self):
        rows = [
            {"raw_instance_key": "I1", "building_id": building}
            for building in ("1", "2")
        ]
        self.assertEqual(
            V12.ambiguous_primary_mapping_keys(rows, set()),
            {"I1"},
        )
        self.assertEqual(
            V12.ambiguous_primary_mapping_keys(rows, {"1", "2"}),
            set(),
        )


class GeometryAxisLocationTests(unittest.TestCase):
    def test_rotated_parallel_axis_family_has_known_fraction(self):
        angle = 0.6
        direction = (math.cos(angle), math.sin(angle))
        normal = (-direction[1], direction[0])
        observations = {}
        for index, label in enumerate(("1", "2", "3")):
            offset = index * 1000.0
            observations[label] = [
                (
                    normal[0] * offset - direction[0] * 5000.0,
                    normal[1] * offset - direction[1] * 5000.0,
                ),
                (
                    normal[0] * offset + direction[0] * 5000.0,
                    normal[1] * offset + direction[1] * 5000.0,
                ),
            ]
        axes = V12.build_line_axes(observations)
        point = (normal[0] * 250.0, normal[1] * 250.0)
        location = V12.locate_between_line_axes(point[0], point[1], axes)
        self.assertIsNotNone(location)
        self.assertEqual((location.low, location.high), ("1", "2"))
        self.assertAlmostEqual(location.fraction, 0.25, places=6)

    @staticmethod
    def curved_fixture(include_extensions=True):
        center_y = 10000.0
        observations = {}
        records = []
        for label, radius in (("A", 10000.0), ("B", 8000.0), ("C", 6000.0)):
            start = (0.0, center_y - radius)
            mid = (
                radius / math.sqrt(2.0),
                center_y - radius / math.sqrt(2.0),
            )
            end = (radius, center_y)
            observations[label] = [(radius + 500.0, center_y)]
            records.append(
                {
                    "record_key": f"ARC-{label}",
                    "entity_type": "Arc",
                    "curve_geometry_valid": True,
                    "layer": "S-AXIS",
                    "curve_center_x": 0.0,
                    "curve_center_y": center_y,
                    "curve_radius": radius,
                    "start_x": start[0],
                    "start_y": start[1],
                    "curve_mid_x": mid[0],
                    "curve_mid_y": mid[1],
                    "end_x": end[0],
                    "end_y": end[1],
                    "min_x": 0.0,
                    "min_y": center_y - radius,
                    "max_x": radius,
                    "max_y": center_y,
                }
            )
            if include_extensions:
                records.append(
                    {
                        "record_key": f"LINE-{label}",
                        "entity_type": "Line",
                        "endpoints_valid": True,
                        "layer": "S-AXIS",
                        "start_x": start[0],
                        "start_y": start[1],
                        "end_x": -20000.0,
                        "end_y": start[1],
                    }
                )
        return observations, records

    def test_curved_axis_family_locates_straight_arc_and_outer_band(self):
        observations, records = self.curved_fixture()
        family = V12.build_curved_axis_family(observations, records)
        self.assertIsNotNone(family)

        straight = V12.locate_between_curved_axes(-5000.0, 1000.0, family)
        self.assertEqual((straight.low, straight.high), ("A", "B"))
        self.assertAlmostEqual(straight.fraction, 0.5, places=6)

        radius = 7000.0
        curved = V12.locate_between_curved_axes(
            radius / math.sqrt(2.0),
            10000.0 - radius / math.sqrt(2.0),
            family,
        )
        self.assertEqual((curved.low, curved.high), ("B", "C"))
        self.assertAlmostEqual(curved.fraction, 0.5, places=6)

        outside = V12.locate_between_curved_axes(-5000.0, 4500.0, family)
        self.assertEqual((outside.low, outside.high), ("C", "OUT"))
        self.assertAlmostEqual(outside.fraction, 0.25, places=6)

    def test_curved_axes_without_tangent_extensions_stop_safely(self):
        observations, records = self.curved_fixture(include_extensions=False)
        self.assertIsNone(V12.build_curved_axis_family(observations, records))


class EndToEndGroupingTests(unittest.TestCase):
    @staticmethod
    def write_csv(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_group_invokes_v12_and_closes_cross_view_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            v16 = root / "v16"
            primary_path = root / "1#楼结构平面图.dwg"
            cross_path = root / "1#楼梁平法施工图.dwg"
            manifest_rows = [
                {
                    "source_path": str(primary_path),
                    "copied_stem": "D0001_primary",
                },
                {
                    "source_path": str(cross_path),
                    "copied_stem": "D0002_cross",
                },
            ]
            self.write_csv(v16 / "input_manifest.csv", manifest_rows)
            v18_rows = [
                {
                    "source_path": str(primary_path),
                    "route_status": "selected",
                    "content_scan_decision": "already_primary",
                },
                {
                    "source_path": str(cross_path),
                    "route_status": "selected",
                    "content_scan_decision": "already_primary",
                },
            ]
            v18_report = root / "V18.csv"
            self.write_csv(v18_report, v18_rows)

            candidate_fields = {
                "decision": "",
                "semantic_leaf_symbol": "True",
                "instance_key": "",
                "frame_id": "FRAME-001",
                "x": "",
                "y": "5",
                "semantic_parent_key": "",
                "parent_instance_key": "",
                "frame_role": "",
                "semantic_parent_block": "1#楼阻尼器-1F",
                "block_name": "VFD",
                "effective_name": "VFD",
                "name_path": "VFD",
                "semantic_preview": "阻尼器",
                "frame_role_evidence": "",
                "layer": "DAM",
                "geometry_signature": "",
                "reasons": "",
            }
            for stem, role, decision in (
                ("D0001_primary", "structural_plan", "counted"),
                ("D0002_cross", "beam_plan", "manual_review"),
            ):
                rows = []
                for index, x in enumerate((5, 15), 1):
                    row = dict(candidate_fields)
                    row.update(
                        {
                            "decision": decision,
                            "instance_key": f"{stem}-I{index}",
                            "x": str(x),
                            "frame_role": role,
                        }
                    )
                    rows.append(row)
                analysis = v16 / "analysis" / stem
                self.write_csv(analysis / f"{stem}.阻尼器实例候选.csv", rows)
                axes = [
                    {"frame_id": "FRAME-001", "text": "1-1", "x": "0", "y": "0"},
                    {"frame_id": "FRAME-001", "text": "1-2", "x": "10", "y": "0"},
                    {"frame_id": "FRAME-001", "text": "1-3", "x": "20", "y": "0"},
                    {"frame_id": "FRAME-001", "text": "1-A", "x": "0", "y": "0"},
                    {"frame_id": "FRAME-001", "text": "1-B", "x": "0", "y": "10"},
                ]
                self.write_csv(
                    analysis / f"{stem}.文字按图框归属清单.csv", axes
                )
                visibility = {
                    "viewport_record_count": 0,
                    "records": [
                        {
                            "instance_key": f"{stem}-I{index}",
                            "effective_visible_database": True,
                            "visibility_reason": "visible",
                            "effective_layer": "DAM",
                            "entity_visible": True,
                        }
                        for index in (1, 2)
                    ],
                }
                (analysis / f"{stem}.cad_visibility_export_v13.json").write_text(
                    json.dumps(visibility, ensure_ascii=False),
                    encoding="utf-8",
                )

            output = root / "v19"
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--v18-report",
                    str(v18_report),
                    "--v16-root",
                    str(v16),
                    "--v12-script",
                    str(
                        MODULE_PATH.parent
                        / "跨视图阻尼器物理设备归一.py"
                    ),
                    "--output-dir",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            rows = V19.read_csv(output / "V19跨DWG证据组.csv")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "cross_view_quantity_closed")
            self.assertEqual(rows[0]["physical_template_count"], "2")
            self.assertEqual(rows[0]["unresolved_occurrence_count"], "0")

    def test_single_primary_generates_complete_v21_location_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            v16 = root / "v16"
            primary_path = root / "1#楼结构平面图.dwg"
            self.write_csv(
                v16 / "input_manifest.csv",
                [
                    {
                        "source_path": str(primary_path),
                        "copied_stem": "D0001_primary",
                    }
                ],
            )
            v18_report = root / "V18.csv"
            self.write_csv(
                v18_report,
                [
                    {
                        "source_path": str(primary_path),
                        "route_status": "selected",
                        "content_scan_decision": "already_primary",
                    }
                ],
            )
            analysis = v16 / "analysis" / "D0001_primary"
            candidates = []
            for index, x in enumerate((5, 15), 1):
                candidates.append(
                    {
                        "decision": "counted",
                        "semantic_leaf_symbol": "True",
                        "instance_key": f"PRIMARY-I{index}",
                        "frame_id": "FRAME-001",
                        "x": str(x),
                        "y": "5",
                        "semantic_parent_key": "",
                        "parent_instance_key": "",
                        "frame_role": "structural_plan",
                        "semantic_parent_block": "1#楼阻尼器-1F~2F",
                        "block_name": "VFD",
                        "effective_name": "VFD",
                        "name_path": "VFD",
                        "semantic_preview": "阻尼器",
                        "frame_role_evidence": "",
                        "layer": "DAM",
                        "geometry_signature": "",
                        "reasons": "",
                    }
                )
            self.write_csv(
                analysis / "D0001_primary.阻尼器实例候选.csv",
                candidates,
            )
            axes = [
                {"frame_id": "FRAME-001", "text": "1-1", "x": "0", "y": "0"},
                {"frame_id": "FRAME-001", "text": "1-2", "x": "10", "y": "0"},
                {"frame_id": "FRAME-001", "text": "1-3", "x": "20", "y": "0"},
                {"frame_id": "FRAME-001", "text": "1-A", "x": "0", "y": "0"},
                {"frame_id": "FRAME-001", "text": "1-B", "x": "0", "y": "10"},
            ]
            self.write_csv(
                analysis / "D0001_primary.文字按图框归属清单.csv",
                axes,
            )
            visibility = {
                "viewport_record_count": 0,
                "records": [
                    {
                        "instance_key": f"PRIMARY-I{index}",
                        "effective_visible_database": True,
                        "visibility_reason": "visible",
                        "effective_layer": "DAM",
                        "entity_visible": True,
                    }
                    for index in (1, 2)
                ],
            }
            (
                analysis / "D0001_primary.cad_visibility_export_v13.json"
            ).write_text(
                json.dumps(visibility, ensure_ascii=False),
                encoding="utf-8",
            )

            output = root / "v19"
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "--v18-report",
                    str(v18_report),
                    "--v16-root",
                    str(v16),
                    "--v12-script",
                    str(V12_PATH),
                    "--output-dir",
                    str(output),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            groups = V19.read_csv(output / "V19跨DWG证据组.csv")
            self.assertEqual(
                groups[0]["status"],
                "single_primary_device_location_complete",
            )
            registry = V19.read_csv(output / "V21阻尼器定位总表.csv")
            self.assertEqual(len(registry), 4)
            self.assertEqual(
                {row["floor"] for row in registry},
                {"1F", "2F"},
            )
            for row in registry:
                for field in V19.LOCATION_REQUIRED_FIELDS:
                    self.assertTrue(row[field], field)
            payload = json.loads(
                (output / "V21阻尼器定位总表.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                payload["status"],
                "device_location_registry_complete",
            )
            self.assertEqual(payload["missing_location_row_count"], 0)
            self.assertEqual(payload["duplicate_registry_id_count"], 0)

    def test_v12_blocks_primary_instance_mapped_to_two_axis_systems(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = root / "candidate.csv"
            self.write_csv(
                candidate,
                [
                    {
                        "decision": "counted",
                        "semantic_leaf_symbol": "True",
                        "instance_key": "AMBIGUOUS-I1",
                        "frame_id": "FRAME-001",
                        "x": "5",
                        "y": "5",
                        "semantic_parent_key": "",
                        "geometry_signature": "",
                        "reasons": "",
                    }
                ],
            )
            axes = []
            for building in ("1", "2"):
                axes.extend(
                    [
                        {
                            "frame_id": "FRAME-001",
                            "text": f"{building}-1",
                            "x": "0",
                            "y": "0",
                        },
                        {
                            "frame_id": "FRAME-001",
                            "text": f"{building}-2",
                            "x": "10",
                            "y": "0",
                        },
                        {
                            "frame_id": "FRAME-001",
                            "text": f"{building}-3",
                            "x": "20",
                            "y": "0",
                        },
                        {
                            "frame_id": "FRAME-001",
                            "text": f"{building}-A",
                            "x": "0",
                            "y": "0",
                        },
                        {
                            "frame_id": "FRAME-001",
                            "text": f"{building}-B",
                            "x": "0",
                            "y": "10",
                        },
                    ]
                )
            frame_texts = root / "axes.csv"
            self.write_csv(frame_texts, axes)
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "primary_source_id": "PRIMARY",
                        "scope_buildings": ["1"],
                        "scope_floors": ["1F"],
                        "sources": [
                            {
                                "source_id": "PRIMARY",
                                "view_type": "structural_plan",
                                "candidate_csv": str(candidate),
                                "frame_texts_csv": str(frame_texts),
                                "include_decisions": ["counted"],
                                "semantic_leaf_only": True,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = root / "out"
            result = subprocess.run(
                [
                    sys.executable,
                    str(V12_PATH),
                    str(manifest),
                    "--output-dir",
                    str(output),
                    "--prefix",
                    "AMB",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = V19.parse_result(result.stdout)
            self.assertIsNotNone(payload)
            self.assertEqual(
                payload["status"],
                "single_primary_device_location_unresolved",
            )
            self.assertEqual(payload["primary_location_issue_count"], 1)
            self.assertEqual(payload["physical_device_count"], 0)
            occurrences = V19.read_csv(
                output / "AMB.device_occurrence.csv"
            )
            self.assertEqual(
                occurrences[0]["mapping_status"],
                "primary_axis_location_ambiguous",
            )


if __name__ == "__main__":
    unittest.main()
