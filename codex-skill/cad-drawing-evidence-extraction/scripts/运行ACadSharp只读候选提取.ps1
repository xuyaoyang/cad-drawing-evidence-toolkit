param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [string]$WorkRoot,

    [string]$BuildRoot,

    [string]$PackagePath,

    [ValidateRange(1, 86400)]
    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = 'Stop'

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $stream = [System.IO.File]::OpenRead([System.IO.Path]::GetFullPath($Path))
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '')
    }
    finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Test-SynchronizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full -match '(?i)[\\/]OneDrive([\\/]|$)') { return $true }
    foreach ($name in @('OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')) {
        $root = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        $normalizedRoot = [System.IO.Path]::GetFullPath($root).TrimEnd('\')
        if (
            $full.Equals($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
            $full.StartsWith($normalizedRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)
        ) { return $true }
    }
    return $false
}

function Test-SameOrChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $candidatePath = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\')
    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    return (
        $candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith($rootPath + '\', [System.StringComparison]::OrdinalIgnoreCase)
    )
}

function ConvertTo-ProcessArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    if ($Value.Contains('"')) {
        throw 'Double quotes are not supported in portable-reader argument values.'
    }
    return '"' + $Value + '"'
}

$InputPath = (Resolve-Path -LiteralPath $InputPath).Path
if (-not (Test-Path -LiteralPath $InputPath -PathType Leaf)) {
    throw "Input DWG not found: $InputPath"
}
if (-not [System.IO.Path]::GetExtension($InputPath).Equals('.dwg', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Only a DWG file can be passed to this candidate reader: $InputPath"
}
if ([string]::IsNullOrWhiteSpace($WorkRoot)) {
    $WorkRoot = Join-Path $env:LOCALAPPDATA 'CadReadingToolkit\Work\acadsharp-portable'
}
if ([string]::IsNullOrWhiteSpace($BuildRoot)) {
    $BuildRoot = Join-Path $env:LOCALAPPDATA 'CadReadingToolkit\Portable\acadsharp-3.6.51\bin'
}
$WorkRoot = [System.IO.Path]::GetFullPath($WorkRoot)
$BuildRoot = [System.IO.Path]::GetFullPath($BuildRoot)
$repositoryRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if ([System.IO.Path]::GetPathRoot($WorkRoot).TrimEnd('\').Equals($WorkRoot.TrimEnd('\'), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "WorkRoot cannot be a disk root: $WorkRoot"
}
foreach ($candidate in @($WorkRoot, $BuildRoot)) {
    if (Test-SynchronizedPath -Path $candidate) {
        throw "Work/build roots cannot be in OneDrive or synchronized storage: $candidate"
    }
    if (Test-SameOrChildPath -Candidate $candidate -Root $repositoryRoot) {
        throw "Work/build roots cannot be inside the source repository: $candidate"
    }
}

$inputDirectory = Join-Path $WorkRoot 'input'
$analysisDirectory = Join-Path $WorkRoot 'analysis'
$logDirectory = Join-Path $WorkRoot 'logs'
foreach ($directory in @($inputDirectory, $analysisDirectory, $logDirectory)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$beforeSha256 = Get-Sha256Hex -Path $InputPath
$copyName = $beforeSha256.Substring(0, 12) + '-' + [System.IO.Path]::GetFileName($InputPath)
$copyPath = Join-Path $inputDirectory $copyName
Copy-Item -LiteralPath $InputPath -Destination $copyPath -Force
$copySha256 = Get-Sha256Hex -Path $copyPath
if (-not $copySha256.Equals($beforeSha256, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Analysis-copy SHA-256 mismatch; extraction was not started."
}

$builder = Join-Path (Split-Path -Parent $PSScriptRoot) 'portable\build_acadsharp_portable_reader.ps1'
$buildArguments = @{
    OutputDir = $BuildRoot
}
if (-not [string]::IsNullOrWhiteSpace($PackagePath)) {
    $buildArguments.PackagePath = $PackagePath
}
$build = & $builder @buildArguments
$executable = $build.Executable
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Portable reader executable was not produced: $executable"
}

$evidencePath = Join-Path $analysisDirectory 'acadsharp-portable-evidence.json'
$stdoutPath = Join-Path $logDirectory 'acadsharp-portable.stdout.log'
$stderrPath = Join-Path $logDirectory 'acadsharp-portable.stderr.log'
$arguments = @(
    '--input', $copyPath,
    '--output', $evidencePath,
    '--source-path', $InputPath,
    '--source-name', [System.IO.Path]::GetFileName($InputPath),
    '--source-sha256', $beforeSha256
)
$processArguments = foreach ($argument in $arguments) {
    ConvertTo-ProcessArgument -Value $argument
}
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $executable
$startInfo.Arguments = $processArguments -join ' '
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $startInfo
if (-not $process.Start()) {
    throw "Failed to start portable reader: $executable"
}
$completed = $process.WaitForExit($TimeoutSeconds * 1000)
$timedOut = -not $completed
if ($timedOut) {
    $process.Kill()
    $process.WaitForExit()
}
else {
    $process.WaitForExit()
}
$processExitCode = if ($timedOut) { 124 } else { $process.ExitCode }
$stdout = $process.StandardOutput.ReadToEnd()
$stderrText = $process.StandardError.ReadToEnd()
[System.IO.File]::WriteAllText($stdoutPath, $stdout, (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText($stderrPath, $stderrText, (New-Object System.Text.UTF8Encoding($false)))
$process.Dispose()

$afterSha256 = Get-Sha256Hex -Path $InputPath
$sourceUnchanged = $afterSha256.Equals($beforeSha256, [System.StringComparison]::OrdinalIgnoreCase)
$readerStatus = 'portable_read_failed'
if ($processExitCode -eq 0 -and (Test-Path -LiteralPath $evidencePath -PathType Leaf)) {
    $statusLine = Get-Content -LiteralPath $stdoutPath | Where-Object { $_ -like 'status=*' } | Select-Object -Last 1
    if ($statusLine) { $readerStatus = $statusLine.Substring('status='.Length) }
}

$runStatus = if (-not $sourceUnchanged) {
    'source_hash_changed_safe_stop'
}
elseif ($processExitCode -ne 0) {
    'portable_read_failed'
}
else {
    $readerStatus
}

$runRecord = [ordered]@{
    schema_version = 'acadsharp-portable-run/0.1'
    backend = 'ACadSharp'
    backend_version = '3.6.51'
    status = $runStatus
    source_name = [System.IO.Path]::GetFileName($InputPath)
    source_sha256_before = $beforeSha256
    source_sha256_after = $afterSha256
    source_unchanged = $sourceUnchanged
    analysis_copy = $copyPath
    analysis_copy_sha256 = $copySha256
    evidence_output = $evidencePath
    process_exit_code = $processExitCode
    timed_out = $timedOut
    timeout_seconds = $TimeoutSeconds
    formal_backend_equivalent = $false
    absence_proven = $false
    original_dwg_opened_by_parser = $false
}
$runRecordPath = Join-Path $analysisDirectory 'acadsharp-portable-run.json'
$runRecord | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $runRecordPath -Encoding UTF8

if (-not $sourceUnchanged) {
    throw "Original source SHA-256 changed during the run; outputs are not usable. See $runRecordPath"
}
if ($processExitCode -ne 0) {
    $stderr = if (Test-Path -LiteralPath $stderrPath) { Get-Content -LiteralPath $stderrPath -Raw } else { '' }
    throw "Portable reader failed with exit code $processExitCode. $stderr"
}

[pscustomobject]@{
    Status = $runStatus
    SourceUnchanged = $sourceUnchanged
    SourceSha256 = $beforeSha256
    Evidence = $evidencePath
    RunRecord = $runRecordPath
    Stdout = $stdoutPath
    Stderr = $stderrPath
}
