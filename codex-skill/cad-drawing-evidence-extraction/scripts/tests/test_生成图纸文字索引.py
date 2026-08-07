from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "生成图纸文字索引.py"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_portable_text_index(tmp_path: Path) -> None:
    frames = tmp_path / "frames.csv"
    texts = tmp_path / "texts.csv"
    output = tmp_path / "index.md"
    write_csv(frames, ["frame_id"], [{"frame_id": "FRAME-001"}, {"frame_id": "FRAME-002"}])
    write_csv(
        texts,
        ["frame_id", "entity_type", "origin", "text", "handle", "x", "y"],
        [
            {
                "frame_id": "FRAME-001",
                "entity_type": "DBText",
                "origin": "direct",
                "text": "结构设计总说明",
                "handle": "A1",
                "x": "10",
                "y": "20",
            },
            {
                "frame_id": "FRAME-001",
                "entity_type": "MText",
                "origin": "direct",
                "text": "阻尼器" + "技术要求" * 300,
                "handle": "A2",
                "x": "11",
                "y": "19",
            },
            {
                "frame_id": "",
                "entity_type": "DBText",
                "origin": "direct",
                "text": "未归属阻尼器",
                "handle": "B1",
                "x": "0",
                "y": "0",
            },
        ],
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--frames",
            str(frames),
            "--texts",
            str(texts),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    content = output.read_text(encoding="utf-8")
    assert "frames=2 texts=3" in completed.stdout
    assert "| FRAME-001 | 2 | 2 | 1 | 结构设计总说明 | 阻尼器(1) |" in content
    assert "| FRAME-002 | 0 | 0 | 0 | 未自动定位 | 无 |" in content
    assert "共 1 条" in content
    assert "B1" not in content
