param(
    [Parameter(Mandatory = $true)]
    [string]$JobPath
)

$ErrorActionPreference = 'Stop'

function Wait-ZwcadIdle {
    param(
        [Parameter(Mandatory = $true)]$Document,
        [int]$TimeoutSeconds
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
        [int]$TimeoutSeconds
    )

    $Document.SendCommand($Command + "`n")
    Start-Sleep -Seconds 1
    Wait-ZwcadIdle `
        -Document $Document `
        -TimeoutSeconds $TimeoutSeconds
}

$job = Get-Content -LiteralPath $JobPath -Encoding UTF8 -Raw |
    ConvertFrom-Json
$result = [ordered]@{
    drawing_path = $job.drawing_path
    mode = $job.mode
    status = 'started'
    message = ''
    elapsed_seconds = $null
    completed_commands = @()
}
$started = Get-Date
$app = $null
$bootstrap = $null
$document = $null

try {
    $app = New-Object -ComObject 'ZWCAD.Application'
    $app.Visible = [bool]$job.visible
    $bootstrap = $app.ActiveDocument
    $bootstrap.SetVariable('FILEDIA', 0)
    $bootstrap.SetVariable('CMDDIA', 0)

    foreach ($pluginPath in @($job.plugin_paths)) {
        $resolvedPlugin = (
            Resolve-Path -LiteralPath $pluginPath
        ).Path
        $lispPath = $resolvedPlugin.Replace('\', '/').Replace('"', '\"')
        $bootstrap.SendCommand(
            "(command `"_.NETLOAD`" `"$lispPath`")`n"
        )
        Start-Sleep -Seconds 1
        Wait-ZwcadIdle `
            -Document $bootstrap `
            -TimeoutSeconds ([int]$job.command_timeout_seconds)
    }

    $document = $app.Documents.Open(
        [System.IO.Path]::GetFullPath($job.drawing_path),
        $true
    )
    $document.Activate()
    Wait-ZwcadIdle `
        -Document $document `
        -TimeoutSeconds ([int]$job.command_timeout_seconds)

    foreach ($command in @($job.commands)) {
        Invoke-ZwcadCommand `
            -Document $document `
            -Command $command `
            -TimeoutSeconds ([int]$job.command_timeout_seconds)
        $result.completed_commands += $command
    }
    $result.status = 'success'
}
catch {
    $result.status = 'failed'
    $result.message = $_.Exception.ToString()
}
finally {
    $result.elapsed_seconds = [math]::Round(
        ((Get-Date) - $started).TotalSeconds,
        3
    )
    if ($null -ne $document) {
        try {
            $document.Close($false)
        }
        catch {
        }
    }
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
    $result |
        ConvertTo-Json -Depth 6 |
        Set-Content `
            -LiteralPath $job.result_path `
            -Encoding utf8
}

