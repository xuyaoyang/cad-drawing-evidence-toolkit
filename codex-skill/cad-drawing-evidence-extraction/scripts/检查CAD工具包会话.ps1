param(
    [Parameter(Mandatory = $true)]
    [string]$WorkRoot,
    [string]$OutputPath,
    [string]$InstalledSkillRoot,
    [string]$ToolkitRoot,
    [switch]$SkipCadDiscovery,
    [switch]$PassThru
)

$ErrorActionPreference = 'Stop'

function Get-FullPath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
}

function Test-UnsafeWorkRoot {
    param([string]$Path)
    $full = Get-FullPath $Path
    $root = [System.IO.Path]::GetPathRoot($full).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    if ($full.TrimEnd(':') -eq $root.TrimEnd(':')) { return $true }
    if ($full -match '(?i)(^|[\\/])OneDrive($|[\\/])') { return $true }
    return $false
}

function Get-RelativeFiles {
    param([string]$Root)
    $fullRoot = Get-FullPath $Root
    if (-not (Test-Path -LiteralPath $fullRoot -PathType Container)) { return @() }
    return @(
        Get-ChildItem -LiteralPath $fullRoot -Recurse -File |
            Where-Object {
                $_.FullName -notmatch '(?i)[\\/]__pycache__[\\/]' -and
                $_.Extension -notin @('.pyc', '.pyo')
            } |
            ForEach-Object { $_.FullName.Substring($fullRoot.Length + 1) } |
            Sort-Object
    )
}

function Compare-SkillTrees {
    param([string]$SourceRoot, [string]$TargetRoot)
    if (-not (Test-Path -LiteralPath $TargetRoot -PathType Container)) {
        return [pscustomobject][ordered]@{
            status = 'not_installed'; source_root = $SourceRoot; installed_root = $TargetRoot
            missing_count = 0; extra_count = 0; hash_mismatch_count = 0; differences = @()
        }
    }
    $sourceFiles = @(Get-RelativeFiles $SourceRoot)
    $targetFiles = @(Get-RelativeFiles $TargetRoot)
    $missing = @($sourceFiles | Where-Object { $_ -notin $targetFiles })
    $extra = @($targetFiles | Where-Object { $_ -notin $sourceFiles })
    $hashMismatch = New-Object 'System.Collections.Generic.List[string]'
    foreach ($relative in @($sourceFiles | Where-Object { $_ -in $targetFiles })) {
        $sourceHash = (Get-FileHash -LiteralPath (Join-Path $SourceRoot $relative) -Algorithm SHA256).Hash
        $targetHash = (Get-FileHash -LiteralPath (Join-Path $TargetRoot $relative) -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash) { $hashMismatch.Add($relative) }
    }
    $differences = @($missing + $extra + @($hashMismatch) | Sort-Object -Unique)
    return [pscustomobject][ordered]@{
        status = if ($differences.Count -eq 0) { 'in_sync' } else { 'drifted' }
        source_root = $SourceRoot; installed_root = $TargetRoot
        missing_count = $missing.Count; extra_count = $extra.Count
        hash_mismatch_count = $hashMismatch.Count; differences = $differences
    }
}

if ([string]::IsNullOrWhiteSpace($ToolkitRoot)) {
    $toolkitRoot = Get-FullPath (Split-Path -Parent $PSScriptRoot)
} else {
    $toolkitRoot = Get-FullPath $ToolkitRoot
}
$manifestPath = Join-Path $toolkitRoot 'MANIFEST.json'
$runContext = if (Test-Path -LiteralPath (Join-Path $toolkitRoot '.git') -PathType Container) {
    'repository'
} else { 'installed_skill' }

$requiredRelative = if ($runContext -eq 'repository') {
    @(
        'MANIFEST.json', 'AI_WORKFLOW.md', 'ENVIRONMENT.md', 'OUTPUT_CONTRACT.md',
        'SESSION_BOOTSTRAP.md', 'scripts\运行CAD只读自动后端.ps1',
        'scripts\运行CAD阻尼器数量核对V18.ps1', 'scripts\validate_cad_backend_route.py',
        'schemas\cad-backend-route.schema.json'
    )
} else {
    @(
        'MANIFEST.json', 'SKILL.md', 'references\evidence-contract.md',
        'references\environment-routing.md', 'references\session-bootstrap.md',
        'scripts\运行CAD只读自动后端.ps1', 'scripts\运行CAD阻尼器数量核对V18.ps1',
        'scripts\validate_cad_backend_route.py', 'schemas\cad-backend-route.schema.json'
    )
}

$requiredFiles = @(
    foreach ($relative in $requiredRelative) {
        $candidate = Join-Path $toolkitRoot $relative
        $exists = Test-Path -LiteralPath $candidate -PathType Leaf
        [pscustomobject][ordered]@{
            path = $relative
            exists = $exists
            sha256 = if ($exists) { (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash } else { $null }
        }
    }
)

$manifest = $null
$manifestError = $null
try {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
} catch { $manifestError = $_.Exception.Message }

$workRootFull = Get-FullPath $WorkRoot
$unsafeWorkRoot = Test-UnsafeWorkRoot $workRootFull
if ($unsafeWorkRoot) {
    throw "Unsafe WorkRoot. Use a non-root, non-OneDrive local directory: $workRootFull"
}
[System.IO.Directory]::CreateDirectory($workRootFull) | Out-Null
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $workRootFull 'cad-toolkit-session.json'
}
$outputFull = Get-FullPath $OutputPath
[System.IO.Directory]::CreateDirectory((Split-Path -Parent $outputFull)) | Out-Null

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonVersion = $null
if ($null -ne $pythonCommand) {
    try { $pythonVersion = (& $pythonCommand.Source --version 2>&1 | Out-String).Trim() } catch { $pythonVersion = $null }
}

$nativeBackends = @()
$discoveryError = $null
if (-not $SkipCadDiscovery) {
    $discoveryCandidates = @(
        (Join-Path $toolkitRoot 'zwcad\发现CAD安装.ps1'),
        (Join-Path $PSScriptRoot 'zwcad\发现CAD安装.ps1')
    )
    $discoveryScript = @($discoveryCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1)
    if ($discoveryScript.Count -eq 1) {
        try { $nativeBackends = @(& $discoveryScript[0] -Vendor Any) } catch { $discoveryError = $_.Exception.Message }
    } else { $discoveryError = 'CAD discovery script was not found.' }
}

if ([string]::IsNullOrWhiteSpace($InstalledSkillRoot)) {
    $InstalledSkillRoot = Join-Path $env:USERPROFILE '.codex\skills\cad-drawing-evidence-extraction'
}
$installedFull = Get-FullPath $InstalledSkillRoot
$sourceSkillRoot = if ($runContext -eq 'repository') {
    Join-Path $toolkitRoot 'codex-skill\cad-drawing-evidence-extraction'
} else { $toolkitRoot }
$skillSync = Compare-SkillTrees -SourceRoot $sourceSkillRoot -TargetRoot $installedFull

$missingCount = @($requiredFiles | Where-Object { -not $_.exists }).Count
$nativeReadyCount = @($nativeBackends | Where-Object { $_.current_toolkit_backend_ready }).Count
$blocked = ($missingCount -gt 0 -or $null -eq $manifest)
$warning = (-not $blocked -and (
    $skillSync.status -in @('drifted', 'not_installed') -or $nativeReadyCount -eq 0 -or
    $null -ne $discoveryError -or $null -eq $pythonCommand
))

$result = [pscustomobject][ordered]@{
    schema_version = 'cad-toolkit-session/1.0'
    overall_status = if ($blocked) { 'blocked' } elseif ($warning) { 'warning' } else { 'ready' }
    toolkit_name = if ($null -ne $manifest) { $manifest.name } else { $null }
    toolkit_version = if ($null -ne $manifest) { $manifest.version } else { $null }
    toolkit_root = $toolkitRoot
    execution_context = $runContext
    work_root = $workRootFull
    work_root_safe = $true
    output_path = $outputFull
    manifest_valid = $null -ne $manifest
    manifest_error = $manifestError
    required_file_missing_count = $missingCount
    required_files = $requiredFiles
    powershell = [pscustomobject][ordered]@{ edition = $PSVersionTable.PSEdition; version = $PSVersionTable.PSVersion.ToString() }
    python = [pscustomobject][ordered]@{
        available = $null -ne $pythonCommand
        executable = if ($null -ne $pythonCommand) { $pythonCommand.Source } else { $null }
        version = $pythonVersion
    }
    portable_candidate_ready = (
        (Test-Path -LiteralPath (Join-Path $toolkitRoot 'portable\ACadSharpPortableReader.cs') -PathType Leaf) -or
        (Test-Path -LiteralPath (Join-Path $toolkitRoot 'scripts\运行ACadSharp只读候选提取.ps1') -PathType Leaf)
    )
    native_backend_ready = $nativeReadyCount -gt 0
    native_backend_ready_count = $nativeReadyCount
    cad_discovery_skipped = [bool]$SkipCadDiscovery
    cad_discovery_error = $discoveryError
    native_backends = $nativeBackends
    skill_sync = $skillSync
    next_entry = if ($blocked) { $null } else { Join-Path $PSScriptRoot '运行CAD只读自动后端.ps1' }
    absence_proven = $false
}

$json = $result | ConvertTo-Json -Depth 12
[System.IO.File]::WriteAllText($outputFull, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
if ($PassThru) { $result } else { $outputFull }
