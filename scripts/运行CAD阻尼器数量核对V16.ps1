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

    [switch]$RouteOnly
)

$ErrorActionPreference = 'Stop'

function Resolve-DrawingRoute {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][bool]$ExplicitFile,
        [bool]$TopLevelDirectoryFile = $false,
        [string]$RevisionDate = ''
    )

    if ($ExplicitFile) {
        return [pscustomobject]@{
            RouteClass = 'primary'
            Profession = 'explicit'
            DrawingRole = 'explicit_dwg'
            Priority = 100
            Reason = 'explicit_dwg_input'
            Evidence = 'user_explicit_file'
        }
    }

    $full = [System.IO.Path]::GetFullPath($Path)
    $fileName = [System.IO.Path]::GetFileName($full)
    $structural = $full -match (
        '(?i)(^|[\\/])(结构|结施|结构专业|structural)([\\/]|$)' +
        '|结构|结施|structural'
    )
    $otherProfessionPattern = (
        '建筑|建施|暖通|给排水|水施|电气|电施|弱电|消防|' +
        '景观|装修|幕墙|通风|空调|强电|配电|照明|防雷|' +
        '接地|智能化|通信|火灾报警|广播|绿建|节能|喷淋'
    )
    $otherProfession = $full -match $otherProfessionPattern
    $fileExplicitOther = $fileName -match $otherProfessionPattern
    $devicePattern = (
        '阻尼器|消能器|消能减震|减震布置|隔震布置|隔震支座|' +
        '黏滞|粘滞|屈曲约束|防屈曲|BRB|VFD|墙板阻尼|' +
        '^\s*[\(（]?(QB|XNQB)[\)）]?'
    )
    $referencePattern = (
        '图纸目录|目录|总说明|设计说明|结构说明|计算书|审查回复|' +
        '审图回复'
    )
    $supportingPattern = (
        '基础|桩位|承台|楼梯|梁平法|梁施工|梁配筋|柱平法|' +
        '柱施工|柱配筋|剪力墙|墙施工|墙配筋|板配筋|叠合板|' +
        '空心楼盖|肋梁|节点|详图|大样|雨棚|连廊|预制构件|' +
        '墙柱|门刚|网架'
    )
    $primaryPattern = (
        '结构平面|结构布置|楼层结构|屋面结构|结构施工图|' +
        '结构图|结构.*图|消能.*布置|减震.*布置|隔震.*布置|平面'
    )

    if ($fileExplicitOther) {
        return [pscustomobject]@{
            RouteClass = 'excluded'
            Profession = 'non_structural'
            DrawingRole = 'other_profession'
            Priority = 0
            Reason = 'explicit_non_structural_profession'
            Evidence = $fileName
        }
    }
    if ($otherProfession -and -not $structural) {
        return [pscustomobject]@{
            RouteClass = 'excluded'
            Profession = 'non_structural'
            DrawingRole = 'other_profession'
            Priority = 0
            Reason = 'non_structural_profession_path'
            Evidence = 'path_profession'
        }
    }
    if ($fileName -match $devicePattern) {
        return [pscustomobject]@{
            RouteClass = 'primary'
            Profession = 'structural_or_device'
            DrawingRole = 'damper_or_isolation_drawing'
            Priority = 95
            Reason = 'device_specific_filename'
            Evidence = $Matches[0]
        }
    }
    if ($fileName -match $referencePattern) {
        return [pscustomobject]@{
            RouteClass = 'reference'
            Profession = $(if ($structural) { 'structural' } else { 'uncertain' })
            DrawingRole = 'catalog_or_general_notes'
            Priority = 30
            Reason = 'reference_drawing_filename'
            Evidence = $Matches[0]
        }
    }
    if ($fileName -match $supportingPattern) {
        return [pscustomobject]@{
            RouteClass = 'supporting'
            Profession = $(if ($structural) { 'structural' } else { 'uncertain' })
            DrawingRole = 'structural_supporting_view'
            Priority = 55
            Reason = 'supporting_view_filename'
            Evidence = $Matches[0]
        }
    }
    if ($fileName -match $primaryPattern) {
        return [pscustomobject]@{
            RouteClass = 'primary'
            Profession = 'structural'
            DrawingRole = 'primary_structure_or_layout'
            Priority = 85
            Reason = 'primary_structure_filename'
            Evidence = $Matches[0]
        }
    }
    if (
        $TopLevelDirectoryFile -and
        -not [string]::IsNullOrWhiteSpace($RevisionDate)
    ) {
        return [pscustomobject]@{
            RouteClass = 'primary'
            Profession = 'project_package_candidate'
            DrawingRole = 'top_level_dated_project_package'
            Priority = 70
            Reason = 'top_level_dated_project_package'
            Evidence = $RevisionDate
        }
    }
    if ($structural -and $fileName -match '施工图|深化图|深化设计|结施') {
        return [pscustomobject]@{
            RouteClass = 'primary'
            Profession = 'structural'
            DrawingRole = 'structural_package'
            Priority = 75
            Reason = 'generic_structural_package_filename'
            Evidence = $Matches[0]
        }
    }
    if ($structural) {
        return [pscustomobject]@{
            RouteClass = 'uncertain'
            Profession = 'structural'
            DrawingRole = 'unclassified_structural_drawing'
            Priority = 40
            Reason = 'structural_profession_role_unproven'
            Evidence = 'structural_path_or_filename'
        }
    }
    return [pscustomobject]@{
        RouteClass = 'uncertain'
        Profession = 'uncertain'
        DrawingRole = 'unclassified'
        Priority = 20
        Reason = 'profession_not_proven'
        Evidence = ''
    }
}

function Resolve-PipelineSelection {
    param(
        [Parameter(Mandatory = $true)][string]$RouteClass
    )

    if ($RouteClass -eq 'primary') {
        return 'selected'
    }
    if ($RouteClass -eq 'supporting' -and $IncludeSupportingCandidates) {
        return 'selected'
    }
    if ($RouteClass -eq 'reference' -and $IncludeReferenceCandidates) {
        return 'selected'
    }
    if ($RouteClass -eq 'uncertain' -and $IncludeUncertainCandidates) {
        return 'selected'
    }
    return $RouteClass
}

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

function Get-DrawingRevisionMetadata {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    $stem = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $matches = [regex]::Matches($stem, '(?<!\d)(20\d{6})(?!\d)')
    $revisionDate = ''
    if ($matches.Count -gt 0) {
        $revisionDate = $matches[$matches.Count - 1].Value
    }
    $family = [regex]::Replace($stem, '(?<!\d)20\d{6}(?!\d)', '')
    $family = [regex]::Replace($family, '[\(（]\d+[\)）]\s*$', '')
    $family = [regex]::Replace($family, '[\s_\-（）\(\)]+', '')
    $family = $family.ToLowerInvariant()
    return [pscustomobject]@{
        Family = $family
        RevisionDate = $revisionDate
    }
}

function Invoke-PythonStep {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Step,
        [switch]$AllowFailure
    )

    $previousErrorAction = $ErrorActionPreference
    try {
        # Windows PowerShell 5.1 turns native stderr redirected through the
        # success stream into ErrorRecord objects. Keep the native diagnostic
        # visible, but decide success from LASTEXITCODE so AllowFailure works.
        $ErrorActionPreference = 'Continue'
        & $PythonExe @Arguments 2>&1 | Out-Host
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($code -ne 0 -and -not $AllowFailure) {
        throw "Python step failed ($Step): $code"
    }
    return [pscustomobject]@{
        Step = $Step
        ExitCode = $code
        Success = ($code -eq 0)
    }
}

$projectRoot = Split-Path -Parent $PSScriptRoot
function Resolve-V16Script {
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )

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
    throw "V16 component not found: $Name"
}

$frameDetectorScript = Resolve-V16Script '识别图框候选并归属文字.py'
$countScript = Resolve-V16Script '识别阻尼器实例并计数.py'
$layoutVisibilityScript = Resolve-V16Script '分析布局视口阻尼器可见性.py'
$summaryScript = Resolve-V16Script '汇总CAD阻尼器数量核对V16.py'
$buildExportersScript = Resolve-V16Script 'build_zwcad_exporters_v16.ps1'
$batchExportScript = Resolve-V16Script '中望COM隔离批量只读导出V20.ps1'
$cadDiscoveryScript = Resolve-V16Script '发现CAD安装.ps1'
$sourceCandidates = @(
    (Join-Path $PSScriptRoot 'zwcad'),
    (Join-Path $projectRoot 'zwcad'),
    (Join-Path $projectRoot 'zwcad_text_exporter')
)
$exporterSourceDir = $null
foreach ($candidate in $sourceCandidates) {
    if (Test-Path -LiteralPath (
        Join-Path $candidate 'ZwcadVisibilityExporterV13.cs'
    ) -PathType Leaf) {
        $exporterSourceDir = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}
if ([string]::IsNullOrWhiteSpace($exporterSourceDir)) {
    throw 'V16 exporter source directory not found.'
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
    $WorkRoot = Join-Path $baseWorkRoot "cad-v16-$stamp"
}
$WorkRoot = [System.IO.Path]::GetFullPath($WorkRoot)
$workRootDrive = [System.IO.Path]::GetPathRoot($WorkRoot)
if ($WorkRoot.TrimEnd('\').Equals(
        $workRootDrive.TrimEnd('\'),
        [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'V16 工作目录不能直接使用磁盘根目录。'
}
$synchronizedRoots = @(
    $env:OneDrive,
    $env:OneDriveConsumer,
    $env:OneDriveCommercial
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
foreach ($syncRoot in $synchronizedRoots) {
    if (Test-PathSameOrChild -Candidate $WorkRoot -Root $syncRoot) {
        throw "V16 工作目录不得位于同步目录：$syncRoot"
    }
}
if ($WorkRoot -match '(?i)(^|[\\/])OneDrive([\\/]|$)') {
    throw 'V16 工作目录不得位于 OneDrive 路径。'
}

$inputDir = Join-Path $WorkRoot 'input'
$buildDir = Join-Path $WorkRoot 'build'
$analysisRoot = Join-Path $WorkRoot 'analysis'
$summaryDir = Join-Path $WorkRoot 'output'
$exportDir = Join-Path $WorkRoot '输出'
New-Item -ItemType Directory -Path `
    $inputDir, $buildDir, $analysisRoot, $summaryDir -Force | Out-Null

$drawingRows = New-Object System.Collections.Generic.List[object]
$seen = @{}
foreach ($rawPath in $InputPath) {
    $resolved = Resolve-Path -LiteralPath $rawPath
    if (Test-Path -LiteralPath $resolved.Path -PathType Leaf) {
        if ([System.IO.Path]::GetExtension($resolved.Path) -ine '.dwg') {
            throw "显式输入不是 DWG：$($resolved.Path)"
        }
        $key = $resolved.Path.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $revision = Get-DrawingRevisionMetadata -Path $resolved.Path
            $route = Resolve-DrawingRoute `
                -Path $resolved.Path `
                -ExplicitFile $true `
                -RevisionDate $revision.RevisionDate
            $drawingRows.Add([pscustomobject]@{
                SourcePath = $resolved.Path
                RouteStatus = Resolve-PipelineSelection $route.RouteClass
                RouteClass = $route.RouteClass
                Profession = $route.Profession
                DrawingRole = $route.DrawingRole
                Priority = $route.Priority
                RouteReason = $route.Reason
                RouteEvidence = $route.Evidence
                DrawingFamily = $revision.Family
                RevisionDate = $revision.RevisionDate
                FileLength = (Get-Item -LiteralPath $resolved.Path).Length
                RouteSourceSha256 = ''
            })
        }
        continue
    }

    foreach ($file in Get-ChildItem -LiteralPath $resolved.Path -Recurse -File -Filter '*.dwg') {
        $key = $file.FullName.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        $revision = Get-DrawingRevisionMetadata -Path $file.FullName
        $isTopLevel = $file.DirectoryName.Equals(
            $resolved.Path.TrimEnd('\'),
            [System.StringComparison]::OrdinalIgnoreCase
        )
        $route = Resolve-DrawingRoute `
            -Path $file.FullName `
            -ExplicitFile $false `
            -TopLevelDirectoryFile $isTopLevel `
            -RevisionDate $revision.RevisionDate
        $drawingRows.Add([pscustomobject]@{
            SourcePath = $file.FullName
            RouteStatus = Resolve-PipelineSelection $route.RouteClass
            RouteClass = $route.RouteClass
            Profession = $route.Profession
            DrawingRole = $route.DrawingRole
            Priority = $route.Priority
            RouteReason = $route.Reason
            RouteEvidence = $route.Evidence
            DrawingFamily = $revision.Family
            RevisionDate = $revision.RevisionDate
            FileLength = $file.Length
            RouteSourceSha256 = ''
        })
    }
}

if ($drawingRows.Count -eq 0) {
    throw '输入范围内未找到 DWG。'
}

$datedPrimaryGroups = $drawingRows |
    Where-Object {
        $_.RouteStatus -eq 'selected' -and
        $_.RouteClass -eq 'primary' -and
        -not [string]::IsNullOrWhiteSpace($_.RevisionDate)
    } |
    Group-Object DrawingFamily
foreach ($group in $datedPrimaryGroups) {
    if ($group.Count -lt 2) {
        continue
    }
    $latestDate = (
        $group.Group |
            Sort-Object RevisionDate -Descending |
            Select-Object -First 1
    ).RevisionDate
    foreach ($row in $group.Group) {
        if ($row.RevisionDate -ge $latestDate) {
            continue
        }
        $row.RouteClass = 'older_revision'
        $row.RouteStatus = $(if ($IncludeOlderRevisionCandidates) {
                'selected'
            }
            else {
                'older_revision'
            })
        $row.Priority = 10
        $row.RouteReason = 'older_embedded_date_revision'
        $row.RouteEvidence = "$($row.RevisionDate)<$latestDate"
    }
}

$sameSizeSelectedGroups = $drawingRows |
    Where-Object { $_.RouteStatus -eq 'selected' } |
    Group-Object FileLength |
    Where-Object { $_.Count -gt 1 }
foreach ($sizeGroup in $sameSizeSelectedGroups) {
    foreach ($row in $sizeGroup.Group) {
        $row.RouteSourceSha256 = Get-Sha256Hex -Path $row.SourcePath
    }
}
$exactDuplicateGroups = $drawingRows |
    Where-Object {
        $_.RouteStatus -eq 'selected' -and
        -not [string]::IsNullOrWhiteSpace($_.RouteSourceSha256)
    } |
    Group-Object RouteSourceSha256 |
    Where-Object { $_.Count -gt 1 }
foreach ($group in $exactDuplicateGroups) {
    $ordered = @(
        $group.Group |
            Sort-Object @{
                Expression = { $_.RevisionDate }
                Descending = $true
            }, @{
                Expression = { $_.SourcePath.Length }
                Descending = $false
            }, SourcePath
    )
    for ($index = 1; $index -lt $ordered.Count; $index++) {
        $row = $ordered[$index]
        $row.RouteClass = 'exact_duplicate'
        $row.RouteStatus = $(if ($IncludeExactDuplicateCandidates) {
                'selected'
            }
            else {
                'exact_duplicate'
            })
        $row.Priority = 5
        $row.RouteReason = 'exact_sha256_duplicate'
        $row.RouteEvidence = $row.RouteSourceSha256
    }
}

$manifest = New-Object System.Collections.Generic.List[object]
$selectedIndex = 0
foreach ($row in $drawingRows) {
    $copiedPath = ''
    $copiedStem = ''
    $sha256 = ''
    if ($row.RouteStatus -eq 'selected' -and -not $RouteOnly) {
        $selectedIndex++
        $safeStem = [System.IO.Path]::GetFileNameWithoutExtension($row.SourcePath)
        $safeStem = $safeStem -replace '[<>:"/\\|?*]', '_'
        if ($safeStem.Length -gt 80) {
            $safeStem = $safeStem.Substring(0, 80)
        }
        $copiedStem = ('D{0:D4}__{1}' -f $selectedIndex, $safeStem)
        $copiedPath = Join-Path $inputDir ($copiedStem + '.dwg')
        Copy-Item -LiteralPath $row.SourcePath -Destination $copiedPath -Force
        $sourceHash = Get-Sha256Hex -Path $row.SourcePath
        $copyHash = Get-Sha256Hex -Path $copiedPath
        if ($sourceHash -ne $copyHash) {
            throw "DWG 副本哈希不一致：$($row.SourcePath)"
        }
        $sha256 = $sourceHash
    }
    $manifest.Add([pscustomobject]@{
        source_path = $row.SourcePath
        copied_path = $copiedPath
        copied_stem = $copiedStem
        sha256 = $sha256
        route_status = $row.RouteStatus
        route_class = $row.RouteClass
        profession = $row.Profession
        drawing_role = $row.DrawingRole
        route_priority = $row.Priority
        route_reason = $row.RouteReason
        route_evidence = $row.RouteEvidence
        drawing_family = $row.DrawingFamily
        embedded_revision_date = $row.RevisionDate
        source_file_length = $row.FileLength
        route_source_sha256 = $row.RouteSourceSha256
    })
}

$manifestPath = Join-Path $WorkRoot 'input_manifest.csv'
$manifest | Export-Csv -LiteralPath $manifestPath -NoTypeInformation -Encoding UTF8
$routeReport = Join-Path $summaryDir 'V16专业路由预检.md'
if ($RouteOnly) {
    $routeCounts = $manifest |
        Group-Object route_status |
        Sort-Object Name
    $routeSummary = (
        $routeCounts | ForEach-Object { "$($_.Name)=$($_.Count)" }
    ) -join '；'
    $lines = @(
        '# V16 CAD目录分层筛选预检',
        '',
        $routeSummary,
        '',
        '| 主流程状态 | 分类 | 专业 | 图纸角色 | 优先级 | 文件 | 证据 |',
        '| --- | --- | --- | --- | ---: | --- | --- |'
    )
    foreach ($item in ($manifest | Sort-Object @{
                Expression = { [int]$_.route_priority }
                Descending = $true
            }, source_path)) {
        $lines += (
            "| $($item.route_status) | $($item.route_class) | " +
            "$($item.profession) | $($item.drawing_role) | " +
            "$($item.route_priority) | " +
            "$([System.IO.Path]::GetFileName($item.source_path)) | " +
            "$($item.route_reason):$($item.route_evidence) |"
        )
    }
    $lines += @(
        '',
        '- 本次仅预检目录，未启动中望 CAD、未复制 DWG、未生成识图或数量结论。',
        '- `selected` 默认进入阻尼器数量主流程。',
        '- `supporting` 只作为梁/柱/墙/板/基础等跨视图候选，默认不打开；主图有疑点时可显式加入。',
        '- `reference` 只作为目录、总说明和设计说明证据，默认不参与设备主计数。',
        '- `older_revision` 仅表示文件名主体一致且八位日期较早，默认不进入主流程；它不是正式版本关系证明。',
        '- `exact_duplicate` 表示主候选文件 SHA-256 完全相同，默认只保留一个进入主流程。',
        '- `uncertain` 不会自动进入主流程，必须由内容证据或人工选择提升。',
        '- 文件名筛选只是第一层；进入主流程后仍须由图框、实例及可见性证据决定是否计数。'
    )
    Set-Content -LiteralPath $routeReport -Value $lines -Encoding utf8
    Write-Output "V16 路由预检完成：$routeReport"
    return
}
$selected = @($manifest | Where-Object { $_.route_status -eq 'selected' })
if ($selected.Count -eq 0) {
    Invoke-PythonStep -Arguments @(
        $summaryScript,
        '--manifest', $manifestPath,
        '--export-dir', $exportDir,
        '--analysis-root', $analysisRoot,
        '--output-dir', $summaryDir
    ) -Step 'summary-no-selected' | Out-Null
    Write-Output "V16 未选择任何结构 DWG；详见 $summaryDir"
    return
}

if ([string]::IsNullOrWhiteSpace($ZwcadRoot)) {
    $cadCandidates = @(& $cadDiscoveryScript -Vendor ZWCAD -RequireCurrentBackend)
    $ZwcadRoot = [string]$cadCandidates[0].install_root
    Write-Output "已自动发现中望CAD：$ZwcadRoot"
}

& $buildExportersScript `
    -ZwcadRoot $ZwcadRoot `
    -OutputDir $buildDir `
    -SourceDir $exporterSourceDir

$exportExecutionLog = Join-Path $summaryDir 'V20中望导出执行.csv'
& $batchExportScript `
    -DrawingPath @($selected.copied_path) `
    -ZwcadRoot $ZwcadRoot `
    -Mode Full `
    -PluginDir $buildDir `
    -ExecutionLog $exportExecutionLog `
    -PerDrawingTimeoutSeconds 900 `
    -CommandTimeoutSeconds 600 `
    -Visible:$Visible

$stepLog = New-Object System.Collections.Generic.List[object]
foreach ($item in $selected) {
    $stem = $item.copied_stem
    $analysisDir = Join-Path $analysisRoot $stem
    New-Item -ItemType Directory -Path $analysisDir -Force | Out-Null
    $textJson = Join-Path $exportDir "$stem.cad_text_export_v5.json"
    $frameJson = Join-Path $exportDir "$stem.cad_frame_export_v5.json"
    $symbolJson = Join-Path $exportDir "$stem.cad_symbol_export_v6.json"
    $orientedJson = Join-Path $exportDir "$stem.cad_oriented_text_export_v7.json"
    $visibilityJson = Join-Path $exportDir "$stem.cad_visibility_export_v13.json"

    if ((Test-Path -LiteralPath $textJson) -and (Test-Path -LiteralPath $frameJson)) {
        $frameArguments = @(
            $frameDetectorScript,
            '--text', $textJson,
            '--geometry', $frameJson,
            '--prefix', $stem,
            '--output-dir', $analysisDir
        )
        if ($SupplementLargeBlockFrames) {
            $frameArguments += '--supplement-large-block-frames'
        }
        $frameStep = Invoke-PythonStep `
            -Arguments $frameArguments `
            -Step "$stem/frame" `
            -AllowFailure
        $stepLog.Add($frameStep)
    }

    if (Test-Path -LiteralPath $symbolJson) {
        $countArguments = @(
            $countScript,
            $symbolJson,
            '--output-dir', $analysisDir,
            '--prefix', $stem
        )
        $framesCsv = Join-Path $analysisDir "$stem.图框候选清单.csv"
        $frameTextsCsv = Join-Path $analysisDir "$stem.文字按图框归属清单.csv"
        if ((Test-Path -LiteralPath $framesCsv) -and
            (Test-Path -LiteralPath $frameTextsCsv)) {
            $countArguments += @(
                '--frames', $framesCsv,
                '--frame-texts', $frameTextsCsv
            )
        }
        if (Test-Path -LiteralPath $orientedJson) {
            $countArguments += @('--oriented-texts', $orientedJson)
        }
        if (Test-Path -LiteralPath $textJson) {
            $countArguments += @('--text-json', $textJson)
        }
        $countStep = Invoke-PythonStep `
            -Arguments $countArguments `
            -Step "$stem/count"
        $stepLog.Add($countStep)
    }

    $candidateCsv = Join-Path $analysisDir "$stem.阻尼器实例候选.csv"
    if ((Test-Path -LiteralPath $visibilityJson) -and
        (Test-Path -LiteralPath $candidateCsv)) {
        $layoutStep = Invoke-PythonStep -Arguments @(
            $layoutVisibilityScript,
            $visibilityJson,
            $candidateCsv,
            '--output-dir', $analysisDir,
            '--prefix', "$stem.V14布局视口可见性"
        ) -Step "$stem/layout-visibility"
        $stepLog.Add($layoutStep)
    }
}

$stepLog |
    Export-Csv -LiteralPath (Join-Path $WorkRoot 'pipeline_steps.csv') `
    -NoTypeInformation -Encoding UTF8

Invoke-PythonStep -Arguments @(
    $summaryScript,
    '--manifest', $manifestPath,
    '--export-dir', $exportDir,
    '--analysis-root', $analysisRoot,
    '--output-dir', $summaryDir
) -Step 'summary' | Out-Null

Write-Output "V16 完成。"
Write-Output "工作目录：$WorkRoot"
Write-Output "运行汇总：$(Join-Path $summaryDir 'V16运行汇总.md')"




