import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "生成阻尼器几何定位证据包V23.py"
)
SPEC = importlib.util.spec_from_file_location("v23", MODULE_PATH)
V23 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = V23
SPEC.loader.exec_module(V23)


class EvidenceValidationTests(unittest.TestCase):
    def test_same_location_accepts_matching_recalculation(self):
        expected = {
            "axis_x_low": "1",
            "axis_x_high": "2",
            "axis_x_fraction": "0.5000",
            "axis_y_low": "A",
            "axis_y_high": "B",
            "axis_y_fraction": "0.2500",
        }
        x_location = V23.CORE.AxisLocation("1", "2", 0.5, {})
        y_location = V23.CORE.AxisLocation("A", "B", 0.25, {})
        self.assertEqual(
            V23.same_location(expected, x_location, y_location),
            (True, ""),
        )

    def test_missing_curve_extension_is_unresolved(self):
        evidence = {
            "kind": "curved_axis_family",
            "low_boundary": {
                "label": "A",
                "label_handles": ["T-A"],
                "arc_record_key": "R/A",
                "extension_record_key": "R/EA",
                "label_match_distance": 10.0,
            },
            "high_boundary": {
                "label": "B",
                "label_handles": ["T-B"],
                "arc_record_key": "R/B",
                "extension_record_key": "",
                "label_match_distance": 12.0,
            },
        }
        complete, reason = V23.evidence_complete(evidence)
        self.assertFalse(complete)
        self.assertIn("切向延伸句柄", reason)


class DirectManifestEndToEndTests(unittest.TestCase):
    @staticmethod
    def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
        fields = sorted({key for row in rows for key in row})
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def make_fixture(self, root: Path, x_fraction: str = "0.5000"):
        frame_path = root / "frame.csv"
        candidate_path = root / "candidate.csv"
        device_path = root / "physical_device.csv"
        manifest_path = root / "manifest.json"
        frame_rows = [
            {
                "frame_id": "FRAME-001",
                "text": text,
                "x": str(x),
                "y": str(y),
                "handle": handle,
            }
            for text, x, y, handle in (
                ("1-1", 0, 0, "X1"),
                ("1-2", 10, 0, "X2"),
                ("1-3", 20, 0, "X3"),
                ("1-A", 0, 0, "YA"),
                ("1-B", 0, 10, "YB"),
            )
        ]
        self.write_csv(frame_path, frame_rows)
        self.write_csv(
            candidate_path,
            [
                {
                    "instance_key": "ROOT/DEVICE",
                    "min_x": "4",
                    "min_y": "4",
                    "max_x": "6",
                    "max_y": "6",
                }
            ],
        )
        self.write_csv(
            device_path,
            [
                {
                    "physical_device_id": "PD-B1-1F-PT-0001",
                    "physical_template_id": "PT-0001",
                    "building_id": "1",
                    "floor": "1F",
                    "axis_position_key": (
                        f"B1|X1>2@{x_fraction}|YA>B@0.5000"
                    ),
                    "axis_x_low": "1",
                    "axis_x_high": "2",
                    "axis_x_fraction": x_fraction,
                    "axis_y_low": "A",
                    "axis_y_high": "B",
                    "axis_y_fraction": "0.5000",
                    "primary_source_id": "PRIMARY",
                    "primary_frame_id": "FRAME-001",
                    "primary_instance_key": "ROOT/DEVICE",
                    "primary_world_x": "5",
                    "primary_world_y": "5",
                    "location_method": "building_axis_grid",
                }
            ],
        )
        manifest_path.write_text(
            json.dumps(
                {
                    "project_id": "SYNTHETIC-V23",
                    "primary_source_id": "PRIMARY",
                    "sources": [
                        {
                            "source_id": "PRIMARY",
                            "view_type": "structural_plan",
                            "candidate_csv": str(candidate_path),
                            "frame_texts_csv": str(frame_path),
                            "include_decisions": ["counted"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return manifest_path, device_path

    def run_fixture(self, x_fraction: str):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        manifest_path, device_path = self.make_fixture(root, x_fraction)
        output = root / "output"
        result = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                str(manifest_path),
                "--physical-device-csv",
                str(device_path),
                "--output-dir",
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return temporary, output, result

    def test_direct_scalar_manifest_builds_complete_preview(self):
        temporary, output, result = self.run_fixture("0.5000")
        with temporary:
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(
                (output / "V23逐台几何定位证据.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                payload["status"], "v23_located_template_evidence_complete"
            )
            self.assertEqual(payload["template_count"], 1)
            self.assertEqual(payload["device_count"], 1)
            preview = output / payload["templates"][0]["preview_relative"]
            self.assertTrue(preview.is_file())
            self.assertIn("<svg", preview.read_text(encoding="utf-8"))

    def test_recalculation_mismatch_stays_partial(self):
        temporary, output, result = self.run_fixture("0.9000")
        with temporary:
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(
                (output / "V23逐台几何定位证据.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["status"], "v23_evidence_package_partial")
            self.assertEqual(payload["unresolved_template_count"], 1)
            self.assertIn(
                "axis_x_fraction不一致",
                payload["templates"][0]["evidence_reason"],
            )


if __name__ == "__main__":
    unittest.main()
