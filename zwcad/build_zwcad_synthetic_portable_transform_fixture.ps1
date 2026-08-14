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
    $SourcePath = Join-Path $PSScriptRoot 'ZwcadSyntheticPortableTransformFixture.cs'
}
$source = (Resolve-Path -LiteralPath $SourcePath).Path
foreach ($path in @($compiler, (Join-Path $ZwcadRoot 'ZwManaged.dll'), (Join-Path $ZwcadRoot 'ZwDatabaseMgd.dll'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required build file not found: $path" }
}
$outputDirectory = Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath))
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
& $compiler /nologo /target:library /platform:x64 /out:$OutputPath `
    ('/reference:' + (Join-Path $ZwcadRoot 'ZwManaged.dll')) `
    ('/reference:' + (Join-Path $ZwcadRoot 'ZwDatabaseMgd.dll')) `
    $source
if ($LASTEXITCODE -ne 0) { throw "Compilation failed with exit code $LASTEXITCODE" }
Get-Item -LiteralPath $OutputPath | Select-Object FullName, Length, LastWriteTime
