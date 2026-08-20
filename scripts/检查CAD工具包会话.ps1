param(
    [Parameter(Mandatory = $true)]
    [string]$WorkRoot,
    [string]$OutputPath,
    [string]$InstalledSkillRoot,
    [switch]$SkipCadDiscovery,
    [switch]$PassThru
)

$ErrorActionPreference = 'Stop'
$toolkitRoot = Split-Path -Parent $PSScriptRoot
$implementation = Join-Path $toolkitRoot 'codex-skill\cad-drawing-evidence-extraction\scripts\检查CAD工具包会话.ps1'
if (-not (Test-Path -LiteralPath $implementation -PathType Leaf)) {
    throw "Session Doctor implementation not found: $implementation"
}

$arguments = @{
    WorkRoot = $WorkRoot
    ToolkitRoot = $toolkitRoot
    SkipCadDiscovery = $SkipCadDiscovery
    PassThru = $PassThru
}
if (-not [string]::IsNullOrWhiteSpace($OutputPath)) { $arguments.OutputPath = $OutputPath }
if (-not [string]::IsNullOrWhiteSpace($InstalledSkillRoot)) { $arguments.InstalledSkillRoot = $InstalledSkillRoot }
& $implementation @arguments
