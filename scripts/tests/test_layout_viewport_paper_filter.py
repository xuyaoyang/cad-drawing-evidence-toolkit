import csv
import json
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "分析布局视口阻尼器可见性.py"


def test_paper_viewport_is_reported_but_not_used_as_model_view(tmp_path):
    visibility = tmp_path / "visibility.json"
    candidates = tmp_path / "candidates.csv"
    output = tmp_path / "output"
    visibility.write_text(
        json.dumps(
            {
                "records": [],
                "layouts": [{"layout_name": "Layout1"}],
                "viewports": [
                    {
                        "handle": "PAPER",
                        "number": 1,
                        "is_paper_viewport": True,
                        "view_direction_z": 0,
                    },
                    {
                        "handle": "MODEL",
                        "number": 2,
                        "is_paper_viewport": False,
                        "on": True,
                        "entity_visible": True,
                        "paper_width": 100,
                        "paper_height": 50,
                        "view_height": 50,
                        "view_direction_x": 0,
                        "view_direction_y": 0,
                        "view_direction_z": 1,
                        "layer_state": {},
                        "frozen_layers": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    with candidates.open("w", encoding="utf-8", newline="") as stream:
        csv.DictWriter(stream, fieldnames=["semantic_leaf_symbol"]).writeheader()
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(visibility), str(candidates), "--output-dir", str(output)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "layout_viewport_visibility_consistent"
    assert result["viewport_count"] == 1
    assert result["paper_viewport_count"] == 1
    summary = next(output.glob("*.viewport_summary.csv"))
    rows = list(csv.DictReader(summary.open(encoding="utf-8-sig")))
    assert len(rows) == 2
    assert {row["is_paper_viewport"] for row in rows} == {"True", "False"}
