param(
    [Parameter(Mandatory = $true)]
    [string]$AutoCadRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$SourceDir
)

$ErrorActionPreference = 'Stop'

$genericBuilder = Join-Path $PSScriptRoot 'build_autocad_exporters.ps1'
& $genericBuilder -AutoCadRoot $AutoCadRoot -OutputDir $OutputDir `
    -SourceDir $SourceDir -RequiredRelease '2023'
