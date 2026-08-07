param(
    [Parameter(Mandatory)][string]$ZwcadRoot,
    [Parameter(Mandatory)][string]$OutputDir
)

$ErrorActionPreference = 'Stop'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $csc)) {
    throw "C# compiler not found: $csc"
}
foreach ($reference in 'ZwManaged.dll', 'ZwDatabaseMgd.dll') {
    if (!(Test-Path -LiteralPath (Join-Path $ZwcadRoot $reference))) {
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
    }
)

foreach ($target in $targets) {
    $source = Join-Path $PSScriptRoot $target.Source
    $output = Join-Path $OutputDir $target.Output
    if (!(Test-Path -LiteralPath $source)) {
        throw "Exporter source not found: $source"
    }
    & $csc /nologo /target:library /platform:x64 /out:$output `
        $managedReference `
        $databaseReference `
        $source
    if ($LASTEXITCODE -ne 0) {
        throw "Compilation failed for $($target.Source) with exit code $LASTEXITCODE"
    }
}

Get-ChildItem -LiteralPath $OutputDir -Filter 'CadReadingExploration.Zwcad*.dll' |
    Sort-Object Name |
    Select-Object FullName, Length, LastWriteTime
