param(
    [string]$OutputDir,

    [string]$PackageCacheRoot,

    [string]$PackagePath
)

$ErrorActionPreference = 'Stop'

$packageVersion = '3.6.51'
$packageSha256 = 'E66741A44848C6D1F9CF935DA72716F6A84924EA5D5EC494F5644C41AA98D97B'
$packageUrl = 'https://api.nuget.org/v3-flatcontainer/acadsharp/3.6.51/acadsharp.3.6.51.nupkg'
$source = Join-Path $PSScriptRoot 'ACadSharpPortableReader.cs'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'

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

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $env:LOCALAPPDATA 'CadReadingToolkit\Portable\acadsharp-3.6.51\bin'
}
if ([string]::IsNullOrWhiteSpace($PackageCacheRoot)) {
    $PackageCacheRoot = Join-Path $env:LOCALAPPDATA 'CadReadingToolkit\Portable\package-cache'
}

$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
$PackageCacheRoot = [System.IO.Path]::GetFullPath($PackageCacheRoot)
$repositoryRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
foreach ($candidate in @($OutputDir, $PackageCacheRoot)) {
    if (Test-SynchronizedPath -Path $candidate) {
        throw "Build and package paths cannot be in OneDrive/synchronized storage: $candidate"
    }
    if (Test-SameOrChildPath -Candidate $candidate -Root $repositoryRoot) {
        throw "Build and package paths cannot be inside the source repository: $candidate"
    }
}
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Reader source not found: $source"
}
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw "64-bit .NET Framework C# compiler not found: $compiler"
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
New-Item -ItemType Directory -Path $PackageCacheRoot -Force | Out-Null

if ([string]::IsNullOrWhiteSpace($PackagePath)) {
    $PackagePath = Join-Path $PackageCacheRoot "acadsharp.$packageVersion.nupkg"
    if (-not (Test-Path -LiteralPath $PackagePath -PathType Leaf)) {
        Invoke-WebRequest -Uri $packageUrl -OutFile $PackagePath
    }
}
$PackagePath = (Resolve-Path -LiteralPath $PackagePath).Path
$actualPackageSha256 = (Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash
if (-not $actualPackageSha256.Equals($packageSha256, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "ACadSharp package SHA-256 mismatch. Expected $packageSha256, got $actualPackageSha256"
}

$expanded = Join-Path $PackageCacheRoot "acadsharp-$packageVersion"
$assembly = Join-Path $expanded 'lib\net48\ACadSharp.dll'
if (-not (Test-Path -LiteralPath $assembly -PathType Leaf)) {
    New-Item -ItemType Directory -Path $expanded -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($PackagePath, $expanded)
}
if (-not (Test-Path -LiteralPath $assembly -PathType Leaf)) {
    throw "Pinned ACadSharp net48 assembly was not found after extraction: $assembly"
}

$targetAssembly = Join-Path $OutputDir 'ACadSharp.dll'
$targetExe = Join-Path $OutputDir 'ACadSharpPortableReader.exe'
Copy-Item -LiteralPath $assembly -Destination $targetAssembly -Force

& $compiler /nologo /target:exe /platform:x64 /optimize+ /checked+ `
    /reference:System.Core.dll `
    /reference:System.Web.Extensions.dll `
    /reference:$targetAssembly `
    /out:$targetExe `
    $source
if ($LASTEXITCODE -ne 0) {
    throw "ACadSharp portable reader compilation failed with exit code $LASTEXITCODE"
}

[pscustomobject]@{
    Backend = 'ACadSharp'
    BackendVersion = $packageVersion
    PackageSha256 = $actualPackageSha256
    Executable = $targetExe
    Assembly = $targetAssembly
}
