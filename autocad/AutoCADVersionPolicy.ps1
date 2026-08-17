$ErrorActionPreference = 'Stop'

function Get-AutoCadHostPolicy {
    param([Parameter(Mandatory = $true)][string]$FileVersion)

    $policies = @(
        [pscustomobject][ordered]@{
            release = '2023'
            product_name = 'AutoCAD 2023'
            api_version = 'R24.2'
            version_pattern = '^R?24\.2(?:\.|$)'
            backend_key = 'AutoCAD2023'
            route_status = 'autocad_2023_native_fallback_selected'
            max_dwg_version = 'AC1032'
            priority = 400
        },
        [pscustomobject][ordered]@{
            release = '2020'
            product_name = 'AutoCAD 2020'
            api_version = 'R23.1'
            version_pattern = '^R?23\.1(?:\.|$)'
            backend_key = 'AutoCAD2020'
            route_status = 'autocad_2020_native_fallback_selected'
            max_dwg_version = 'AC1032'
            priority = 300
        },
        [pscustomobject][ordered]@{
            release = '2018'
            product_name = 'AutoCAD 2018'
            api_version = 'R22.0'
            version_pattern = '^R?22\.0(?:\.|$)'
            backend_key = 'AutoCAD2018'
            route_status = 'autocad_2018_native_fallback_selected'
            max_dwg_version = 'AC1032'
            priority = 200
        },
        [pscustomobject][ordered]@{
            release = '2014'
            product_name = 'AutoCAD 2014'
            api_version = 'R19.1'
            version_pattern = '^R?19\.1(?:\.|$)'
            backend_key = 'AutoCAD2014'
            route_status = 'autocad_2014_native_fallback_selected'
            max_dwg_version = 'AC1027'
            priority = 100
        }
    )
    return @($policies | Where-Object { $FileVersion -match $_.version_pattern }) |
        Select-Object -First 1
}

function Get-SupportedAutoCadPolicies {
    foreach ($version in @('R24.2', 'R23.1', 'R22.0', 'R19.1')) {
        Get-AutoCadHostPolicy -FileVersion $version
    }
}

function Get-DwgVersionCode {
    param([Parameter(Mandatory = $true)][string]$Path)

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $stream = [System.IO.File]::Open(
        $resolved,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite
    )
    try {
        $buffer = New-Object byte[] 6
        $read = $stream.Read($buffer, 0, $buffer.Length)
        if ($read -ne 6) { return 'UNRECOGNIZED' }
        $value = [System.Text.Encoding]::ASCII.GetString($buffer)
        if ($value -notmatch '^AC\d{4}$') { return 'UNRECOGNIZED' }
        return $value
    }
    finally {
        $stream.Dispose()
    }
}

function Test-AutoCadDwgCompatibility {
    param(
        [Parameter(Mandatory = $true)][string]$DwgVersion,
        [Parameter(Mandatory = $true)]$Policy
    )

    $recognized = (
        $DwgVersion -match '^AC\d{4}$' -and
        $Policy.max_dwg_version -match '^AC\d{4}$'
    )
    $compatible = $false
    $reason = 'dwg_version_unrecognized'
    if ($recognized) {
        $sourceNumber = [int]$DwgVersion.Substring(2)
        $maximumNumber = [int]$Policy.max_dwg_version.Substring(2)
        $compatible = $sourceNumber -le $maximumNumber
        $reason = if ($compatible) {
            'dwg_version_supported'
        } else {
            'dwg_version_newer_than_host'
        }
    }
    return [pscustomobject][ordered]@{
        recognized = $recognized
        compatible = $compatible
        reason = $reason
        source_dwg_version = $DwgVersion
        max_dwg_version = $Policy.max_dwg_version
        host_release = $Policy.release
    }
}

function Get-PortableExecutableArchitecture {
    param([Parameter(Mandatory = $true)][string]$Path)

    $resolved = (Resolve-Path -LiteralPath $Path).Path
    $stream = [System.IO.File]::OpenRead($resolved)
    $reader = New-Object System.IO.BinaryReader($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) { return 'unknown' }
        $stream.Seek(0x3C, [System.IO.SeekOrigin]::Begin) | Out-Null
        $peOffset = $reader.ReadInt32()
        $stream.Seek($peOffset, [System.IO.SeekOrigin]::Begin) | Out-Null
        if ($reader.ReadUInt32() -ne 0x00004550) { return 'unknown' }
        $machine = $reader.ReadUInt16()
        if ($machine -eq 0x8664) { return 'x64' }
        if ($machine -eq 0x014C) { return 'x86' }
        return ('machine-0x{0:X4}' -f $machine)
    }
    catch {
        return 'unknown'
    }
    finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}
