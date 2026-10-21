param(
    [string]$Version,
    [switch]$NoPrompt
)

$ErrorActionPreference = 'Stop'
$stateRoot = Join-Path $env:LOCALAPPDATA 'NeuroFlow'
$currentFile = Join-Path $stateRoot 'current-version.txt'
if (-not $Version -and (Test-Path $currentFile)) { $Version = (Get-Content $currentFile -Raw).Trim() }
if (-not $Version -or $Version -notmatch '^\d+\.\d+\.\d+(?:[A-Za-z0-9.+!-]*)$') {
    throw 'Specify an installed NeuroFlow semantic version (for example 0.4.11), or install NeuroFlow first.'
}
$venv = Join-Path $stateRoot "venvs\$Version"
$venvRoot = [IO.Path]::GetFullPath((Join-Path $stateRoot 'venvs'))
$venv = [IO.Path]::GetFullPath($venv)
if (-not [string]::Equals((Split-Path -Parent $venv), $venvRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to remove a path outside the versioned NeuroFlow environments.'
}
if (-not (Test-Path $venv)) { Write-Host "NeuroFlow $Version is not installed."; exit 0 }
if (-not $NoPrompt) {
    $answer = Read-Host "Remove NeuroFlow $Version? User configuration and job data will be kept (y/N)"
    if ($answer -notin @('y', 'Y')) { Write-Host 'Cancelled.'; exit 0 }
}

Remove-Item $venv -Recurse -Force
if ((Test-Path $currentFile) -and (Get-Content $currentFile -Raw).Trim() -eq $Version) {
    Remove-Item $currentFile -Force
    $bin = Join-Path $stateRoot 'bin'
    Remove-Item (Join-Path $bin 'neuroflow.cmd'), (Join-Path $bin 'neuroflow-ui.ps1') -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\NeuroFlow') -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Removed the NeuroFlow $Version environment. Site configuration and job data were preserved."
