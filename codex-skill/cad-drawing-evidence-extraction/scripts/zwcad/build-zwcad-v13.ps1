param(
    [Parameter(Mandatory = $true)]
    [string]$ZwcadRoot,
    [string]$SourcePath,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if ([string]::IsNullOrWhiteSpace($SourcePath)) {
    $localSource = Join-Path $PSScriptRoot 'ZwcadVisibilityExporterV13.cs'
    $projectSource = Join-Path (
        Join-Path (Split-Path -Parent $PSScriptRoot) 'zwcad_text_exporter'
    ) 'ZwcadVisibilityExporterV13.cs'
    $SourcePath = if (Test-Path -LiteralPath $localSource -PathType Leaf) {
        $localSource
    }
    else {
        $projectSource
    }
}
$source = (Resolve-Path -LiteralPath $SourcePath).Path
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    throw '必须显式传入 -OutputPath；DLL 构建输出不得写入技能目录。'
}

if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw "C# compiler not found: $compiler"
}
foreach ($reference in @('ZwManaged.dll', 'ZwDatabaseMgd.dll')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ZwcadRoot $reference) -PathType Leaf)) {
        throw "ZWCAD API assembly not found: $reference"
    }
}

$outputDirectory = Split-Path -Parent $OutputPath
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
}

$managedReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwManaged.dll')
$databaseReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwDatabaseMgd.dll')

& $compiler /nologo /target:library /platform:x64 /out:$OutputPath `
    $managedReference `
    $databaseReference `
    $source

if ($LASTEXITCODE -ne 0) {
    throw "Compilation failed with exit code $LASTEXITCODE"
}

Get-Item -LiteralPath $OutputPath | Select-Object FullName, Length, LastWriteTime
