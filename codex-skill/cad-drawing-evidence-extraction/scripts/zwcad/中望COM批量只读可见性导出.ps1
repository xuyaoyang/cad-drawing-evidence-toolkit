param(
    [Parameter(Mandatory = $true)]
    [string[]]$DrawingPath,

    [Parameter(Mandatory = $true)]
    [string]$PluginPath,

    [int]$TimeoutSeconds = 300,

    [switch]$Visible
)

$ErrorActionPreference = 'Stop'

function Wait-ZwcadIdle {
    param(
        [Parameter(Mandatory = $true)]$Document,
        [int]$TimeoutSeconds = 300
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 250
        try {
            if ([int]$Document.GetVariable('CMDACTIVE') -eq 0) {
                return
            }
        }
        catch {
            # 文档切换期间 COM 可能短暂拒绝调用。
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "等待中望 CAD 命令结束超时：$($Document.Name)"
}

function Invoke-ZwcadCommand {
    param(
        [Parameter(Mandatory = $true)]$Document,
        [Parameter(Mandatory = $true)][string]$Command,
        [int]$TimeoutSeconds = 300
    )

    $Document.SendCommand($Command + "`n")
    Start-Sleep -Seconds 1
    Wait-ZwcadIdle -Document $Document -TimeoutSeconds $TimeoutSeconds
}

$resolvedPlugin = (Resolve-Path -LiteralPath $PluginPath).Path
$resolvedDrawings = foreach ($path in $DrawingPath) {
    (Resolve-Path -LiteralPath $path).Path
}

$app = $null
$bootstrap = $null
try {
    $app = New-Object -ComObject 'ZWCAD.Application'
    $app.Visible = [bool]$Visible
    $bootstrap = $app.ActiveDocument
    Write-Output "中望 CAD COM 已启动。"

    $lispPath = $resolvedPlugin.Replace('\', '/').Replace('"', '\"')
    $bootstrap.SendCommand("(command `"_.NETLOAD`" `"$lispPath`")`n")
    Start-Sleep -Seconds 1
    Wait-ZwcadIdle -Document $bootstrap -TimeoutSeconds $TimeoutSeconds
    Write-Output "可见性插件已加载：$resolvedPlugin"

    foreach ($drawing in $resolvedDrawings) {
        $document = $null
        try {
            Write-Output "只读打开：$drawing"
            $document = $app.Documents.Open($drawing, $true)
            $document.Activate()
            Wait-ZwcadIdle -Document $document -TimeoutSeconds $TimeoutSeconds
            Write-Output "执行：CADVISIBILITYEXPORT13"
            Invoke-ZwcadCommand `
                -Document $document `
                -Command 'CADVISIBILITYEXPORT13' `
                -TimeoutSeconds $TimeoutSeconds
            Write-Output "完成：$drawing"
        }
        finally {
            if ($null -ne $document) {
                $document.Close($false)
            }
        }
    }
}
finally {
    if ($null -ne $bootstrap) {
        try {
            $bootstrap.Close($false)
        }
        catch {
        }
    }
    if ($null -ne $app) {
        try {
            $app.Quit()
        }
        catch {
        }
    }
}
