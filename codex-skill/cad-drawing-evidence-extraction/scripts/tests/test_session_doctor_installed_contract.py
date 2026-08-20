import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_installed_skill_session_doctor_runs_without_repository(tmp_path: Path):
    script = ROOT / "scripts" / "检查CAD工具包会话.ps1"
    completed = subprocess.run(
        [
            "pwsh", "-NoProfile", "-File", str(script),
            "-WorkRoot", str(tmp_path / "work"),
            "-InstalledSkillRoot", str(ROOT),
            "-SkipCadDiscovery",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = Path(completed.stdout.strip())
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["execution_context"] == "installed_skill"
    assert result["required_file_missing_count"] == 0
    assert result["skill_sync"]["status"] == "in_sync"
    assert result["absence_proven"] is False
