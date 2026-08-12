param(
    [Parameter(Mandatory = $true)]
    [string[]]$InputPath,

    [string]$ZwcadRoot,

    [string]$WorkRoot,

    [string]$PythonExe = 'python',

    [switch]$Visible,

    [switch]$SupplementLargeBlockFrames,

    [switch]$IncludeSupportingCandidates,

    [switch]$IncludeReferenceCandidates,

    [switch]$IncludeUncertainCandidates,

    [switch]$IncludeOlderRevisionCandidates,

    [switch]$IncludeExactDuplicateCandidates,

    [switch]$ContentScanOnly,

    [switch]$RouteOnly
)

$ErrorActionPreference = 'Stop'

function Test-PathSameOrChild {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $candidatePath = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    return (
        $candidatePath.Equals(
            $rootPath,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        $candidatePath.StartsWith(
            $rootPath + '\',
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
}

function Get-Sha256Hex {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    $stream = [System.IO.File]::OpenRead(
        [System.IO.Path]::GetFullPath($Path)
    )
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return (
            [System.BitConverter]::ToString(
                $algorithm.ComputeHash($stream)
            ).Replace('-', '')
        )
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Resolve-V18Component {
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )

    $projectRoot = Split-Path -Parent $PSScriptRoot
    $candidates = @(
        (Join-Path $PSScriptRoot $Name),
        (Join-Path (Join-Path $PSScriptRoot 'zwcad') $Name),
        (Join-Path (Join-Path $projectRoot 'zwcad') $Name),
        (Join-Path (Join-Path $projectRoot '脚本') $Name)
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "V18 component not found: $Name"
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Step
    )

    $previousErrorAction = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $PythonExe @Arguments 2>&1 | Out-Host
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($code -ne 0) {
        throw "Python step failed ($Step): $code"
    }
}

$v16Script = Resolve-V18Component '运行CAD阻尼器数量核对V16.ps1'
$analyzerScript = Resolve-V18Component '分析CAD目录内容指纹V18.py'
$v19Script = Resolve-V18Component '组织跨DWG阻尼器证据组V19.py'
$v12Script = Resolve-V18Component '跨视图阻尼器物理设备归一.py'
$buildScript = Resolve-V18Component 'build_zwcad_content_fingerprint_v18.ps1'
$batchScript = Resolve-V18Component '中望COM隔离批量只读导出V20.ps1'
$cadDiscoveryScript = Resolve-V18Component '发现CAD安装.ps1'
$projectRoot = Split-Path -Parent $PSScriptRoot
$sourceCandidates = @(
    (Join-Path $PSScriptRoot 'zwcad'),
    (Join-Path $projectRoot 'zwcad'),
    (Join-Path $projectRoot 'zwcad_text_exporter')
)
$exporterSourceDir = $null
foreach ($candidate in $sourceCandidates) {
    if (Test-Path -LiteralPath (
        Join-Path $candidate 'ZwcadContentFingerprintExporterV18.cs'
    ) -PathType Leaf) {
        $exporterSourceDir = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}
if ([string]::IsNullOrWhiteSpace($exporterSourceDir)) {
    throw 'V18 content-fingerprint exporter source directory not found.'
}

if ([string]::IsNullOrWhiteSpace($WorkRoot)) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $baseWorkRoot = $env:CAD_READING_WORK_ROOT
    if ([string]::IsNullOrWhiteSpace($baseWorkRoot)) {
        if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
            $baseWorkRoot = Join-Path $env:LOCALAPPDATA (
                'CadReadingToolkit\Work'
            )
        }
        else {
            $baseWorkRoot = Join-Path (
                [System.IO.Path]::GetTempPath()
            ) 'CadReadingToolkit\Work'
        }
    }
    $WorkRoot = Join-Path $baseWorkRoot "cad-v18-$stamp"
}
$WorkRoot = [System.IO.Path]::GetFullPath($WorkRoot)
$workRootDrive = [System.IO.Path]::GetPathRoot($WorkRoot)
if ($WorkRoot.TrimEnd('\').Equals(
        $workRootDrive.TrimEnd('\'),
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'V18 工作目录不能直接使用磁盘根目录。'
}
$synchronizedRoots = @(
    $env:OneDrive,
    $env:OneDriveConsumer,
    $env:OneDriveCommercial
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
foreach ($syncRoot in $synchronizedRoots) {
    if (Test-PathSameOrChild -Candidate $WorkRoot -Root $syncRoot) {
        throw "V18 工作目录不得位于同步目录：$syncRoot"
    }
}
if ($WorkRoot -match '(?i)(^|[\\/])OneDrive([\\/]|$)') {
    throw 'V18 工作目录不得位于 OneDrive 路径。'
}

$routeRoot = Join-Path $WorkRoot 'route'
$prescanRoot = Join-Path $WorkRoot 'prescan'
$prescanInput = Join-Path $prescanRoot 'input'
$prescanBuild = Join-Path $prescanRoot 'build'
$prescanExport = Join-Path $prescanRoot '输出'
$outputDir = Join-Path $WorkRoot 'output'
$fullRoot = Join-Path $WorkRoot 'full'
New-Item -ItemType Directory -Path `
    $routeRoot, $prescanInput, $prescanBuild, $outputDir -Force | Out-Null

$routeArguments = @{
    InputPath = $InputPath
    ZwcadRoot = $ZwcadRoot
    WorkRoot = $routeRoot
    PythonExe = $PythonExe
    RouteOnly = $true
}
foreach ($switchName in @(
        'IncludeSupportingCandidates',
        'IncludeReferenceCandidates',
        'IncludeUncertainCandidates',
        'IncludeOlderRevisionCandidates',
        'IncludeExactDuplicateCandidates'
    )) {
    if ((Get-Variable -Name $switchName -ValueOnly).IsPresent) {
        $routeArguments[$switchName] = $true
    }
}
& $v16Script @routeArguments

$routeManifest = Join-Path $routeRoot 'input_manifest.csv'
if (-not (Test-Path -LiteralPath $routeManifest -PathType Leaf)) {
    throw "V17 route manifest not found: $routeManifest"
}
if ($RouteOnly) {
    Write-Output 'V18 仅完成 V17 路由预检；未启动中望 CAD。'
    Write-Output "工作目录：$WorkRoot"
    return
}

if ([string]::IsNullOrWhiteSpace($ZwcadRoot)) {
    $cadCandidates = @(& $cadDiscoveryScript -Vendor ZWCAD -RequireCurrentBackend)
    $ZwcadRoot = [string]$cadCandidates[0].install_root
    Write-Output "已自动发现中望CAD：$ZwcadRoot"
}

$routeRows = @(Import-Csv -LiteralPath $routeManifest -Encoding utf8)
$scanCandidates = @(
    $routeRows |
        Where-Object {
            $_.route_status -in @('supporting', 'uncertain')
        } |
        Sort-Object @(
            @{ Expression = { [int]$_.route_priority }; Descending = $true },
            @{ Expression = { $_.source_path }; Descending = $false }
        )
)
$prescanRows = New-Object System.Collections.Generic.List[object]
$scanIndex = 0
foreach ($item in $scanCandidates) {
    $scanIndex++
    $safeStem = [System.IO.Path]::GetFileNameWithoutExtension($item.source_path)
    $safeStem = $safeStem -replace '[<>:"/\\|?*]', '_'
    if ($safeStem.Length -gt 80) {
        $safeStem = $safeStem.Substring(0, 80)
    }
    $copiedStem = ('P{0:D4}__{1}' -f $scanIndex, $safeStem)
    $copiedPath = Join-Path $prescanInput ($copiedStem + '.dwg')
    Copy-Item -LiteralPath $item.source_path -Destination $copiedPath -Force
    $sourceHash = Get-Sha256Hex -Path $item.source_path
    $copyHash = Get-Sha256Hex -Path $copiedPath
    if ($sourceHash -ne $copyHash) {
        throw "V18 DWG 副本哈希不一致：$($item.source_path)"
    }
    $prescanRows.Add([pscustomobject]@{
        source_path = $item.source_path
        copied_path = $copiedPath
        copied_stem = $copiedStem
        sha256 = $sourceHash
        route_status = $item.route_status
        route_class = $item.route_class
        profession = $item.profession
        drawing_role = $item.drawing_role
        route_reason = $item.route_reason
        route_evidence = $item.route_evidence
    })
}
$prescanManifest = Join-Path $prescanRoot 'prescan_manifest.csv'
if ($prescanRows.Count -gt 0) {
    $prescanRows |
        Export-Csv -LiteralPath $prescanManifest `
            -NoTypeInformation -Encoding UTF8
}
else {
    Set-Content -LiteralPath $prescanManifest -Encoding utf8 -Value (
        'source_path,copied_path,copied_stem,sha256,route_status,' +
        'route_class,profession,drawing_role,route_reason,route_evidence'
    )
}

$executionLog = Join-Path $prescanRoot 'content_scan_execution.csv'
if ($prescanRows.Count -gt 0) {
    & $buildScript `
        -ZwcadRoot $ZwcadRoot `
        -OutputDir $prescanBuild `
        -SourceDir $exporterSourceDir
    $pluginPath = Join-Path $prescanBuild (
        'CadReadingExploration.ZwcadContentFingerprintExporterV18.dll'
    )
    & $batchScript `
        -DrawingPath @($prescanRows.copied_path) `
        -ZwcadRoot $ZwcadRoot `
        -Mode ContentFingerprint `
        -PluginPath $pluginPath `
        -ExecutionLog $executionLog `
        -PerDrawingTimeoutSeconds 900 `
        -CommandTimeoutSeconds 600 `
        -Visible:$Visible
}
else {
    Set-Content -LiteralPath $executionLog -Encoding utf8 -Value (
        'drawing_path,status,message,elapsed_seconds'
    )
}

Invoke-Python -Arguments @(
    $analyzerScript,
    '--route-manifest', $routeManifest,
    '--prescan-manifest', $prescanManifest,
    '--fingerprint-dir', $prescanExport,
    '--execution-log', $executionLog,
    '--output-dir', $outputDir
) -Step 'V18-content-prescan'

if ($ContentScanOnly) {
    Write-Output 'V18 已完成目录内容复筛；按 ContentScanOnly 未启动完整六导出流程。'
    Write-Output "工作目录：$WorkRoot"
    Write-Output "内容复筛：$(Join-Path $outputDir 'V18目录内容复筛.md')"
    return
}

$decisionCsv = Join-Path $outputDir 'V18目录内容复筛.csv'
$decisionRows = @(Import-Csv -LiteralPath $decisionCsv -Encoding utf8)
$fullSourceMap = @{}
foreach ($item in $routeRows | Where-Object { $_.route_status -eq 'selected' }) {
    $fullSourceMap[$item.source_path.ToLowerInvariant()] = $item.source_path
}
foreach ($item in $decisionRows | Where-Object {
        $_.content_scan_decision -eq 'promoted_primary'
    }) {
    $fullSourceMap[$item.source_path.ToLowerInvariant()] = $item.source_path
}
$fullSources = @($fullSourceMap.Values | Sort-Object)

if ($fullSources.Count -gt 0) {
    $fullArguments = @{
        InputPath = $fullSources
        ZwcadRoot = $ZwcadRoot
        WorkRoot = $fullRoot
        PythonExe = $PythonExe
        Visible = [bool]$Visible
        SupplementLargeBlockFrames = [bool]$SupplementLargeBlockFrames
    }
    & $v16Script @fullArguments
    $fullSummary = Join-Path $fullRoot 'output\V16运行汇总.csv'
    if (Test-Path -LiteralPath $fullSummary -PathType Leaf) {
        Invoke-Python -Arguments @(
            $analyzerScript,
            '--route-manifest', $routeManifest,
            '--prescan-manifest', $prescanManifest,
            '--fingerprint-dir', $prescanExport,
            '--execution-log', $executionLog,
            '--output-dir', $outputDir,
            '--full-summary', $fullSummary
        ) -Step 'V18-final-summary'
        Invoke-Python -Arguments @(
            $v19Script,
            '--v18-report', $decisionCsv,
            '--v16-root', $fullRoot,
            '--v12-script', $v12Script,
            '--output-dir', (Join-Path $WorkRoot 'v19')
        ) -Step 'V19-cross-drawing-groups'
    }
}

Write-Output 'V18 完成。'
Write-Output "工作目录：$WorkRoot"
Write-Output "内容复筛：$(Join-Path $outputDir 'V18目录内容复筛.md')"
if (Test-Path -LiteralPath (Join-Path $fullRoot 'output\V16运行汇总.md')) {
    Write-Output "完整数量流程：$(Join-Path $fullRoot 'output\V16运行汇总.md')"
}
if (Test-Path -LiteralPath (Join-Path $WorkRoot 'v19\V19跨DWG证据组.md')) {
    Write-Output "跨DWG证据组：$(Join-Path $WorkRoot 'v19\V19跨DWG证据组.md')"
}





