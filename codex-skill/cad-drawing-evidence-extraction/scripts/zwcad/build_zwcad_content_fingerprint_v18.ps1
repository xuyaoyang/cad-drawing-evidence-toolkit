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
$source = Join-Path $SourceDir 'ZwcadContentFingerprintExporterV18.cs'
$output = Join-Path $OutputDir (
    'CadReadingExploration.ZwcadContentFingerprintExporterV18.dll'
)
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Exporter source not found: $source"
}

$managedReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwManaged.dll')
$databaseReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwDatabaseMgd.dll')
& $compiler /nologo /target:library /platform:x64 /out:$output `
    $managedReference `
    $databaseReference `
    $source
if ($LASTEXITCODE -ne 0) {
    throw "Compilation failed for ZwcadContentFingerprintExporterV18.cs: $LASTEXITCODE"
}

Get-Item -LiteralPath $output |
    Select-Object FullName, Length, LastWriteTime



