import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]


def run_doctor(script: Path, work_root: Path, installed: Path) -> dict:
    completed = subprocess.run(
        [
            "pwsh", "-NoProfile", "-File", str(script),
            "-WorkRoot", str(work_root),
            "-InstalledSkillRoot", str(installed),
            "-SkipCadDiscovery",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    output = Path(completed.stdout.strip())
    return json.loads(output.read_text(encoding="utf-8"))


def test_repository_doctor_writes_self_contained_session(tmp_path: Path):
    installed = tmp_path / "installed"
    shutil.copytree(ROOT / "codex-skill" / "cad-drawing-evidence-extraction", installed)
    result = run_doctor(ROOT / "scripts" / "检查CAD工具包会话.ps1", tmp_path / "work", installed)
    assert result["schema_version"] == "cad-toolkit-session/1.0"
    assert result["execution_context"] == "repository"
    assert result["manifest_valid"] is True
    assert result["required_file_missing_count"] == 0
    assert result["work_root_safe"] is True
    assert result["skill_sync"]["status"] == "in_sync"
    assert result["absence_proven"] is False


def test_installed_skill_layout_bootstraps_without_repository_context(tmp_path: Path):
    installed = tmp_path / "installed"
    shutil.copytree(ROOT / "codex-skill" / "cad-drawing-evidence-extraction", installed)
    result = run_doctor(installed / "scripts" / "检查CAD工具包会话.ps1", tmp_path / "work", installed)
    assert result["execution_context"] == "installed_skill"
    assert result["toolkit_root"] == str(installed.resolve())
    assert result["required_file_missing_count"] == 0
    assert result["skill_sync"]["status"] == "in_sync"
    assert result["next_entry"].endswith("运行CAD只读自动后端.ps1")


def test_doctor_reports_skill_drift(tmp_path: Path):
    installed = tmp_path / "installed"
    shutil.copytree(ROOT / "codex-skill" / "cad-drawing-evidence-extraction", installed)
    (installed / "SKILL.md").write_text("drift", encoding="utf-8")
    result = run_doctor(ROOT / "scripts" / "检查CAD工具包会话.ps1", tmp_path / "work", installed)
    assert result["overall_status"] == "warning"
    assert result["skill_sync"]["status"] == "drifted"
    assert "SKILL.md" in result["skill_sync"]["differences"]


def test_repository_wrapper_uses_packaged_skill_implementation():
    wrapper = (ROOT / "scripts" / "检查CAD工具包会话.ps1").read_text(encoding="utf-8-sig")
    assert "codex-skill\\cad-drawing-evidence-extraction\\scripts\\检查CAD工具包会话.ps1" in wrapper
    assert "ToolkitRoot = $toolkitRoot" in wrapper
