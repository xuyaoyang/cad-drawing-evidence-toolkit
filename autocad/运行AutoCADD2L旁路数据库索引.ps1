param(
    [Parameter(Mandatory = $true)]
    [string]$TargetDrawingPath,

    [Parameter(Mandatory = $true)]
    [string]$HostDrawingPath,

    [Parameter(Mandatory = $true)]
    [string]$AutoCadRoot,

    [Parameter(Mandatory = $true)]
    [string]$PluginDir,

    [Parameter(Mandatory = $true)]
    [string]$WorkRoot,

    [int]$TimeoutSeconds = 300,

    [string]$ExpandBlockRegex,

    [string]$ExpandRootHandleRegex,

    [string]$ExplodeTopLevelHandleRegex,

    [int]$MaxExpandedEntities = 500000
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'AutoCADVersionPolicy.ps1')

if (-not ('CadD2LNativePath' -as [type])) {
    Add-Type -TypeDefinition @'
using System.Text;
using System.Runtime.InteropServices;
public static class CadD2LNativePath
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    public static extern uint GetShortPathName(
        string longPath,
        StringBuilder shortPath,
        uint shortPathLength);
}
'@
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Convert-ToCoreConsolePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $buffer = New-Object System.Text.StringBuilder 2048
    $length = [CadD2LNativePath]::GetShortPathName(
        $fullPath,
        $buffer,
        [uint32]$buffer.Capacity
    )
    if ($length -eq 0 -or $length -ge $buffer.Capacity) {
        throw "Unable to obtain an AutoCAD script-safe path for: $fullPath"
    }
    $scriptPath = $buffer.ToString()
    if ($scriptPath -match '[^\x20-\x7E]') {
        throw "AutoCAD script path is not ASCII-safe: $scriptPath"
    }
    return ($scriptPath -replace '\\', '/')
}

function Get-NewCoreConsoleProcessIds {
    param([int[]]$BeforeIds)
    $before = @{}
    foreach ($id in $BeforeIds) { $before[[int]$id] = $true }
    return @(
        Get-Process -Name 'accoreconsole' -ErrorAction SilentlyContinue |
            Where-Object { -not $before.ContainsKey([int]$_.Id) } |
            Select-Object -ExpandProperty Id
    )
}

$root = (Resolve-Path -LiteralPath $AutoCadRoot).Path
$pluginRoot = (Resolve-Path -LiteralPath $PluginDir).Path
$target = (Resolve-Path -LiteralPath $TargetDrawingPath).Path
$hostDrawing = (Resolve-Path -LiteralPath $HostDrawingPath).Path
$work = [System.IO.Path]::GetFullPath($WorkRoot)
if ($work -match '(?i)OneDrive') {
    throw 'WorkRoot must not be inside OneDrive.'
}
if ($target -eq $hostDrawing) {
    throw 'TargetDrawingPath and HostDrawingPath must be different files.'
}

$acadExe = Join-Path $root 'acad.exe'
$coreConsole = Join-Path $root 'accoreconsole.exe'
$plugin = Join-Path $pluginRoot (
    'CadDeepeningAssistance.AutoCADSideDatabaseIndexD2L.dll'
)
foreach ($required in @($acadExe, $coreConsole, $plugin)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required AutoCAD D2L file not found: $required"
    }
}

$acadVersion = (Get-Item -LiteralPath $acadExe).VersionInfo.FileVersion
$policy = Get-AutoCadHostPolicy -FileVersion $acadVersion
if ($null -eq $policy) {
    throw "Unsupported AutoCAD host: $acadVersion"
}
if ((Get-PortableExecutableArchitecture -Path $acadExe) -ne 'x64') {
    throw 'Only 64-bit AutoCAD hosts are supported.'
}

$targetDwgVersion = Get-DwgVersionCode -Path $target
$hostDwgVersion = Get-DwgVersionCode -Path $hostDrawing
foreach ($item in @(
        [pscustomobject]@{ name = 'target'; version = $targetDwgVersion },
        [pscustomobject]@{ name = 'host'; version = $hostDwgVersion }
    )) {
    $compatibility = Test-AutoCadDwgCompatibility `
        -DwgVersion $item.version `
        -Policy $policy
    if (-not $compatibility.compatible) {
        throw (
            "$($item.name) DWG is not compatible with AutoCAD " +
            "$($policy.release): $($compatibility.reason); " +
            "source=$($item.version); max=$($policy.max_dwg_version). " +
            'No conversion was performed.'
        )
    }
}

$outputDir = Join-Path $work 'output'
$jobDir = Join-Path $work 'job'
New-Item -ItemType Directory -Path $outputDir, $jobDir -Force | Out-Null
$stem = [System.IO.Path]::GetFileNameWithoutExtension($target)
$outputPath = Join-Path $outputDir (
    $stem + '.cad_side_database_index_d2l.json'
)
$phasePath = Join-Path $outputDir (
    $stem + '.cad_side_database_index_d2l.phase.json'
)
$executionPath = Join-Path $outputDir 'autocad-d2l-execution.json'
foreach ($path in @($outputPath, $phasePath, $executionPath)) {
    if (Test-Path -LiteralPath $path) {
        throw "Refusing to reuse or overwrite pre-existing D2L evidence: $path"
    }
}

$scriptPath = Join-Path $jobDir 'run-d2l.scr'
$stdoutPath = Join-Path $jobDir 'accoreconsole.stdout.log'
$stderrPath = Join-Path $jobDir 'accoreconsole.stderr.log'
$scriptLines = @(
    '_.FILEDIA',
    '0',
    '_.CMDDIA',
    '0',
    '_.NETLOAD',
    ('"' + (Convert-ToCoreConsolePath -Path $plugin) + '"'),
    'CADSIDEDBINDEXD2L',
    '_.QUIT',
    '_Y'
)
[System.IO.File]::WriteAllLines(
    $scriptPath,
    $scriptLines,
    [System.Text.Encoding]::ASCII
)

$targetHashBefore = Get-Sha256Hex -Path $target
$hostHashBefore = Get-Sha256Hex -Path $hostDrawing
$baselinePids = @(
    Get-Process -Name 'accoreconsole' -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Id
)
$started = Get-Date
$status = 'failed'
$message = ''
$terminatedPids = @()
$previous = [ordered]@{
    input = $env:CAD_D2L_SIDEDB_INPUT
    output = $env:CAD_D2L_SIDEDB_OUTPUT
    expand_block = $env:CAD_D2L_EXPAND_BLOCK_REGEX
    expand_root = $env:CAD_D2L_EXPAND_ROOT_HANDLE_REGEX
    explode_top = $env:CAD_D2L_EXPLODE_TOP_LEVEL_HANDLE_REGEX
    max_expanded = $env:CAD_D2L_MAX_EXPANDED_ENTITIES
}

try {
    $env:CAD_D2L_SIDEDB_INPUT = $target
    $env:CAD_D2L_SIDEDB_OUTPUT = $outputDir
    $env:CAD_D2L_EXPAND_BLOCK_REGEX = $ExpandBlockRegex
    $env:CAD_D2L_EXPAND_ROOT_HANDLE_REGEX = $ExpandRootHandleRegex
    $env:CAD_D2L_EXPLODE_TOP_LEVEL_HANDLE_REGEX = (
        $ExplodeTopLevelHandleRegex
    )
    $env:CAD_D2L_MAX_EXPANDED_ENTITIES = [string]$MaxExpandedEntities

    $arguments = @(
        '/i', ('"' + $hostDrawing + '"'),
        '/s', ('"' + $scriptPath + '"'),
        '/l', 'en-US'
    )
    $process = Start-Process `
        -FilePath $coreConsole `
        -ArgumentList $arguments `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $terminatedPids = @(Get-NewCoreConsoleProcessIds -BeforeIds $baselinePids)
        foreach ($id in $terminatedPids) {
            Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        }
        $status = 'timeout'
        $message = "AutoCAD Core Console exceeded ${TimeoutSeconds} seconds."
    }
    elseif ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $outputPath)) {
        $message = "exit_code=$($process.ExitCode); output_exists=$(Test-Path -LiteralPath $outputPath)"
    }
    else {
        $payload = Get-Content -LiteralPath $outputPath -Raw -Encoding UTF8 |
            ConvertFrom-Json
        $records = @($payload.records)
        $missingGateFields = @(
            $records | Where-Object {
                $null -eq $_.effective_visible -or
                $null -eq $_.effective_plottable -or
                (
                    $_.kind -eq 'text' -and
                    [string]::IsNullOrWhiteSpace([string]$_.rotation_space)
                )
            }
        ).Count
        if (
            $payload.schema_version -ne 'D2L-sidedb-2.2' -or
            [bool]$payload.truncated -or
            [bool]$payload.expanded_truncated -or
            [int]$payload.skipped_object_count -ne 0 -or
            $missingGateFields -ne 0
        ) {
            $status = 'evidence_gate_failed'
            $message = (
                "schema=$($payload.schema_version); truncated=$($payload.truncated); " +
                "expanded_truncated=$($payload.expanded_truncated); " +
                "skipped=$($payload.skipped_object_count); " +
                "missing_gate_fields=$missingGateFields"
            )
        }
        else {
            $status = 'success'
        }
    }
}
finally {
    $env:CAD_D2L_SIDEDB_INPUT = $previous.input
    $env:CAD_D2L_SIDEDB_OUTPUT = $previous.output
    $env:CAD_D2L_EXPAND_BLOCK_REGEX = $previous.expand_block
    $env:CAD_D2L_EXPAND_ROOT_HANDLE_REGEX = $previous.expand_root
    $env:CAD_D2L_EXPLODE_TOP_LEVEL_HANDLE_REGEX = $previous.explode_top
    $env:CAD_D2L_MAX_EXPANDED_ENTITIES = $previous.max_expanded
}

$targetHashAfter = Get-Sha256Hex -Path $target
$hostHashAfter = Get-Sha256Hex -Path $hostDrawing
if ($targetHashAfter -ne $targetHashBefore -or $hostHashAfter -ne $hostHashBefore) {
    $status = 'input_hash_changed'
    $message = 'A target or host analysis-copy DWG hash changed during execution.'
}

$execution = [pscustomobject][ordered]@{
    schema_version = 'cad-autocad-d2l-execution-1.0'
    status = $status
    message = $message
    backend = "autocad-$($policy.release)-dotnet-coreconsole-d2l"
    autocad_release = $policy.release
    autocad_api_version = $policy.api_version
    autocad_file_version = $acadVersion
    architecture = 'x64'
    source_dwg_version = $targetDwgVersion
    host_dwg_version = $hostDwgVersion
    max_dwg_version = $policy.max_dwg_version
    target_drawing = $target
    host_drawing = $hostDrawing
    output = $outputPath
    output_sha256 = if (Test-Path -LiteralPath $outputPath) {
        Get-Sha256Hex -Path $outputPath
    } else { $null }
    elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
    target_sha256_before = $targetHashBefore
    target_sha256_after = $targetHashAfter
    host_sha256_before = $hostHashBefore
    host_sha256_after = $hostHashAfter
    terminated_accoreconsole_pids = @($terminatedPids)
    stdout_log = $stdoutPath
    stderr_log = $stderrPath
}
$execution | ConvertTo-Json -Depth 6 |
    Set-Content -LiteralPath $executionPath -Encoding UTF8
$execution

if ($status -ne 'success') {
    throw "AutoCAD D2L extraction did not pass formal gates: $status; $message"
}
