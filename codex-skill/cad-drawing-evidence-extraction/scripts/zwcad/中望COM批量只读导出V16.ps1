param(
    [Parameter(Mandatory = $true)]
    [string[]]$DrawingPath,

    [Parameter(Mandatory = $true)]
    [string]$PluginDir,

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

$plugins = @(
    'CadReadingExploration.ZwcadTextExporterV5.dll',
    'CadReadingExploration.ZwcadFrameExporterV5.dll',
    'CadReadingExploration.ZwcadSymbolExporterV6.dll',
    'CadReadingExploration.ZwcadOrientedTextExporterV7.dll',
    'CadReadingExploration.ZwcadPrimitiveGeometryExporterV10.dll',
    'CadReadingExploration.ZwcadVisibilityExporterV13.dll'
)
$commands = @(
    'CADTEXTEXPORT5',
    'CADFRAMEEXPORT5',
    'CADSYMBOLEXPORT6',
    'CADORIENTEDTEXTEXPORT7',
    'CADPRIMITIVEEXPORT10',
    'CADVISIBILITYEXPORT13'
)

$resolvedPlugins = foreach ($name in $plugins) {
    (Resolve-Path -LiteralPath (Join-Path $PluginDir $name)).Path
}
$resolvedDrawings = foreach ($path in $DrawingPath) {
    (Resolve-Path -LiteralPath $path).Path
}

$app = $null
$bootstrap = $null
try {
    $app = New-Object -ComObject 'ZWCAD.Application'
    $app.Visible = [bool]$Visible
    $bootstrap = $app.ActiveDocument

    foreach ($plugin in $resolvedPlugins) {
        $lispPath = $plugin.Replace('\', '/').Replace('"', '\"')
        $bootstrap.SendCommand("(command `"_.NETLOAD`" `"$lispPath`")`n")
        Start-Sleep -Seconds 1
        Wait-ZwcadIdle -Document $bootstrap -TimeoutSeconds $TimeoutSeconds
    }

    foreach ($drawing in $resolvedDrawings) {
        $document = $null
        try {
            Write-Output "只读打开：$drawing"
            $document = $app.Documents.Open($drawing, $true)
            $document.Activate()
            Wait-ZwcadIdle -Document $document -TimeoutSeconds $TimeoutSeconds
            foreach ($command in $commands) {
                Write-Output "执行：$command"
                Invoke-ZwcadCommand `
                    -Document $document `
                    -Command $command `
                    -TimeoutSeconds $TimeoutSeconds
            }
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
