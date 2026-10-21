$ErrorActionPreference = 'Stop'
$stateRoot = Join-Path $env:LOCALAPPDATA 'NeuroFlow'
$versionFile = Join-Path $stateRoot 'current-version.txt'
if (-not (Test-Path $versionFile)) { throw 'NeuroFlow is not installed for this user.' }
$version = (Get-Content $versionFile -Raw).Trim()
$neuroflow = Join-Path $stateRoot "venvs\$version\Scripts\neuroflow.exe"
if (-not (Test-Path $neuroflow)) { throw "NeuroFlow $version is missing. Rerun the installer." }
$logRoot = Join-Path $stateRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $logRoot "ui-$stamp.log"
$stderr = Join-Path $logRoot "ui-$stamp.error.log"
$server = Start-Process -FilePath $neuroflow -ArgumentList 'serve' -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
$ready = $false
try {
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if ($server.HasExited) { break }
        try {
            Invoke-WebRequest 'http://127.0.0.1:8765/' -UseBasicParsing -TimeoutSec 1 | Out-Null
            $ready = $true
            break
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $ready) {
        throw "NeuroFlow UI did not start. Check logs: $stdout and $stderr"
    }
    Start-Process 'http://127.0.0.1:8765'
    $server.WaitForExit()
} finally {
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
