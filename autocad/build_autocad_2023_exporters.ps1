param(
    [Parameter(Mandatory = $true)]
    [string]$AutoCadRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$SourceDir
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($SourceDir)) {
    $SourceDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'zwcad'
}

$resolvedRoot = (Resolve-Path -LiteralPath $AutoCadRoot).Path
$resolvedSource = (Resolve-Path -LiteralPath $SourceDir).Path
$acadExe = Join-Path $resolvedRoot 'acad.exe'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'

foreach ($required in @($acadExe, $compiler)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required file not found: $required"
    }
}

$acadVersion = (Get-Item -LiteralPath $acadExe).VersionInfo.FileVersion
if ($acadVersion -notmatch '^R?24\.2(?:\.|$)') {
    throw (
        "This adapter is intentionally limited to AutoCAD 2023 (R24.2). " +
        "Detected: $acadVersion"
    )
}

$referenceNames = @('AcCoreMgd.dll', 'AcDbMgd.dll', 'AcMgd.dll')
$references = foreach ($name in $referenceNames) {
    $path = Join-Path $resolvedRoot $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "AutoCAD 2023 API assembly not found: $path"
    }
    '/reference:' + $path
}

$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
$generatedSourceDir = Join-Path $resolvedOutput 'generated-source'
New-Item -ItemType Directory -Path $resolvedOutput, $generatedSourceDir -Force |
    Out-Null

$targets = @(
    @{ Source = 'ZwcadTextExporterV2.cs'; Output = 'CadReadingExploration.AutoCADTextExporterV5.dll' },
    @{ Source = 'ZwcadFrameExporterV4.cs'; Output = 'CadReadingExploration.AutoCADFrameExporterV5.dll' },
    @{ Source = 'ZwcadSymbolExporterV6.cs'; Output = 'CadReadingExploration.AutoCADSymbolExporterV6.dll' },
    @{ Source = 'ZwcadOrientedTextExporterV7.cs'; Output = 'CadReadingExploration.AutoCADOrientedTextExporterV7.dll' },
    @{ Source = 'ZwcadPrimitiveGeometryExporterV10.cs'; Output = 'CadReadingExploration.AutoCADPrimitiveGeometryExporterV10.dll' },
    @{ Source = 'ZwcadVisibilityExporterV13.cs'; Output = 'CadReadingExploration.AutoCADVisibilityExporterV13.dll' },
    @{ Source = 'ZwcadContentFingerprintExporterV18.cs'; Output = 'CadReadingExploration.AutoCADContentFingerprintExporterV18.dll' }
)

$buildRows = New-Object System.Collections.Generic.List[object]
foreach ($target in $targets) {
    $sourcePath = Join-Path $resolvedSource $target.Source
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Shared exporter source not found: $sourcePath"
    }

    $generatedPath = Join-Path $generatedSourceDir $target.Source.Replace('Zwcad', 'AutoCAD')
    $sourceText = Get-Content -LiteralPath $sourcePath -Encoding UTF8 -Raw
    $sourceText = $sourceText.Replace(
        'using ZwSoft.ZwCAD.',
        'using Autodesk.AutoCAD.'
    )
    $sourceText = $sourceText.Replace(
        'namespace CadReadingExploration.Zwcad',
        'namespace CadReadingExploration.AutoCAD'
    )
    [System.IO.File]::WriteAllText(
        $generatedPath,
        $sourceText,
        [System.Text.UTF8Encoding]::new($false)
    )

    $outputPath = Join-Path $resolvedOutput $target.Output
    & $compiler /nologo /target:library /platform:x64 /out:$outputPath `
        @references `
        $generatedPath
    if ($LASTEXITCODE -ne 0) {
        throw "AutoCAD 2023 compilation failed for $($target.Source): $LASTEXITCODE"
    }

    $buildRows.Add([pscustomobject]@{
        source = $target.Source
        output = $outputPath
        length = (Get-Item -LiteralPath $outputPath).Length
        autocad_version = $acadVersion
    })
}

$manifestPath = Join-Path $resolvedOutput 'autocad-2023-build.json'
[ordered]@{
    backend = 'autocad-2023-dotnet-coreconsole'
    autocad_root = $resolvedRoot
    autocad_version = $acadVersion
    source_dir = $resolvedSource
    generated_source_dir = $generatedSourceDir
    outputs = $buildRows.ToArray()
} |
    ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $manifestPath -Encoding utf8

$buildRows
