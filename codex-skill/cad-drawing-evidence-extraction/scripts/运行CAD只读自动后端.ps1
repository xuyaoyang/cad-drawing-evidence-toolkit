param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [string]$WorkRoot,

    [string]$ZwcadRoot,

    [string]$AutoCadRoot,

    [int]$PortableTimeoutSeconds = 300,

    [int]$NativeTimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Test-SynchronizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full -match '(?i)[\\/]OneDrive([\\/]|$)') { return $true }
    foreach ($name in @('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')) {
        $root = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        $normalized = [System.IO.Path]::GetFullPath($root).TrimEnd('\')
        if (
            $full.Equals($normalized, [System.StringComparison]::OrdinalIgnoreCase) -or
            $full.StartsWith($normalized + '\', [System.StringComparison]::OrdinalIgnoreCase)
        ) { return $true }
    }
    return $false
}

function Get-CountFromEntityList {
    param($Values)
    $total = 0L
    foreach ($value in @($Values)) {
        if ($null -ne $value.count) { $total += [long]$value.count }
    }
    return $total
}

function Get-MissingNativeFullOutputs {
    param(
        [Parameter(Mandatory = $true)][string]$OutputDirectory,
        [Parameter(Mandatory = $true)][string]$DrawingPath
    )
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($DrawingPath)
    $suffixes = @(
        '.cad_text_export_v5.json',
        '.cad_frame_export_v5.json',
        '.cad_symbol_export_v6.json',
        '.cad_oriented_text_export_v7.json',
        '.cad_primitive_export_v10.json',
        '.cad_visibility_export_v13.json'
    )
    return @(
        foreach ($suffix in $suffixes) {
            $path = Join-Path $OutputDirectory ($stem + $suffix)
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                $path
            }
        }
    )
}

function Get-PortableFallbackReasons {
    param(
        [Parameter(Mandatory = $true)]$RunRecord,
        [Parameter(Mandatory = $true)]$Evidence
    )

    $reasons = New-Object System.Collections.Generic.List[string]
    if (-not [bool]$RunRecord.source_unchanged) {
        $reasons.Add('portable_source_hash_not_closed')
    }
    if ($RunRecord.status -ne 'portable_readonly_candidate_ready_for_comparison') {
        $reasons.Add('portable_status_unresolved')
    }
    if ($Evidence.metadata.backend -ne 'ACadSharp') {
        $reasons.Add('portable_backend_identity_invalid')
    }
    if ([long]$Evidence.summary.traversal_issue_count -gt 0) {
        $reasons.Add('portable_traversal_issue')
    }
    if ((Get-CountFromEntityList -Values $Evidence.summary.unsupported_reachable_type_counts) -gt 0) {
        $reasons.Add('portable_unsupported_reachable_entity')
    }
    if ([long]$Evidence.summary.non_uniform_insert_count -gt 0) {
        $reasons.Add('portable_non_uniform_insert')
    }
    if ([long]$Evidence.summary.multiple_insert_count -gt 0) {
        $reasons.Add('portable_minsert_not_expanded')
    }
    if ([long]$Evidence.summary.evidence_missing_handle_count -gt 0) {
        $reasons.Add('portable_missing_handle')
    }
    if ([long]$Evidence.summary.cycle_stop_count -gt 0) {
        $reasons.Add('portable_cycle_stop')
    }
    if ([long]$Evidence.summary.depth_stop_count -gt 0) {
        $reasons.Add('portable_depth_stop')
    }
    foreach ($value in @($Evidence.summary.raw_entity_type_counts)) {
        if ($value.entity_type -eq 'VIEWPORT' -and [long]$value.count -gt 0) {
            $reasons.Add('portable_layout_viewport_unverified')
            break
        }
    }
    return @($reasons | Sort-Object -Unique)
}

function Add-Attempt {
    param(
        [Parameter(Mandatory = $true)]$Attempts,
        [Parameter(Mandatory = $true)][string]$Backend,
        [Parameter(Mandatory = $true)][string]$Status,
        [string]$Message,
        [string]$Output,
        [string[]]$Reasons
    )
    $Attempts.Add([pscustomobject]@{
        backend = $Backend
        status = $Status
        message = $Message
        output = $Output
        reasons = @($Reasons)
    })
}

$InputPath = (Resolve-Path -LiteralPath $InputPath).Path
if (-not [System.IO.Path]::GetExtension($InputPath).Equals(
        '.dwg',
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Only DWG input is supported: $InputPath"
}
if ([string]::IsNullOrWhiteSpace($WorkRoot)) {
    $WorkRoot = Join-Path $env:LOCALAPPDATA 'CadReadingToolkit\Work\auto-backend'
}
$WorkRoot = [System.IO.Path]::GetFullPath($WorkRoot)
if (Test-SynchronizedPath -Path $WorkRoot) {
    throw "WorkRoot cannot be in synchronized storage: $WorkRoot"
}
if ([System.IO.Path]::GetPathRoot($WorkRoot).TrimEnd('\') -eq $WorkRoot.TrimEnd('\')) {
    throw "WorkRoot cannot be a drive root: $WorkRoot"
}

$sourceHashBefore = Get-Sha256Hex -Path $InputPath
$runName = (
    (Get-Date).ToString('yyyyMMdd-HHmmss') + '-' +
    $sourceHashBefore.Substring(0, 12)
)
$runRoot = Join-Path $WorkRoot $runName
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$routeRecordPath = Join-Path $runRoot 'cad-backend-route.json'
$attempts = New-Object System.Collections.Generic.List[object]
$selectedBackend = $null
$selectedOutput = $null
$routeStatus = 'manual_review_required_no_backend'
$portableReasons = @('portable_not_run')

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$portableRunner = Join-Path $PSScriptRoot '运行ACadSharp只读候选提取.ps1'
$zwcadComponentRoot = Join-Path $repositoryRoot 'zwcad'
if (-not (Test-Path -LiteralPath $zwcadComponentRoot -PathType Container)) {
    $zwcadComponentRoot = Join-Path $PSScriptRoot 'zwcad'
}
$autoCadComponentRoot = Join-Path $repositoryRoot 'autocad'
if (-not (Test-Path -LiteralPath $autoCadComponentRoot -PathType Container)) {
    $autoCadComponentRoot = Join-Path $PSScriptRoot 'autocad'
}
$discoveryScript = Join-Path $zwcadComponentRoot '发现CAD安装.ps1'
$zwcadBuilder = Join-Path $zwcadComponentRoot 'build_zwcad_exporters_v16.ps1'
$zwcadRunner = Join-Path $zwcadComponentRoot '中望COM隔离批量只读导出V20.ps1'
$autoCadBuilder = Join-Path $autoCadComponentRoot 'build_autocad_2023_exporters.ps1'
$autoCadRunner = Join-Path $autoCadComponentRoot 'AutoCADCoreConsole只读导出.ps1'

try {
    $portableRoot = Join-Path $runRoot '01-acadsharp'
    $portableResult = & $portableRunner `
        -InputPath $InputPath `
        -WorkRoot $portableRoot `
        -BuildRoot (Join-Path $runRoot '01-acadsharp-build') `
        -TimeoutSeconds $PortableTimeoutSeconds
    $portableRun = Get-Content -LiteralPath $portableResult.RunRecord -Encoding UTF8 -Raw |
        ConvertFrom-Json
    $portableEvidence = Get-Content -LiteralPath $portableResult.Evidence -Encoding UTF8 -Raw |
        ConvertFrom-Json
    $portableReasons = Get-PortableFallbackReasons `
        -RunRecord $portableRun `
        -Evidence $portableEvidence
    if ($portableReasons.Count -eq 0) {
        Add-Attempt -Attempts $attempts -Backend 'ACadSharp' -Status 'success' `
            -Message 'Portable candidate completed without a native-fallback trigger.' `
            -Output $portableResult.Evidence -Reasons @()
        $selectedBackend = 'ACadSharp'
        $selectedOutput = $portableResult.Evidence
        $routeStatus = 'portable_candidate_selected'
    }
    else {
        Add-Attempt -Attempts $attempts -Backend 'ACadSharp' -Status 'fallback_required' `
            -Message 'Portable output is retained, but critical evidence remains unresolved.' `
            -Output $portableResult.Evidence -Reasons $portableReasons
    }
}
catch {
    $portableReasons = @('portable_execution_failed')
    Add-Attempt -Attempts $attempts -Backend 'ACadSharp' -Status 'failed' `
        -Message $_.Exception.Message -Reasons $portableReasons
}

if ($null -eq $selectedBackend) {
    $resolvedZwcadRoot = $ZwcadRoot
    if ([string]::IsNullOrWhiteSpace($resolvedZwcadRoot)) {
        try {
            $candidate = @(& $discoveryScript -Vendor ZWCAD -RequireCurrentBackend) |
                Select-Object -First 1
            $resolvedZwcadRoot = $candidate.install_root
        }
        catch {
            Add-Attempt -Attempts $attempts -Backend 'ZWCAD' -Status 'not_available' `
                -Message $_.Exception.Message -Reasons @('zwcad_not_available')
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($resolvedZwcadRoot)) {
        try {
            $zwcadRoot = Join-Path $runRoot '02-zwcad'
            $zwcadInput = Join-Path $zwcadRoot '输入'
            $zwcadOutput = Join-Path $zwcadRoot '输出'
            $zwcadBuild = Join-Path $zwcadRoot 'build'
            New-Item -ItemType Directory -Path $zwcadInput, $zwcadOutput, $zwcadBuild -Force |
                Out-Null
            $zwcadCopy = Join-Path $zwcadInput ([System.IO.Path]::GetFileName($InputPath))
            Copy-Item -LiteralPath $InputPath -Destination $zwcadCopy -Force
            $copyHashBefore = Get-Sha256Hex -Path $zwcadCopy
            & $zwcadBuilder -ZwcadRoot $resolvedZwcadRoot -OutputDir $zwcadBuild `
                -SourceDir $zwcadComponentRoot | Out-Null
            $zwcadLog = Join-Path $zwcadRoot 'execution.csv'
            & $zwcadRunner -DrawingPath $zwcadCopy -ZwcadRoot $resolvedZwcadRoot `
                -Mode Full -PluginDir $zwcadBuild -ExecutionLog $zwcadLog `
                -PerDrawingTimeoutSeconds $NativeTimeoutSeconds `
                -CommandTimeoutSeconds ([math]::Min($NativeTimeoutSeconds, 600)) |
                Out-Null
            $execution = Import-Csv -LiteralPath $zwcadLog | Select-Object -First 1
            $copyHashAfter = Get-Sha256Hex -Path $zwcadCopy
            if ($execution.status -ne 'success') {
                throw "ZWCAD execution status: $($execution.status) $($execution.message)"
            }
            if ($copyHashBefore -ne $copyHashAfter) {
                throw 'ZWCAD analysis-copy hash changed.'
            }
            $missingZwcadOutputs = Get-MissingNativeFullOutputs `
                -OutputDirectory $zwcadOutput `
                -DrawingPath $zwcadCopy
            if ($missingZwcadOutputs.Count -gt 0) {
                throw (
                    'ZWCAD reported success but required evidence outputs are missing: ' +
                    ($missingZwcadOutputs -join '|')
                )
            }
            Add-Attempt -Attempts $attempts -Backend 'ZWCAD' -Status 'success' `
                -Message 'Validated ZWCAD native fallback completed.' `
                -Output $zwcadOutput -Reasons @()
            $selectedBackend = 'ZWCAD'
            $selectedOutput = $zwcadOutput
            $routeStatus = 'zwcad_native_fallback_selected'
        }
        catch {
            Add-Attempt -Attempts $attempts -Backend 'ZWCAD' -Status 'failed' `
                -Message $_.Exception.Message -Reasons @('zwcad_execution_failed')
        }
    }
}

if ($null -eq $selectedBackend) {
    $resolvedAutoCadRoot = $AutoCadRoot
    if ([string]::IsNullOrWhiteSpace($resolvedAutoCadRoot)) {
        try {
            $candidate = @(& $discoveryScript -Vendor AutoCAD) |
                Where-Object {
                    $_.host_api_ready -and $_.version -match '^R?24\.2(?:\.|$)'
                } |
                Select-Object -First 1
            if ($null -ne $candidate) {
                $resolvedAutoCadRoot = $candidate.install_root
            }
        }
        catch {
        }
    }

    if ([string]::IsNullOrWhiteSpace($resolvedAutoCadRoot)) {
        Add-Attempt -Attempts $attempts -Backend 'AutoCAD2023' -Status 'not_available' `
            -Message 'No AutoCAD 2023 (R24.2) managed/Core Console host was found.' `
            -Reasons @('autocad_2023_not_available')
    }
    else {
        try {
            $autoCadRunRoot = Join-Path $runRoot '03-autocad-2023'
            $autoCadInput = Join-Path $autoCadRunRoot '输入'
            $autoCadOutput = Join-Path $autoCadRunRoot '输出'
            $autoCadBuild = Join-Path $autoCadRunRoot 'build'
            New-Item -ItemType Directory -Path $autoCadInput, $autoCadOutput, $autoCadBuild -Force |
                Out-Null
            $autoCadCopy = Join-Path $autoCadInput ([System.IO.Path]::GetFileName($InputPath))
            Copy-Item -LiteralPath $InputPath -Destination $autoCadCopy -Force
            & $autoCadBuilder -AutoCadRoot $resolvedAutoCadRoot `
                -OutputDir $autoCadBuild -SourceDir $zwcadComponentRoot |
                Out-Null
            $autoCadLog = Join-Path $autoCadRunRoot 'execution.csv'
            $autoCadExecution = & $autoCadRunner -DrawingPath $autoCadCopy `
                -AutoCadRoot $resolvedAutoCadRoot -Mode Full `
                -PluginDir $autoCadBuild -ExecutionLog $autoCadLog `
                -PerDrawingTimeoutSeconds $NativeTimeoutSeconds |
                Select-Object -First 1
            if ($autoCadExecution.status -ne 'success') {
                throw (
                    "AutoCAD 2023 execution status: $($autoCadExecution.status) " +
                    $autoCadExecution.message
                )
            }
            $missingAutoCadOutputs = Get-MissingNativeFullOutputs `
                -OutputDirectory $autoCadOutput `
                -DrawingPath $autoCadCopy
            if ($missingAutoCadOutputs.Count -gt 0) {
                throw (
                    'AutoCAD 2023 reported success but required evidence outputs are missing: ' +
                    ($missingAutoCadOutputs -join '|')
                )
            }
            Add-Attempt -Attempts $attempts -Backend 'AutoCAD2023' -Status 'success' `
                -Message 'AutoCAD 2023 Core Console fallback completed.' `
                -Output $autoCadOutput -Reasons @()
            $selectedBackend = 'AutoCAD2023'
            $selectedOutput = $autoCadOutput
            $routeStatus = 'autocad_2023_native_fallback_selected'
        }
        catch {
            Add-Attempt -Attempts $attempts -Backend 'AutoCAD2023' -Status 'failed' `
                -Message $_.Exception.Message -Reasons @('autocad_2023_execution_failed')
        }
    }
}

$sourceHashAfter = Get-Sha256Hex -Path $InputPath
if ($sourceHashAfter -ne $sourceHashBefore) {
    $routeStatus = 'source_hash_changed_safe_stop'
    $selectedBackend = $null
    $selectedOutput = $null
}

$routeRecord = [ordered]@{
    schema_version = 'cad-backend-route/0.1'
    route_order = @('ACadSharp', 'ZWCAD', 'AutoCAD2023')
    status = $routeStatus
    selected_backend = $selectedBackend
    selected_output = $selectedOutput
    source_name = [System.IO.Path]::GetFileName($InputPath)
    source_sha256_before = $sourceHashBefore
    source_sha256_after = $sourceHashAfter
    source_unchanged = ($sourceHashBefore -eq $sourceHashAfter)
    portable_fallback_reasons = $portableReasons
    absence_proven = $false
    attempts = $attempts.ToArray()
}
$routeRecord |
    ConvertTo-Json -Depth 10 |
    Set-Content -LiteralPath $routeRecordPath -Encoding UTF8

[pscustomobject]@{
    Status = $routeStatus
    SelectedBackend = $selectedBackend
    SelectedOutput = $selectedOutput
    RouteRecord = $routeRecordPath
    RunRoot = $runRoot
}
