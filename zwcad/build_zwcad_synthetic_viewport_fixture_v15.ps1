param(
    [Parameter(Mandatory = $true)]
    [string]$ZwcadRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [string]$SourcePath
)

$ErrorActionPreference = 'Stop'

$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if ([string]::IsNullOrWhiteSpace($SourcePath)) {
    $SourcePath = Join-Path (
        Join-Path (Split-Path -Parent $PSScriptRoot) 'zwcad_text_exporter'
    ) 'ZwcadSyntheticViewportFixtureV15.cs'
}

$source = (Resolve-Path -LiteralPath $SourcePath).Path
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw "C# compiler not found: $compiler"
}
foreach ($reference in @('ZwManaged.dll', 'ZwDatabaseMgd.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ZwcadRoot $reference) -PathType Leaf)) {
        throw "ZWCAD API assembly not found: $reference"
    }
}

$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$managedReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwManaged.dll')
$databaseReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwDatabaseMgd.dll')

& $compiler /nologo /target:library /platform:x64 /out:$OutputPath `
    $managedReference `
    $databaseReference `
    $source

if ($LASTEXITCODE -ne 0) {
    throw "Compilation failed with exit code $LASTEXITCODE"
}

Get-Item -LiteralPath $OutputPath |
    Select-Object FullName, Length, LastWriteTime


