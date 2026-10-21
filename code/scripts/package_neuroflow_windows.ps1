param(
    [Parameter(Mandatory = $true)][string]$WheelPath,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$wheel = (Resolve-Path $WheelPath).Path
if ([IO.Path]::GetFileName($wheel) -notmatch '^neuroflow-(?<version>.+)-py3-none-any\.whl$') {
    throw "Expected a NeuroFlow wheel, got: $wheel"
}
$version = $Matches.version
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path $output) {
    if (Get-ChildItem -Force $output | Select-Object -First 1) {
        throw "Output directory must be empty: $output"
    }
} else {
    New-Item -ItemType Directory -Path $output | Out-Null
}

$tempRoot = [IO.Path]::GetTempPath()
$stage = Join-Path $tempRoot ("neuroflow-windows-bundle-" + [guid]::NewGuid().ToString('N'))
$bundleName = "NeuroFlow-Windows-x86_64-cp311-$version"
$bundle = Join-Path $stage $bundleName
$wheelhouse = Join-Path $bundle 'wheelhouse'
New-Item -ItemType Directory -Path $wheelhouse -Force | Out-Null

try {
    & py -3.11 -m pip download --only-binary=:all: --dest $wheelhouse $wheel
    if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the Windows CPython 3.11 wheelhouse.' }
    Copy-Item (Join-Path $root 'code\scripts\install_neuroflow_windows.ps1') $bundle
    Copy-Item (Join-Path $root 'code\scripts\uninstall_neuroflow_windows.ps1') $bundle
    Copy-Item (Join-Path $root 'code\scripts\neuroflow-ui-windows.ps1') $bundle
    Copy-Item (Join-Path $root 'docs\INSTALL_WINDOWS.md') $bundle
    $manifest = Get-ChildItem $wheelhouse -Filter '*.whl' | Sort-Object Name | ForEach-Object {
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $($_.Name)"
    }
    $manifest | Set-Content -Encoding ascii (Join-Path $wheelhouse 'SHA256SUMS')

    $archive = Join-Path $output "$bundleName.zip"
    Compress-Archive -Path $bundle -DestinationPath $archive -CompressionLevel Optimal
    $archiveHash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    "$archiveHash  $([IO.Path]::GetFileName($archive))" | Set-Content -Encoding ascii "$archive.sha256"
    Write-Host "Windows install bundle ready: $archive"
} finally {
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
}
