param(
    [Parameter(Mandatory = $true)]
    [string[]]$DrawingPath,

    [Parameter(Mandatory = $true)]
    [string]$PluginPath,

    [Parameter(Mandatory = $true)]
    [string]$ExecutionLog,

    [int]$TimeoutSeconds = 600,

    [switch]$Visible
)

$ErrorActionPreference = 'Stop'

function Wait-ZwcadIdle {
    param(
        [Parameter(Mandatory = $true)]$Document,
        [int]$TimeoutSeconds = 600
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
        }
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "等待中望 CAD 命令结束超时：$($Document.Name)"
}

function Invoke-ZwcadCommand {
    param(
        [Parameter(Mandatory = $true)]$Document,
        [Parameter(Mandatory = $true)][string]$Command,
        [int]$TimeoutSeconds = 600
    )

    $Document.SendCommand($Command + "`n")
    Start-Sleep -Seconds 1
    Wait-ZwcadIdle -Document $Document -TimeoutSeconds $TimeoutSeconds
}

$resolvedPlugin = (Resolve-Path -LiteralPath $PluginPath).Path
$resolvedDrawings = foreach ($path in $DrawingPath) {
    (Resolve-Path -LiteralPath $path).Path
}
$executionRows = New-Object System.Collections.Generic.List[object]
$app = $null
$bootstrap = $null
try {
    $app = New-Object -ComObject 'ZWCAD.Application'
    $app.Visible = [bool]$Visible
    $bootstrap = $app.ActiveDocument
    $lispPath = $resolvedPlugin.Replace('\', '/').Replace('"', '\"')
    $bootstrap.SendCommand("(command `"_.NETLOAD`" `"$lispPath`")`n")
    Start-Sleep -Seconds 1
    Wait-ZwcadIdle -Document $bootstrap -TimeoutSeconds $TimeoutSeconds

    foreach ($drawing in $resolvedDrawings) {
        $document = $null
        $started = Get-Date
        $status = 'success'
        $message = ''
        try {
            Write-Output "轻量只读扫描：$drawing"
            $document = $app.Documents.Open($drawing, $true)
            $document.Activate()
            Wait-ZwcadIdle -Document $document -TimeoutSeconds $TimeoutSeconds
            Invoke-ZwcadCommand `
                -Document $document `
                -Command 'CADPREFILTEREXPORT18' `
                -TimeoutSeconds $TimeoutSeconds
        }
        catch {
            $status = 'failed'
            $message = $_.Exception.Message
            Write-Warning "内容指纹导出失败，保留为未解决：$drawing；$message"
        }
        finally {
            if ($null -ne $document) {
                try {
                    $document.Close($false)
                }
                catch {
                    if ([string]::IsNullOrWhiteSpace($message)) {
                        $message = $_.Exception.Message
                    }
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
            })
        }
    }
}
finally {
    $logDirectory = Split-Path -Parent $ExecutionLog
    if (-not [string]::IsNullOrWhiteSpace($logDirectory)) {
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    }
    $executionRows |
        Export-Csv -LiteralPath $ExecutionLog -NoTypeInformation -Encoding UTF8
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



