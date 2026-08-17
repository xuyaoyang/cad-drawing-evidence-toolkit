import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]


def path(relative: str) -> Path:
    candidate = ROOT / relative
    if not candidate.is_file():
        candidate = ROOT / "scripts" / relative
    return candidate


def read(relative: str) -> str:
    return path(relative).read_text(encoding="utf-8-sig")


def run_policy(script: str):
    policy = str(path("autocad/AutoCADVersionPolicy.ps1")).replace("'", "''")
    command = f". '{policy}'; {script} | ConvertTo-Json -Compress"
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def test_autocad_policy_declares_supported_releases_and_dwg_limits():
    policies = run_policy("@(Get-SupportedAutoCadPolicies)")
    assert [row["release"] for row in policies] == ["2023", "2020", "2018", "2014"]
    assert [row["api_version"] for row in policies] == [
        "R24.2",
        "R23.1",
        "R22.0",
        "R19.1",
    ]
    assert [row["max_dwg_version"] for row in policies] == [
        "AC1032",
        "AC1032",
        "AC1032",
        "AC1027",
    ]


def test_autocad_2014_rejects_ac1032_without_conversion():
    result = run_policy(
        "$p = Get-AutoCadHostPolicy -FileVersion 'R19.1.0'; "
        "Test-AutoCadDwgCompatibility -DwgVersion 'AC1032' -Policy $p"
    )
    assert result["compatible"] is False
    assert result["reason"] == "dwg_version_newer_than_host"
    assert result["max_dwg_version"] == "AC1027"


def test_autocad_2018_2020_and_2023_accept_ac1032():
    result = run_policy(
        "@('R24.2.0','R23.1.0','R22.0.0') | ForEach-Object { "
        "$p = Get-AutoCadHostPolicy -FileVersion $_; "
        "Test-AutoCadDwgCompatibility -DwgVersion 'AC1032' -Policy $p }"
    )
    assert len(result) == 3
    assert all(row["compatible"] is True for row in result)


def test_generic_adapter_reuses_shared_sources_and_requires_x64():
    build = read("autocad/build_autocad_exporters.ps1")
    runner = read("autocad/AutoCADCoreConsole只读导出.ps1")
    assert "using ZwSoft.ZwCAD." in build
    assert "using Autodesk.AutoCAD." in build
    assert "ZwcadVisibilityExporterV13.cs" in build
    assert "Only 64-bit AutoCAD hosts are supported" in build
    assert "Test-AutoCadDwgCompatibility" in runner
    assert "dwg_version_incompatible" in runner


def test_core_console_hash_gates_the_copy_and_refuses_output_reuse():
    runner = read("autocad/AutoCADCoreConsole只读导出.ps1")
    assert "$scriptLines.Add('_Y')" in runner
    assert "input_hash_changed" in runner
    assert "GetShortPathName" in runner
    assert "Refusing to reuse or overwrite pre-existing AutoCAD evidence" in runner


def test_router_declares_exact_fallback_order_and_output_gate():
    router = read("scripts/运行CAD只读自动后端.ps1")
    for backend in (
        "AutoCAD2023",
        "AutoCAD2020",
        "AutoCAD2018",
        "AutoCAD2014",
    ):
        assert backend in router
    assert "Get-MissingNativeFullOutputs" in router
    assert "No conversion was performed" in router
    assert "source_dwg_version = $sourceDwgVersion" in router
    assert "absence_proven = $false" in router
