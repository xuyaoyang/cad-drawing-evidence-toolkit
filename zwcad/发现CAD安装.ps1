param(
    [ValidateSet('Any', 'ZWCAD', 'AutoCAD')]
    [string]$Vendor = 'Any',

    [switch]$AsJson,

    [switch]$RequireCurrentBackend
)

$ErrorActionPreference = 'Stop'

function Add-Candidate {
    param(
        [System.Collections.Generic.List[object]]$Rows,
        [string]$CandidateVendor,
        [string]$Root,
        [string]$Source
    )
    if ([string]::IsNullOrWhiteSpace($Root)) { return }
    try {
        $full = [System.IO.Path]::GetFullPath(
            ($Root -replace '^"|"$', '')
        ).TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    }
    catch { return }
    if (-not (Test-Path -LiteralPath $full -PathType Container)) {
        return
    }
    $key = ($CandidateVendor + '|' + $full).ToLowerInvariant()
    if (@($Rows | Where-Object { $_.dedupe_key -eq $key }).Count -gt 0) {
        return
    }

    if ($CandidateVendor -eq 'ZWCAD') {
        $exe = Join-Path $full 'ZWCAD.exe'
        $managed = Join-Path $full 'ZwManaged.dll'
        $database = Join-Path $full 'ZwDatabaseMgd.dll'
        $console = $null
        $backend = 'zwcad-dotnet-com'
    }
    else {
        $exe = Join-Path $full 'acad.exe'
        $managed = Join-Path $full 'AcMgd.dll'
        $database = Join-Path $full 'AcDbMgd.dll'
        $consolePath = Join-Path $full 'accoreconsole.exe'
        $console = if (Test-Path -LiteralPath $consolePath -PathType Leaf) {
            $consolePath
        } else { $null }
        $backend = 'autocad-dotnet-coreconsole'
    }
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        return
    }
    $apiReady = (
        (Test-Path -LiteralPath $exe -PathType Leaf) -and
        (Test-Path -LiteralPath $managed -PathType Leaf) -and
        (Test-Path -LiteralPath $database -PathType Leaf)
    )
    $version = $null
    try {
        $version = (Get-Item -LiteralPath $exe).VersionInfo.FileVersion
    }
    catch { $version = $null }
    if ($CandidateVendor -eq 'ZWCAD') {
        $currentReady = $apiReady
        $status = if ($currentReady) {
            'current_backend_supported'
        } else {
            'managed_api_incomplete'
        }
    }
    else {
        $currentReady = (
            $apiReady -and
            $null -ne $console -and
            $version -match '^R?24\.2(?:\.|$)'
        )
        $status = if ($currentReady) {
            'autocad_2023_backend_supported'
        } else {
            'autocad_backend_outside_validated_scope'
        }
    }
    $Rows.Add([pscustomobject][ordered]@{
        vendor = $CandidateVendor
        install_root = $full
        executable = $exe
        managed_api = $managed
        database_api = $database
        core_console = $console
        version = $version
        discovery_source = $Source
        host_api_ready = $apiReady
        current_toolkit_backend_ready = $currentReady
        backend = $backend
        compatibility_status = $status
        dedupe_key = $key
    })
}

$rows = New-Object 'System.Collections.Generic.List[object]'

if ($Vendor -in @('Any', 'ZWCAD')) {
    Add-Candidate $rows 'ZWCAD' $env:CAD_ZWCAD_ROOT 'environment'
}
if ($Vendor -in @('Any', 'AutoCAD')) {
    Add-Candidate $rows 'AutoCAD' $env:CAD_AUTOCAD_ROOT 'environment'
}

$uninstallRoots = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
foreach ($registryRoot in $uninstallRoots) {
    $items = @(Get-ItemProperty -Path $registryRoot -ErrorAction SilentlyContinue)
    foreach ($item in $items) {
        $name = [string]$item.DisplayName
        $location = [string]$item.InstallLocation
        if ([string]::IsNullOrWhiteSpace($location) -and $item.DisplayIcon) {
            $icon = ([string]$item.DisplayIcon) -replace ',\d+$', ''
            $location = Split-Path -Parent ($icon -replace '^"|"$', '')
        }
        if ($Vendor -in @('Any', 'ZWCAD') -and $name -match '(?i)ZWCAD|中望CAD') {
            Add-Candidate $rows 'ZWCAD' $location 'registry-uninstall'
        }
        if ($Vendor -in @('Any', 'AutoCAD') -and $name -match '(?i)AutoCAD') {
            Add-Candidate $rows 'AutoCAD' $location 'registry-uninstall'
        }
    }
}

$fileSystemProgramFiles = @(
    Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
        ForEach-Object { Join-Path $_.Root 'Program Files' }
)
$searchRoots = @(
    $env:ProgramFiles,
    ${env:ProgramFiles(x86)}
) + $fileSystemProgramFiles |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) } |
    Select-Object -Unique

foreach ($searchRoot in $searchRoots) {
    if ($Vendor -in @('Any', 'ZWCAD')) {
        foreach ($exe in @(Get-ChildItem -LiteralPath $searchRoot -Filter 'ZWCAD.exe' -File -Recurse -Depth 4 -ErrorAction SilentlyContinue)) {
            Add-Candidate $rows 'ZWCAD' $exe.DirectoryName 'filesystem'
        }
    }
    if ($Vendor -in @('Any', 'AutoCAD')) {
        foreach ($exe in @(Get-ChildItem -LiteralPath $searchRoot -Filter 'acad.exe' -File -Recurse -Depth 4 -ErrorAction SilentlyContinue)) {
            Add-Candidate $rows 'AutoCAD' $exe.DirectoryName 'filesystem'
        }
    }
}

$result = @(
    $rows |
        Sort-Object `
            @{ Expression = 'current_toolkit_backend_ready'; Descending = $true }, `
            @{ Expression = 'host_api_ready'; Descending = $true }, `
            @{ Expression = 'version'; Descending = $true }, `
            install_root |
        Select-Object * -ExcludeProperty dedupe_key
)

if ($RequireCurrentBackend) {
    $result = @($result | Where-Object { $_.current_toolkit_backend_ready })
    if ($result.Count -eq 0) {
        throw (
            'No current runnable backend was found. Supported native hosts ' +
            'are an API-ready ZWCAD installation or AutoCAD 2023 R24.2 ' +
            'with Core Console; other AutoCAD releases remain excluded.'
        )
    }
}

if ($AsJson) {
    $result | ConvertTo-Json -Depth 5
}
else {
    $result
}
