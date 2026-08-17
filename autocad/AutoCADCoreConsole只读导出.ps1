param(
    [Parameter(Mandatory = $true)]
    [string[]]$DrawingPath,

    [Parameter(Mandatory = $true)]
    [string]$AutoCadRoot,

    [Parameter(Mandatory = $true)]
    [ValidateSet('Full', 'ContentFingerprint')]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$PluginDir,

    [Parameter(Mandatory = $true)]
    [string]$ExecutionLog,

    [int]$PerDrawingTimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'AutoCADVersionPolicy.ps1')

if (-not ('CadReadingNativePath' -as [type])) {
    Add-Type -TypeDefinition @'
using System.Text;
using System.Runtime.InteropServices;
public static class CadReadingNativePath
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    public static extern uint GetShortPathName(
        string longPath,
        StringBuilder shortPath,
        uint shortPathLength);
}
'@
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Convert-ToCoreConsolePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $buffer = New-Object System.Text.StringBuilder 2048
    $length = [CadReadingNativePath]::GetShortPathName(
        $fullPath,
        $buffer,
        [uint32]$buffer.Capacity
    )
    if ($length -eq 0 -or $length -ge $buffer.Capacity) {
        throw "Unable to obtain an AutoCAD script-safe path for: $fullPath"
    }
    $scriptPath = $buffer.ToString()
    if ($scriptPath -match '[^\x20-\x7E]') {
        throw "AutoCAD script path is not ASCII-safe: $scriptPath"
    }
    return ($scriptPath -replace '\\', '/')
}

$resolvedRoot = (Resolve-Path -LiteralPath $AutoCadRoot).Path
$resolvedPluginDir = (Resolve-Path -LiteralPath $PluginDir).Path
$coreConsole = Join-Path $resolvedRoot 'accoreconsole.exe'
$acadExe = Join-Path $resolvedRoot 'acad.exe'
foreach ($required in @($coreConsole, $acadExe)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "AutoCAD executable not found: $required"
    }
}

$acadVersion = (Get-Item -LiteralPath $acadExe).VersionInfo.FileVersion
$policy = Get-AutoCadHostPolicy -FileVersion $acadVersion
if ($null -eq $policy) {
    throw (
        'Unsupported AutoCAD host. Supported releases are AutoCAD 2023 (R24.2), ' +
        '2020 (R23.1), 2018 (R22.0), and 64-bit 2014 (R19.1). ' +
        "Detected: $acadVersion"
    )
}
$architecture = Get-PortableExecutableArchitecture -Path $acadExe
if ($architecture -ne 'x64') {
    throw "Only 64-bit AutoCAD hosts are supported. Detected architecture: $architecture"
}

if ($Mode -eq 'Full') {
    $pluginNames = @(
        'CadReadingExploration.AutoCADTextExporterV5.dll',
        'CadReadingExploration.AutoCADFrameExporterV5.dll',
        'CadReadingExploration.AutoCADSymbolExporterV6.dll',
        'CadReadingExploration.AutoCADOrientedTextExporterV7.dll',
        'CadReadingExploration.AutoCADPrimitiveGeometryExporterV10.dll',
        'CadReadingExploration.AutoCADVisibilityExporterV13.dll'
    )
    $commands = @(
        'CADTEXTEXPORT5',
        'CADFRAMEEXPORT5',
        'CADSYMBOLEXPORT6',
        'CADORIENTEDTEXTEXPORT7',
        'CADPRIMITIVEEXPORT10',
        'CADVISIBILITYEXPORT13'
    )
    $expectedSuffixes = @(
        '.cad_text_export_v5.json',
        '.cad_frame_export_v5.json',
        '.cad_symbol_export_v6.json',
        '.cad_oriented_text_export_v7.json',
        '.cad_primitive_export_v10.json',
        '.cad_visibility_export_v13.json'
    )
}
else {
    $pluginNames = @('CadReadingExploration.AutoCADContentFingerprintExporterV18.dll')
    $commands = @('CADPREFILTEREXPORT18')
    $expectedSuffixes = @('.cad_content_fingerprint_v18.json')
}

$pluginPaths = foreach ($name in $pluginNames) {
    $path = Join-Path $resolvedPluginDir $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "$($policy.product_name) plugin not found: $path"
    }
    $path
}

$sessionDir = Split-Path -Parent ([System.IO.Path]::GetFullPath($ExecutionLog))
New-Item -ItemType Directory -Path $sessionDir -Force | Out-Null
$jobDir = Join-Path $sessionDir ("autocad-$($policy.release)-jobs")
New-Item -ItemType Directory -Path $jobDir -Force | Out-Null

$rows = New-Object System.Collections.Generic.List[object]
$index = 0

foreach ($drawingValue in $DrawingPath) {
    $index++
    $drawing = (Resolve-Path -LiteralPath $drawingValue).Path
    $beforeHash = Get-Sha256Hex -Path $drawing
    $dwgVersion = Get-DwgVersionCode -Path $drawing
    $compatibility = Test-AutoCadDwgCompatibility `
        -DwgVersion $dwgVersion -Policy $policy
    $started = Get-Date
    if (-not $compatibility.compatible) {
        $afterHash = Get-Sha256Hex -Path $drawing
        $rows.Add([pscustomobject]@{
            drawing_path = $drawing
            mode = $Mode
            backend = "autocad-$($policy.release)-dotnet-coreconsole"
            autocad_release = $policy.release
            autocad_api_version = $policy.api_version
            autocad_file_version = $acadVersion
            architecture = $architecture
            source_dwg_version = $dwgVersion
            max_dwg_version = $policy.max_dwg_version
            status = 'dwg_version_incompatible'
            message = $compatibility.reason
            elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
            input_sha256_before = $beforeHash
            input_sha256_after = $afterHash
            expected_outputs = ''
            stdout_log = $null
            stderr_log = $null
            terminated_accoreconsole_pids = ''
        })
        continue
    }

    $drawingDirectory = Split-Path -Parent $drawing
    $outputDirectory = [System.IO.Path]::GetFullPath(
        (Join-Path $drawingDirectory '..\输出')
    )
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($drawing)
    $expectedPaths = foreach ($suffix in $expectedSuffixes) {
        Join-Path $outputDirectory ($stem + $suffix)
    }
    $preexistingOutputs = @($expectedPaths | Where-Object {
            Test-Path -LiteralPath $_
        })
    if ($preexistingOutputs.Count -gt 0) {
        throw (
            'Refusing to reuse or overwrite pre-existing AutoCAD evidence: ' +
            ($preexistingOutputs -join '|')
        )
    }

    $jobStem = 'J{0:D4}__{1}' -f $index, ($stem -replace '[<>:"/\\|?*]', '_')
    $scriptPath = Join-Path $jobDir ($jobStem + '.scr')
    $stdoutPath = Join-Path $jobDir ($jobStem + '.stdout.log')
    $stderrPath = Join-Path $jobDir ($jobStem + '.stderr.log')

    $scriptLines = New-Object System.Collections.Generic.List[string]
    $scriptLines.Add('_.FILEDIA')
    $scriptLines.Add('0')
    $scriptLines.Add('_.CMDDIA')
    $scriptLines.Add('0')
    foreach ($plugin in $pluginPaths) {
        $scriptLines.Add('_.NETLOAD')
        $scriptLines.Add('"' + (Convert-ToCoreConsolePath -Path $plugin) + '"')
    }
    foreach ($command in $commands) {
        $scriptLines.Add($command)
    }
    $scriptLines.Add('_.QUIT')
    $scriptLines.Add('_Y')
    [System.IO.File]::WriteAllLines(
        $scriptPath,
        $scriptLines,
        [System.Text.Encoding]::ASCII
    )

    $arguments = @(
        '/i', ('"' + $drawing + '"'),
        '/s', ('"' + $scriptPath + '"'),
        '/l', 'en-US'
    )
    $process = Start-Process `
        -FilePath $coreConsole `
        -ArgumentList $arguments `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -PassThru
    $completed = $process.WaitForExit($PerDrawingTimeoutSeconds * 1000)
    $terminatedPids = New-Object System.Collections.Generic.List[int]
    $status = 'failed'
    $message = ''
    if (-not $completed) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        $terminatedPids.Add($process.Id)
        $status = 'timeout'
        $message = "AutoCAD Core Console exceeded ${PerDrawingTimeoutSeconds} seconds."
    }
    else {
        $missing = @($expectedPaths | Where-Object {
                -not (Test-Path -LiteralPath $_ -PathType Leaf)
            })
        if ($process.ExitCode -eq 0 -and $missing.Count -eq 0) {
            $status = 'success'
        }
        else {
            $message = (
                "exit_code=$($process.ExitCode); missing_outputs=" +
                ($missing -join '|')
            )
        }
    }

    $afterHash = Get-Sha256Hex -Path $drawing
    if ($afterHash -ne $beforeHash) {
        $status = 'input_hash_changed'
        $message = 'The analysis-copy DWG hash changed during AutoCAD execution.'
    }

    $rows.Add([pscustomobject]@{
        drawing_path = $drawing
        mode = $Mode
        backend = "autocad-$($policy.release)-dotnet-coreconsole"
        autocad_release = $policy.release
        autocad_api_version = $policy.api_version
        autocad_file_version = $acadVersion
        architecture = $architecture
        source_dwg_version = $dwgVersion
        max_dwg_version = $policy.max_dwg_version
        status = $status
        message = $message
        elapsed_seconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        input_sha256_before = $beforeHash
        input_sha256_after = $afterHash
        expected_outputs = $expectedPaths -join '|'
        stdout_log = $stdoutPath
        stderr_log = $stderrPath
        terminated_accoreconsole_pids = $terminatedPids.ToArray() -join '|'
    })
}

$rows.ToArray() |
    Export-Csv -LiteralPath $ExecutionLog -NoTypeInformation -Encoding UTF8
$rows.ToArray()
