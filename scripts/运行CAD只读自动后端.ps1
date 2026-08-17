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
        [string[]]$Reasons,
        [string]$HostRelease,
        [string]$HostApiVersion,
        [string]$HostFileVersion,
        [string]$MaxDwgVersion
    )
    $Attempts.Add([pscustomobject]@{
        backend = $Backend
        status = $Status
        message = $Message
        output = $Output
        reasons = @($Reasons)
        host_release = if ([string]::IsNullOrWhiteSpace($HostRelease)) { $null } else { $HostRelease }
        host_api_version = if ([string]::IsNullOrWhiteSpace($HostApiVersion)) { $null } else { $HostApiVersion }
        host_file_version = if ([string]::IsNullOrWhiteSpace($HostFileVersion)) { $null } else { $HostFileVersion }
        max_dwg_version = if ([string]::IsNullOrWhiteSpace($MaxDwgVersion)) { $null } else { $MaxDwgVersion }
    })
}

function Get-ExplicitAutoCadCandidate {
    param([Parameter(Mandatory = $true)][string]$Root)

    $resolved = (Resolve-Path -LiteralPath $Root).Path
    $acadExe = Join-Path $resolved 'acad.exe'
    $coreConsole = Join-Path $resolved 'accoreconsole.exe'
    foreach ($required in @(
            $acadExe,
            $coreConsole,
            (Join-Path $resolved 'AcCoreMgd.dll'),
            (Join-Path $resolved 'AcDbMgd.dll'),
            (Join-Path $resolved 'AcMgd.dll')
        )) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "AutoCAD host file not found: $required"
        }
    }
    $fileVersion = (Get-Item -LiteralPath $acadExe).VersionInfo.FileVersion
    $policy = Get-AutoCadHostPolicy -FileVersion $fileVersion
    if ($null -eq $policy) {
        throw "Unsupported AutoCAD host version: $fileVersion"
    }
    $architecture = Get-PortableExecutableArchitecture -Path $acadExe
    if ($architecture -ne 'x64') {
        throw "Only 64-bit AutoCAD hosts are supported: $architecture"
    }
    return [pscustomobject][ordered]@{
        install_root = $resolved
        version = $fileVersion
        architecture = $architecture
        product_release = $policy.release
        api_version = $policy.api_version
        backend_key = $policy.backend_key
        max_dwg_version = $policy.max_dwg_version
        policy_priority = $policy.priority
        current_toolkit_backend_ready = $true
    }
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
$autoCadPolicy = Join-Path $autoCadComponentRoot 'AutoCADVersionPolicy.ps1'
if (-not (Test-Path -LiteralPath $autoCadPolicy -PathType Leaf)) {
    throw "AutoCAD version policy not found: $autoCadPolicy"
}
. $autoCadPolicy
$sourceDwgVersion = Get-DwgVersionCode -Path $InputPath
$discoveryScript = Join-Path $zwcadComponentRoot '发现CAD安装.ps1'
$zwcadBuilder = Join-Path $zwcadComponentRoot 'build_zwcad_exporters_v16.ps1'
$zwcadRunner = Join-Path $zwcadComponentRoot '中望COM隔离批量只读导出V20.ps1'
$autoCadBuilder = Join-Path $autoCadComponentRoot 'build_autocad_exporters.ps1'
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
    $autoCadCandidates = @()
    $autoCadDiscoveryMessage = $null
    if ([string]::IsNullOrWhiteSpace($AutoCadRoot)) {
        try {
            $autoCadCandidates = @(& $discoveryScript -Vendor AutoCAD) |
                Where-Object { $_.current_toolkit_backend_ready } |
                Sort-Object @{ Expression = 'policy_priority'; Descending = $true }
        }
        catch {
            $autoCadDiscoveryMessage = $_.Exception.Message
        }
    }
    else {
        try {
            $autoCadCandidates = @(
                Get-ExplicitAutoCadCandidate -Root $AutoCadRoot
            )
        }
        catch {
            $autoCadDiscoveryMessage = $_.Exception.Message
        }
    }

    if ($autoCadCandidates.Count -eq 0) {
        if ([string]::IsNullOrWhiteSpace($autoCadDiscoveryMessage)) {
            $autoCadDiscoveryMessage = (
                'No supported 64-bit AutoCAD 2023, 2020, 2018, or 2014 ' +
                'managed/Core Console host was found.'
            )
        }
        foreach ($policy in @(Get-SupportedAutoCadPolicies)) {
            Add-Attempt -Attempts $attempts -Backend $policy.backend_key `
                -Status 'not_available' -Message $autoCadDiscoveryMessage `
                -Reasons @("autocad_$($policy.release)_not_available") `
                -HostRelease $policy.release -HostApiVersion $policy.api_version `
                -MaxDwgVersion $policy.max_dwg_version
        }
    }

    foreach ($candidate in $autoCadCandidates) {
        if ($null -ne $selectedBackend) { break }
        $policy = Get-AutoCadHostPolicy -FileVersion ([string]$candidate.version)
        if ($null -eq $policy) { continue }
        $compatibility = Test-AutoCadDwgCompatibility `
            -DwgVersion $sourceDwgVersion -Policy $policy
        if (-not $compatibility.compatible) {
            Add-Attempt -Attempts $attempts -Backend $policy.backend_key `
                -Status 'incompatible' `
                -Message (
                    "$($policy.product_name) accepts through $($policy.max_dwg_version); " +
                    "source is $sourceDwgVersion. No conversion was performed."
                ) `
                -Reasons @($compatibility.reason) `
                -HostRelease $policy.release -HostApiVersion $policy.api_version `
                -HostFileVersion ([string]$candidate.version) `
                -MaxDwgVersion $policy.max_dwg_version
            continue
        }

        try {
            $autoCadRunRoot = Join-Path $runRoot ("03-autocad-$($policy.release)")
            $autoCadInput = Join-Path $autoCadRunRoot '输入'
            $autoCadOutput = Join-Path $autoCadRunRoot '输出'
            $autoCadBuild = Join-Path $autoCadRunRoot 'build'
            New-Item -ItemType Directory -Path $autoCadInput, $autoCadOutput, $autoCadBuild -Force |
                Out-Null
            $autoCadCopy = Join-Path $autoCadInput ([System.IO.Path]::GetFileName($InputPath))
            Copy-Item -LiteralPath $InputPath -Destination $autoCadCopy -Force
            & $autoCadBuilder -AutoCadRoot $candidate.install_root `
                -OutputDir $autoCadBuild -SourceDir $zwcadComponentRoot `
                -RequiredRelease $policy.release |
                Out-Null
            $autoCadLog = Join-Path $autoCadRunRoot 'execution.csv'
            $autoCadExecution = & $autoCadRunner -DrawingPath $autoCadCopy `
                -AutoCadRoot $candidate.install_root -Mode Full `
                -PluginDir $autoCadBuild -ExecutionLog $autoCadLog `
                -PerDrawingTimeoutSeconds $NativeTimeoutSeconds |
                Select-Object -First 1
            if ($autoCadExecution.status -ne 'success') {
                throw (
                    "$($policy.product_name) execution status: $($autoCadExecution.status) " +
                    $autoCadExecution.message
                )
            }
            $missingAutoCadOutputs = Get-MissingNativeFullOutputs `
                -OutputDirectory $autoCadOutput `
                -DrawingPath $autoCadCopy
            if ($missingAutoCadOutputs.Count -gt 0) {
                throw (
                    "$($policy.product_name) reported success but required evidence outputs are missing: " +
                    ($missingAutoCadOutputs -join '|')
                )
            }
            Add-Attempt -Attempts $attempts -Backend $policy.backend_key -Status 'success' `
                -Message "$($policy.product_name) Core Console fallback completed." `
                -Output $autoCadOutput -Reasons @() `
                -HostRelease $policy.release -HostApiVersion $policy.api_version `
                -HostFileVersion ([string]$candidate.version) `
                -MaxDwgVersion $policy.max_dwg_version
            $selectedBackend = $policy.backend_key
            $selectedOutput = $autoCadOutput
            $routeStatus = $policy.route_status
        }
        catch {
            Add-Attempt -Attempts $attempts -Backend $policy.backend_key -Status 'failed' `
                -Message $_.Exception.Message `
                -Reasons @("autocad_$($policy.release)_execution_failed") `
                -HostRelease $policy.release -HostApiVersion $policy.api_version `
                -HostFileVersion ([string]$candidate.version) `
                -MaxDwgVersion $policy.max_dwg_version
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
    schema_version = 'cad-backend-route/0.2'
    route_order = @(
        'ACadSharp',
        'ZWCAD',
        'AutoCAD2023',
        'AutoCAD2020',
        'AutoCAD2018',
        'AutoCAD2014'
    )
    status = $routeStatus
    selected_backend = $selectedBackend
    selected_output = $selectedOutput
    source_name = [System.IO.Path]::GetFileName($InputPath)
    source_dwg_version = $sourceDwgVersion
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
