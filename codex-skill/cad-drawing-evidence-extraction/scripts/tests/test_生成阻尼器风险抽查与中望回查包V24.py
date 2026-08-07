import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "生成阻尼器风险抽查与中望回查包V24.py"
)
SPEC = importlib.util.spec_from_file_location("v24", MODULE_PATH)
V24 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = V24
SPEC.loader.exec_module(V24)


def base_template(**updates):
    value = {
        "group_id": "GROUP-1",
        "group_status": "cross_view_quantity_closed",
        "physical_template_id": "PT-0001",
        "building_id": "1",
        "floors": ["1F"],
        "axis_position_key": "B1|X1>2@0.5000|YA>B@0.5000",
        "primary_instance_key": "ROOT/DEVICE",
        "primary_world_x": "5",
        "primary_world_y": "5",
        "location_method": "building_axis_grid",
        "evidence_status": "evidence_trace_complete",
        "evidence_reason": "",
        "label_handles": ["X1", "X2", "YA", "YB"],
        "geometry_handles": [],
        "x_evidence": {
            "kind": "coordinate_axis_band",
            "low_boundary": {"label": "1", "coordinate": 0},
            "high_boundary": {"label": "2", "coordinate": 10},
            "distance_to_low": 5,
            "distance_to_high": 5,
        },
        "y_evidence": {
            "kind": "coordinate_axis_band",
            "low_boundary": {"label": "A", "coordinate": 0},
            "high_boundary": {"label": "B", "coordinate": 10},
            "distance_to_low": 5,
            "distance_to_high": 5,
        },
        "preview_relative": "previews/PT-0001.svg",
        "manifest_path": "",
        "source_file": "",
    }
    value.update(updates)
    return value


class RiskTests(unittest.TestCase):
    def test_regular_middle_of_band_is_low_risk(self):
        row = V24.risk_record(base_template(), {})
        self.assertEqual(row["risk_tier"], "P3")
        self.assertFalse(row["mandatory_review"])

    def test_out_curved_axis_is_mandatory(self):
        template = base_template(
            group_status="single_primary_location_visibility_unverified",
            axis_position_key="B1|X2>OUT@0.1000|YA>B@0.5000",
            location_method="building_axis_grid_geometry",
            y_evidence={
                "kind": "curved_axis_family",
                "distance_to_low": 5,
                "distance_to_high": 5,
                "low_boundary": {"label_match_distance": 1},
                "high_boundary": {"label_match_distance": 1},
            },
        )
        row = V24.risk_record(template, {})
        self.assertEqual(row["risk_tier"], "P1")
        self.assertTrue(row["mandatory_review"])
        self.assertIn("OUT", row["risk_reasons"])
        self.assertIn("圆弧", row["risk_reasons"])

    def test_unresolved_evidence_is_p0(self):
        row = V24.risk_record(
            base_template(evidence_status="evidence_trace_unresolved"), {}
        )
        self.assertEqual(row["risk_tier"], "P0")
        self.assertTrue(row["mandatory_review"])

    def test_stratified_sample_covers_buildings(self):
        rows = []
        for building in ("1", "2", "3"):
            for index in range(4):
                rows.append(
                    {
                        "building_id": building,
                        "risk_score": 10 - index,
                        "group_id": "G",
                        "physical_template_id": f"{building}-{index}",
                    }
                )
        selected = V24.stratified_sample(rows, rate=0.1, cap=10)
        self.assertEqual({row["building_id"] for row in selected}, {"1", "2", "3"})


class EndToEndTests(unittest.TestCase):
    def test_generates_light_manifest_html_and_lisp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            drawing = root / "readonly-copy.dwg"
            drawing.write_bytes(b"synthetic placeholder")
            preview_dir = root / "v23" / "previews"
            preview_dir.mkdir(parents=True)
            (preview_dir / "PT-0001.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                encoding="utf-8",
            )
            v23_path = root / "v23" / "V23逐台几何定位证据.json"
            template = base_template(review_drawing_path=str(drawing))
            v23_path.write_text(
                json.dumps(
                    {
                        "version": "V23",
                        "status": "v23_located_template_evidence_complete",
                        "templates": [template],
                        "unresolved_groups": [],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output = root / "v24"
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    str(v23_path),
                    "--output-dir",
                    str(output),
                    "--low-sample-rate",
                    "1",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(
                (output / "V24风险抽查汇总.json").read_text(encoding="utf-8")
            )
            self.assertEqual(summary["selected_template_count"], 1)
            self.assertEqual(summary["lisp_review_task_count"], 1)
            with (output / "V24抽查任务.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            lisp = (output / "V24中望回查定位.lsp").read_text(
                encoding="utf-8"
            )
            self.assertIn("V24OPEN", lisp)
            self.assertIn("readonly-copy.dwg", lisp)
            self.assertIn("V24风险分层抽查索引", (
                output / "V24风险抽查索引.html"
            ).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
