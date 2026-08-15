from pathlib import Path


ROOT = Path(__file__).parents[2]


def read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        path = ROOT / "scripts" / relative
    return path.read_text(encoding="utf-8-sig")


def test_autocad_adapter_is_strictly_r24_2_and_reuses_shared_sources():
    build = read("autocad/build_autocad_2023_exporters.ps1")
    runner = read("autocad/AutoCADCoreConsole只读导出.ps1")
    assert "^R?24\\.2(?:\\.|$)" in build
    assert "^R?24\\.2(?:\\.|$)" in runner
    assert "using ZwSoft.ZwCAD." in build
    assert "using Autodesk.AutoCAD." in build
    assert "ZwcadVisibilityExporterV13.cs" in build


def test_core_console_discards_changes_and_hash_gates_the_copy():
    runner = read("autocad/AutoCADCoreConsole只读导出.ps1")
    assert "$scriptLines.Add('_Y')" in runner
    assert "input_hash_changed" in runner
    assert "GetShortPathName" in runner


def test_router_declares_exact_fallback_order_and_output_gate():
    router = read("scripts/运行CAD只读自动后端.ps1")
    assert "route_order = @('ACadSharp', 'ZWCAD', 'AutoCAD2023')" in router
    assert router.index("if ($null -eq $selectedBackend)") < router.index(
        "AutoCAD 2023 (R24.2)"
    )
    assert "Get-MissingNativeFullOutputs" in router
    assert "absence_proven = $false" in router
