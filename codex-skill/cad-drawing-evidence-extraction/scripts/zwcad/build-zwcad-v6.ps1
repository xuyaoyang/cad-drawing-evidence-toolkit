param(
    [Parameter(Mandatory)][string]$ZwcadRoot,
    [Parameter(Mandatory)][string]$OutputDir
)

$ErrorActionPreference = 'Stop'

$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$source = Join-Path $PSScriptRoot 'ZwcadSymbolExporterV6.cs'
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
$output = Join-Path $OutputDir 'CadReadingExploration.ZwcadSymbolExporterV6.dll'

if (!(Test-Path -LiteralPath $compiler)) {
    throw "C# compiler not found: $compiler"
}
foreach ($reference in @('ZwManaged.dll', 'ZwDatabaseMgd.dll')) {
    if (!(Test-Path -LiteralPath (Join-Path $ZwcadRoot $reference))) {
        throw "ZWCAD API assembly not found: $reference"
    }
}

$managedReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwManaged.dll')
$databaseReference = '/reference:' + (Join-Path $ZwcadRoot 'ZwDatabaseMgd.dll')

& $compiler /nologo /target:library /platform:x64 /out:$output `
    $managedReference `
    $databaseReference `
    $source

if ($LASTEXITCODE -ne 0) {
    throw "Compilation failed with exit code $LASTEXITCODE"
}

Get-Item -LiteralPath $output | Select-Object FullName, Length, LastWriteTime

