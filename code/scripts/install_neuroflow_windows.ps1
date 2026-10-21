$ErrorActionPreference = 'Stop'
$bundle = Split-Path -Parent $MyInvocation.MyCommand.Path
$wheelhouse = Join-Path $bundle 'wheelhouse'
$wheels = @(Get-ChildItem $wheelhouse -Filter 'neuroflow-*.whl')
if ($wheels.Count -ne 1) { throw "Expected one NeuroFlow wheel in $wheelhouse; found $($wheels.Count)" }

$listed = @{}
foreach ($line in Get-Content (Join-Path $wheelhouse 'SHA256SUMS')) {
    if ($line -notmatch '^([A-Fa-f0-9]{64})\s+\*?(.+)$') { throw "Invalid checksum line: $line" }
    $name = $Matches[2]
    if ([IO.Path]::GetFileName($name) -ne $name) { throw "Invalid wheelhouse filename in checksum manifest: $name" }
    $file = Join-Path $wheelhouse $name
    if (-not (Test-Path $file)) { throw "Wheelhouse file is missing: $name" }
    $actual = (Get-FileHash $file -Algorithm SHA256).Hash
    if ($actual -ne $Matches[1]) { throw "Checksum mismatch: $name" }
    $listed[$name] = $true
}
if ($listed.Count -ne @(Get-ChildItem $wheelhouse -Filter '*.whl').Count) {
    throw 'Checksum manifest does not cover every wheel in the wheelhouse.'
}

$wheelName = $wheels[0].Name
if ($wheelName -notmatch '^neuroflow-(?<version>.+)-py3-none-any\.whl$') { throw "Unexpected NeuroFlow wheel name: $wheelName" }
$version = $Matches.version
if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) {
    throw 'Python 3.11 x64 with the Python Launcher is required. Install it from https://www.python.org/downloads/windows/, then rerun this installer.'
}
& py -3.11 -c 'import platform,sys; assert sys.version_info[:2] == (3,11) and platform.architecture()[0] == "64bit"'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 x64 is required. Install it from python.org and rerun this installer.' }

$stateRoot = Join-Path $env:LOCALAPPDATA 'NeuroFlow'
$venv = Join-Path $stateRoot "venvs\$version"
$python = Join-Path $venv 'Scripts\python.exe'
$neuroflow = Join-Path $venv 'Scripts\neuroflow.exe'
New-Item -ItemType Directory -Path (Split-Path -Parent $venv) -Force | Out-Null
if (Test-Path $venv) {
    if (-not (Test-Path $neuroflow)) { throw "Existing environment is incomplete: $venv" }
    $installedVersion = & $python -c 'from importlib.metadata import version; print(version("neuroflow"))'
    if ($installedVersion -ne $version) { throw "Environment $venv contains NeuroFlow $installedVersion, expected $version." }
} else {
    New-Item -ItemType Directory -Path $venv | Out-Null
    $createdVenv = $true
    try {
        & py -3.11 -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment: $venv" }
        & (Join-Path $venv 'Scripts\pip.exe') install --no-index --find-links $wheelhouse "neuroflow==$version"
        if ($LASTEXITCODE -ne 0) { throw 'Offline NeuroFlow installation failed.' }
        & $neuroflow --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Installed NeuroFlow command failed its first-launch check.' }
        $createdVenv = $false
    } catch {
        $installError = $_
        if ($createdVenv -and (Test-Path -LiteralPath $venv)) {
            try {
                Remove-Item -LiteralPath $venv -Recurse -Force
                Write-Warning "Removed incomplete NeuroFlow environment after installation failure: $venv"
            } catch {
                Write-Warning "Could not remove incomplete NeuroFlow environment ${venv}: $($_.Exception.Message)"
            }
        }
        throw $installError
    }
}
$siteProfile = Join-Path $env:APPDATA 'NeuroFlow\site.json'
if (-not (Test-Path $siteProfile)) {
    $legacyProfile = Join-Path $HOME '.config\prisma-workbench\site.json'
    if (Test-Path $legacyProfile) { $siteProfile = $legacyProfile }
}
if (-not (Test-Path $siteProfile)) {
    & $neuroflow --site $siteProfile init-site
    if ($LASTEXITCODE -ne 0) { throw 'NeuroFlow site-profile initialization failed.' }
} else {
    & $neuroflow --site $siteProfile refresh-site
    if ($LASTEXITCODE -ne 0) { throw 'NeuroFlow bundled pipeline path refresh failed.' }
}

$bin = Join-Path $stateRoot 'bin'
New-Item -ItemType Directory -Path $bin -Force | Out-Null
$cmd = "@echo off`r`n`"$neuroflow`" %*`r`n"
[IO.File]::WriteAllText((Join-Path $bin 'neuroflow.cmd'), $cmd, [Text.Encoding]::ASCII)
Copy-Item (Join-Path $bundle 'neuroflow-ui-windows.ps1') (Join-Path $bin 'neuroflow-ui.ps1') -Force
Set-Content -Path (Join-Path $stateRoot 'current-version.txt') -Value $version -Encoding ascii

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\NeuroFlow'
New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
$shortcutPath = Join-Path $startMenu 'NeuroFlow.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe).Source
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $bin 'neuroflow-ui.ps1')`""
$shortcut.WorkingDirectory = $stateRoot
$shortcut.Save()

& $neuroflow --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Installed NeuroFlow command failed its first-launch check.' }
Write-Host "NeuroFlow $version installed for this Windows user. Start it from the NeuroFlow Start Menu shortcut."
Write-Host "Command launcher: $(Join-Path $bin 'neuroflow.cmd')"
Write-Host "Site profile: $siteProfile"
Write-Host 'Neuroimaging tools and container images remain separately installed dependencies.'
