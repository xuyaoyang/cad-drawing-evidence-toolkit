param(
    [Parameter(Mandatory = $true)]
    [string[]]$DrawingPath,

    [Parameter(Mandatory = $true)]
    [string]$ZwcadRoot,

    [Parameter(Mandatory = $true)]
    [ValidateSet('Full', 'ContentFingerprint')]
    [string]$Mode,

    [string]$PluginDir,

    [string]$PluginPath,

    [Parameter(Mandatory = $true)]
    [string]$ExecutionLog,

    [int]$PerDrawingTimeoutSeconds = 900,

    [int]$CommandTimeoutSeconds = 600,

    [switch]$Visible,

    [switch]$AllowExistingZwcad
)

$ErrorActionPreference = 'Stop'

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Stop-NewZwcadProcesses {
    param(
        [int[]]$ExistingProcessIds,
        [datetime]$StartedAfter
    )

    $stopped = New-Object System.Collections.Generic.List[int]
    foreach ($process in @(
            Get-Process -Name ZWCAD -ErrorAction SilentlyContinue
        )) {
        if (
            $ExistingProcessIds -notcontains $process.Id -and
            $process.StartTime -ge $StartedAfter
        ) {
            Stop-Process `
                -Id $process.Id `
                -Force `
                -ErrorAction SilentlyContinue
            $stopped.Add($process.Id)
        }
    }
    return @($stopped)
}

function Write-FontMap {
    param(
        [Parameter(Mandatory = $true)][string]$OriginalMap,
        [Parameter(Mandatory = $true)][string]$TargetMap
    )

    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $OriginalMap -PathType Leaf) {
        foreach ($line in [System.IO.File]::ReadAllLines(
                $OriginalMap,
                [System.Text.Encoding]::GetEncoding(936)
            )) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                $lines.Add($line)
            }
        }
    }

    $aliases = @(
        'sft',
        'sft.shx',
        (-join [char[]](0x5B8B, 0x4F53)),
        ((-join [char[]](0x5B8B, 0x4F53)) + '.shx'),
        (-join [char[]](0x9ED1, 0x4F53)),
        ((-join [char[]](0x9ED1, 0x4F53)) + '.shx'),
        (-join [char[]](0x4EFF, 0x5B8B)),
        ((-join [char[]](0x4EFF, 0x5B8B)) + '.shx'),
        (-join [char[]](0x6977, 0x4F53)),
        ((-join [char[]](0x6977, 0x4F53)) + '.shx')
    )
    foreach ($alias in $aliases) {
        $replacement = if ($alias -like 'sft*') {
            'simplex.shx'
        }
        else {
            'HZTXT.SHX'
        }
        $lines.Add($alias + ';' + $replacement)
    }

    [System.IO.File]::WriteAllLines(
        $TargetMap,
        @($lines),
        [System.Text.Encoding]::GetEncoding(936)
    )
}

$workerScript = Join-Path $PSScriptRoot '中望COM单图只读工作器V20.ps1'
if (-not (Test-Path -LiteralPath $workerScript -PathType Leaf)) {
    throw "V20 单图工作器不存在：$workerScript"
}
$resolvedRoot = (Resolve-Path -LiteralPath $ZwcadRoot).Path
$fallbackFont = Join-Path $resolvedRoot 'fonts\HZTXT.SHX'
$normalFont = Join-Path $resolvedRoot 'fonts\simplex.shx'
foreach ($fontPath in @($fallbackFont, $normalFont)) {
    if (-not (Test-Path -LiteralPath $fontPath -PathType Leaf)) {
        throw "无人值守字体不存在：$fontPath"
    }
}

$existingZwcad = @(
    Get-Process -Name ZWCAD -ErrorAction SilentlyContinue
)
if ($existingZwcad.Count -gt 0 -and -not $AllowExistingZwcad) {
    throw (
        '检测到已运行的中望 CAD。无人值守隔离模式拒绝复用或终止' +
        '用户会话；请先关闭中望 CAD，或显式使用 AllowExistingZwcad。'
    )
}
$initialProcessIds = @($existingZwcad | ForEach-Object { $_.Id })

$sessionDir = Split-Path -Parent $ExecutionLog
if ([string]::IsNullOrWhiteSpace($sessionDir)) {
    $sessionDir = (Get-Location).Path
}
$fontPolicyDir = Join-Path $sessionDir 'font-policy'
$jobDir = Join-Path $sessionDir 'v20-jobs'
New-Item `
    -ItemType Directory `
    -Path $sessionDir, $fontPolicyDir, $jobDir `
    -Force |
    Out-Null

$recoveryRoot = Join-Path $env:LOCALAPPDATA 'CadReadingToolkit'
$recoveryFile = Join-Path $recoveryRoot 'v20-font-recovery.json'
New-Item -ItemType Directory -Path $recoveryRoot -Force | Out-Null
$staleRecoveryApplied = $false
if (Test-Path -LiteralPath $recoveryFile -PathType Leaf) {
    $recoveryPayload = Get-Content `
        -LiteralPath $recoveryFile `
        -Encoding UTF8 `
        -Raw |
        ConvertFrom-Json
    $staleApp = $null
    $staleDocument = $null
    try {
        $staleApp = New-Object -ComObject 'ZWCAD.Application'
        $staleApp.Visible = $false
        $staleDocument = $staleApp.ActiveDocument
        $staleApp.Preferences.Files.AltFontFile = (
            $recoveryPayload.original.alt_font_file
        )
        $staleApp.Preferences.Files.FontFileMap = (
            $recoveryPayload.original.font_file_map
        )
        $staleDocument.SetVariable(
            'FILEDIA',
            [int]$recoveryPayload.original.filedia
        )
        $staleDocument.SetVariable(
            'CMDDIA',
            [int]$recoveryPayload.original.cmddia
        )
        $staleDocument.Close($false)
        $staleDocument = $null
        $staleApp.Quit()
        $staleApp = $null
        Remove-Item -LiteralPath $recoveryFile -Force
        $staleRecoveryApplied = $true
        Start-Sleep -Seconds 3
    }
    finally {
        if ($null -ne $staleDocument) {
            try {
                $staleDocument.Close($false)
            }
            catch {
            }
        }
        if ($null -ne $staleApp) {
            try {
                $staleApp.Quit()
            }
            catch {
            }
        }
    }
}

$resolvedDrawings = foreach ($path in $DrawingPath) {
    (Resolve-Path -LiteralPath $path).Path
}
$pluginPaths = @()
$commands = @()
if ($Mode -eq 'Full') {
    if ([string]::IsNullOrWhiteSpace($PluginDir)) {
        throw 'Full 模式必须提供 PluginDir。'
    }
    $pluginNames = @(
        'CadReadingExploration.ZwcadTextExporterV5.dll',
        'CadReadingExploration.ZwcadFrameExporterV5.dll',
        'CadReadingExploration.ZwcadSymbolExporterV6.dll',
        'CadReadingExploration.ZwcadOrientedTextExporterV7.dll',
        'CadReadingExploration.ZwcadPrimitiveGeometryExporterV10.dll',
        'CadReadingExploration.ZwcadVisibilityExporterV13.dll'
    )
    $pluginPaths = foreach ($name in $pluginNames) {
        (Resolve-Path -LiteralPath (Join-Path $PluginDir $name)).Path
    }
    $commands = @(
        'CADTEXTEXPORT5',
        'CADFRAMEEXPORT5',
        'CADSYMBOLEXPORT6',
        'CADORIENTEDTEXTEXPORT7',
        'CADPRIMITIVEEXPORT10',
        'CADVISIBILITYEXPORT13'
    )
}
else {
    if ([string]::IsNullOrWhiteSpace($PluginPath)) {
        throw 'ContentFingerprint 模式必须提供 PluginPath。'
    }
    $pluginPaths = @((Resolve-Path -LiteralPath $PluginPath).Path)
    $commands = @('CADPREFILTEREXPORT18')
}

$original = [ordered]@{}
$fontMapPath = Join-Path $fontPolicyDir 'zwcad-unattended.fmp'
$fontEvidencePath = Join-Path $fontPolicyDir 'font-policy.json'
$bootstrapApp = $null
$bootstrapDocument = $null
$executionRows = New-Object System.Collections.Generic.List[object]

try {
    $bootstrapApp = New-Object -ComObject 'ZWCAD.Application'
    $bootstrapApp.Visible = $false
    $bootstrapDocument = $bootstrapApp.ActiveDocument
    $original.alt_font_file = $bootstrapApp.Preferences.Files.AltFontFile
    $original.font_file_map = $bootstrapApp.Preferences.Files.FontFileMap
    $original.filedia = $bootstrapDocument.GetVariable('FILEDIA')
    $original.cmddia = $bootstrapDocument.GetVariable('CMDDIA')
    [ordered]@{
        policy = 'zwcad_unattended_font_recovery_v20'
        created_at = (Get-Date).ToString('o')
        owner_pid = $PID
        execution_log = $ExecutionLog
        original = $original
    } |
        ConvertTo-Json -Depth 6 |
        Set-Content `
            -LiteralPath $recoveryFile `
            -Encoding utf8
    Write-FontMap `
        -OriginalMap $original.font_file_map `
        -TargetMap $fontMapPath
    $bootstrapApp.Preferences.Files.AltFontFile = 'HZTXT.SHX'
    $bootstrapApp.Preferences.Files.FontFileMap = $fontMapPath
    $bootstrapDocument.SetVariable('FILEDIA', 0)
    $bootstrapDocument.SetVariable('CMDDIA', 0)
    $bootstrapDocument.Close($false)
    $bootstrapDocument = $null
    $bootstrapApp.Quit()
    $bootstrapApp = $null
    Start-Sleep -Seconds 3

    [ordered]@{
        policy = 'zwcad_unattended_font_policy_v20'
        fallback_font = $fallbackFont
        fallback_font_sha256 = Get-Sha256Hex -Path $fallbackFont
        normal_font = $normalFont
        normal_font_sha256 = Get-Sha256Hex -Path $normalFont
        font_map = $fontMapPath
        original = $original
        recovery_file = $recoveryFile
        stale_recovery_applied = $staleRecoveryApplied
        note = (
            '字体替换只保证无人值守读取不中断；字宽、字形和排版可能' +
            '变化，视觉出图仍须使用原字体复核。'
        )
    } |
        ConvertTo-Json -Depth 6 |
        Set-Content `
            -LiteralPath $fontEvidencePath `
            -Encoding utf8

    $index = 0
    foreach ($drawing in $resolvedDrawings) {
        $index++
        $started = Get-Date
        $safeStem = [System.IO.Path]::GetFileNameWithoutExtension($drawing)
        $safeStem = $safeStem -replace '[<>:"/\\|?*]', '_'
        if ($safeStem.Length -gt 72) {
            $safeStem = $safeStem.Substring(0, 72)
        }
        $jobStem = ('J{0:D4}__{1}' -f $index, $safeStem)
        $jobPath = Join-Path $jobDir ($jobStem + '.json')
        $resultPath = Join-Path $jobDir ($jobStem + '.result.json')
        $stdoutPath = Join-Path $jobDir ($jobStem + '.stdout.log')
        $stderrPath = Join-Path $jobDir ($jobStem + '.stderr.log')
        [ordered]@{
            drawing_path = $drawing
            mode = $Mode
            plugin_paths = $pluginPaths
            commands = $commands
            command_timeout_seconds = $CommandTimeoutSeconds
            visible = [bool]$Visible
            result_path = $resultPath
        } |
            ConvertTo-Json -Depth 8 |
            Set-Content -LiteralPath $jobPath -Encoding utf8

        $workerStart = Get-Date
        $workerArguments = (
            '-NoProfile -ExecutionPolicy Bypass ' +
            '-File "' + $workerScript + '" ' +
            '-JobPath "' + $jobPath + '"'
        )
        $process = Start-Process `
            -FilePath 'powershell.exe' `
            -ArgumentList $workerArguments `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -PassThru
        $completed = $process.WaitForExit(
            $PerDrawingTimeoutSeconds * 1000
        )
        $status = 'failed'
        $message = ''
        $completedCommands = ''
        $terminatedPids = @()
        if (-not $completed) {
            Stop-Process `
                -Id $process.Id `
                -Force `
                -ErrorAction SilentlyContinue
            $terminatedPids = Stop-NewZwcadProcesses `
                -ExistingProcessIds $initialProcessIds `
                -StartedAfter $workerStart
            $status = 'timeout'
            $message = (
                "单图隔离进程超过 ${PerDrawingTimeoutSeconds} 秒；" +
                '已终止本轮新进程，后续图纸继续。'
            )
        }
        elseif (Test-Path -LiteralPath $resultPath -PathType Leaf) {
            $workerResult = Get-Content `
                -LiteralPath $resultPath `
                -Encoding UTF8 `
                -Raw |
                ConvertFrom-Json
            $status = $workerResult.status
            $message = $workerResult.message
            $completedCommands = @(
                $workerResult.completed_commands
            ) -join '|'
            if ($status -ne 'success') {
                $terminatedPids = Stop-NewZwcadProcesses `
                    -ExistingProcessIds $initialProcessIds `
                    -StartedAfter $workerStart
            }
        }
        else {
            $message = (
                "工作器退出码=$($process.ExitCode)，但没有结果 JSON。"
            )
            $terminatedPids = Stop-NewZwcadProcesses `
                -ExistingProcessIds $initialProcessIds `
                -StartedAfter $workerStart
        }
        if ($status -eq 'success') {
            Start-Sleep -Seconds 1
            $orphanPids = Stop-NewZwcadProcesses `
                -ExistingProcessIds $initialProcessIds `
                -StartedAfter $workerStart
            if ($orphanPids.Count -gt 0) {
                $terminatedPids = @($terminatedPids) + @($orphanPids)
                $message = (
                    '导出已成功，但工作器退出后仍有本轮中望进程；' +
                    '已按启动时间和原进程快照精确清理。'
                )
            }
        }

        $executionRows.Add([pscustomobject]@{
            drawing_path = $drawing
            status = $status
            message = $message
            elapsed_seconds = [math]::Round(
                ((Get-Date) - $started).TotalSeconds,
                3
            )
            completed_commands = $completedCommands
            terminated_zwcad_pids = $terminatedPids -join '|'
            font_policy = $fontEvidencePath
        })
    }
}
finally {
    if ($null -ne $bootstrapDocument) {
        try {
            $bootstrapDocument.Close($false)
        }
        catch {
        }
    }
    if ($null -ne $bootstrapApp) {
        try {
            $bootstrapApp.Quit()
        }
        catch {
        }
    }

    $executionRows |
        Export-Csv `
            -LiteralPath $ExecutionLog `
            -NoTypeInformation `
            -Encoding UTF8

    if ($original.Count -gt 0) {
        $restoreApp = $null
        $restoreDocument = $null
        $restoreCompleted = $false
        try {
            $restoreApp = New-Object -ComObject 'ZWCAD.Application'
            $restoreApp.Visible = $false
            $restoreDocument = $restoreApp.ActiveDocument
            $restoreApp.Preferences.Files.AltFontFile = (
                $original.alt_font_file
            )
            $restoreApp.Preferences.Files.FontFileMap = (
                $original.font_file_map
            )
            $restoreDocument.SetVariable('FILEDIA', $original.filedia)
            $restoreDocument.SetVariable('CMDDIA', $original.cmddia)
            $restoreDocument.Close($false)
            $restoreDocument = $null
            $restoreApp.Quit()
            $restoreApp = $null
            $restoreCompleted = $true
        }
        finally {
            if ($null -ne $restoreDocument) {
                try {
                    $restoreDocument.Close($false)
                }
                catch {
                }
            }
            if ($null -ne $restoreApp) {
                try {
                    $restoreApp.Quit()
                }
                catch {
                }
            }
        }
        if ($restoreCompleted -and (Test-Path -LiteralPath $recoveryFile)) {
            Remove-Item -LiteralPath $recoveryFile -Force
        }
    }
}

Write-Output "V20 隔离批处理完成：$ExecutionLog"
Write-Output "字体策略证据：$fontEvidencePath"
