param(
    [Parameter(Mandatory)][string]$ZwcadRoot,
    [string]$Source = (Join-Path $PSScriptRoot 'ZwcadTextExporterV2.cs'),
    [string]$Output = (Join-Path $PSScriptRoot 'CadReadingExploration.ZwcadTextExporterV5.dll')
)

$ErrorActionPreference = 'Stop'
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $csc)) { throw "C# compiler not found: $csc" }
foreach ($reference in 'ZwManaged.dll', 'ZwDatabaseMgd.dll') {
    if (!(Test-Path -LiteralPath (Join-Path $ZwcadRoot $reference))) {
        throw "ZWCAD API assembly not found: $reference"
    }
}
& $csc /nologo /target:library /platform:x64 /out:$Output `
  /reference:(Join-Path $ZwcadRoot 'ZwManaged.dll') `
  /reference:(Join-Path $ZwcadRoot 'ZwDatabaseMgd.dll') $Source
if ($LASTEXITCODE -ne 0) { throw "Compilation failed with exit code $LASTEXITCODE" }
Get-Item -LiteralPath $Output | Select-Object FullName,Length,LastWriteTime
