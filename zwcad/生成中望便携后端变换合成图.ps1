param(
    [Parameter(Mandatory = $true)]
    [string]$PluginPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDrawing,

    [int]$TimeoutSeconds = 180,

    [switch]$Visible
)

$ErrorActionPreference = 'Stop'

function Wait-ZwcadIdle {
    param([Parameter(Mandatory = $true)]$Document, [int]$TimeoutSeconds = 180)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        try { if ([int]$Document.GetVariable('CMDACTIVE') -eq 0) { return } } catch { }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "等待中望 CAD 命令结束超时：$($Document.Name)"
}

$resolvedPlugin = (Resolve-Path -LiteralPath $PluginPath).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDrawing)
New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedOutput) -Force | Out-Null
$oldOutput = [Environment]::GetEnvironmentVariable('CAD_SYNTH_PORTABLE_OUTPUT', [EnvironmentVariableTarget]::Process)
$app = $null
$bootstrap = $null
try {
    [Environment]::SetEnvironmentVariable('CAD_SYNTH_PORTABLE_OUTPUT', $resolvedOutput, [EnvironmentVariableTarget]::Process)
    $app = New-Object -ComObject 'ZWCAD.Application'
    $app.Visible = [bool]$Visible
    $bootstrap = $app.ActiveDocument
    $lispPath = $resolvedPlugin.Replace('\', '/').Replace('"', '\"')
    $bootstrap.SendCommand("(command `"_.NETLOAD`" `"$lispPath`")`n")
    Start-Sleep -Seconds 1
    Wait-ZwcadIdle -Document $bootstrap -TimeoutSeconds $TimeoutSeconds
    $bootstrap.SendCommand("CADCREATESYNTHPORTABLETRANSFORM`n")
    Start-Sleep -Seconds 1
    Wait-ZwcadIdle -Document $bootstrap -TimeoutSeconds $TimeoutSeconds
    $truthPath = [System.IO.Path]::ChangeExtension($resolvedOutput, '.ground_truth.json')
    foreach ($path in @($resolvedOutput, $truthPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "中望命令结束但未生成：$path" }
    }
    Get-Item -LiteralPath $resolvedOutput, $truthPath | Select-Object FullName, Length, LastWriteTime
}
finally {
    [Environment]::SetEnvironmentVariable('CAD_SYNTH_PORTABLE_OUTPUT', $oldOutput, [EnvironmentVariableTarget]::Process)
    if ($null -ne $bootstrap) { try { $bootstrap.Close($false) } catch { } }
    if ($null -ne $app) { try { $app.Quit() } catch { } }
}
