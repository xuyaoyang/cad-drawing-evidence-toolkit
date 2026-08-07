param(
    [Parameter(Mandatory = $true)]
    [string]$ZwcadRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$SourceDir
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($SourceDir)) {
    $SourceDir = Join-Path (
        Split-Path -Parent $PSScriptRoot
    ) 'zwcad_text_exporter'
}
$SourceDir = (Resolve-Path -LiteralPath $SourceDir).Path
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'

if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw "C# compiler not found: $compiler"
}
foreach ($reference in @('ZwManaged.dll', 'ZwDatabaseMgd.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ZwcadRoot $reference) -PathType Leaf)) {
        throw "ZWCAD API assembly not found: $reference"
    }
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$managedReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwManaged.dll')
$databaseReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwDatabaseMgd.dll')
$targets = @(
    @{
        Source = 'ZwcadTextExporterV2.cs'
        Output = 'CadReadingExploration.ZwcadTextExporterV5.dll'
    },
    @{
        Source = 'ZwcadFrameExporterV4.cs'
        Output = 'CadReadingExploration.ZwcadFrameExporterV5.dll'
    },
    @{
        Source = 'ZwcadSymbolExporterV6.cs'
        Output = 'CadReadingExploration.ZwcadSymbolExporterV6.dll'
    },
    @{
        Source = 'ZwcadOrientedTextExporterV7.cs'
        Output = 'CadReadingExploration.ZwcadOrientedTextExporterV7.dll'
    },
    @{
        Source = 'ZwcadPrimitiveGeometryExporterV10.cs'
        Output = 'CadReadingExploration.ZwcadPrimitiveGeometryExporterV10.dll'
    },
    @{
        Source = 'ZwcadVisibilityExporterV13.cs'
        Output = 'CadReadingExploration.ZwcadVisibilityExporterV13.dll'
    }
)

foreach ($target in $targets) {
    $source = Join-Path $SourceDir $target.Source
    $output = Join-Path $OutputDir $target.Output
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Exporter source not found: $source"
    }
    & $compiler /nologo /target:library /platform:x64 /out:$output `
        $managedReference `
        $databaseReference `
        $source
    if ($LASTEXITCODE -ne 0) {
        throw "Compilation failed for $($target.Source): $LASTEXITCODE"
    }
}

Get-ChildItem -LiteralPath $OutputDir -Filter 'CadReadingExploration.Zwcad*.dll' |
    Sort-Object Name |
    Select-Object FullName, Length, LastWriteTime
