param([ValidateSet('start', 'stop', 'status')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$dataDir = Join-Path $projectRoot 'data'
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$scriptPath = Join-Path $projectRoot 'collector.py'
function Find-Python {
    # Some PCs have no python on PATH, or only the Microsoft Store alias (WindowsApps); also search install folders.
    $candidates = @()
    $cmd = Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch 'WindowsApps' } | Select-Object -First 1
    if ($cmd) { $candidates += $cmd.Source }
    $launcher = Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) { try { $found = (& $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null); if ($found) { $candidates += $found.Trim() } } catch { } }
    $roots = @($env:ProgramFiles)
    if ($env:LOCALAPPDATA) { $roots += Join-Path $env:LOCALAPPDATA 'Programs\Python' }
    foreach ($root in $roots) {
        if ($root -and (Test-Path -LiteralPath $root)) {
            $candidates += Get-ChildItem -LiteralPath $root -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'python.exe' } | Where-Object { Test-Path -LiteralPath $_ }
        }
    }
    foreach ($exe in $candidates) {
        try { $ok = (& $exe -c 'import sys; print(int(sys.version_info >= (3, 10)))' 2>$null) } catch { $ok = $null }
        if ($ok -and "$ok".Trim() -eq '1') { return $exe }
    }
    throw 'Python 3.10+ not found. Install Python or add it to PATH.'
}
$python = Find-Python
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -match '^python(w)?(\.exe)?$' -and $_.CommandLine -and $_.CommandLine.Contains($scriptPath)
})
if ($Action -eq 'status') {
    if ($running.Count) { Write-Host "Collector running. PID: $($running.ProcessId -join ', ')" }
    else { Write-Host 'Collector is not running.' }
    & $python $scriptPath --status
    $log = Join-Path $dataDir 'collector.log'
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Encoding UTF8 -Tail 8 }
    exit $LASTEXITCODE
}
if ($Action -eq 'stop') {
    if ($running.Count) {
        New-Item -ItemType File -Path (Join-Path $dataDir 'stop.request') -Force | Out-Null
        Write-Host 'Stop requested. Current request may take up to 15 seconds.'
    } else { Write-Host 'Collector is not running.' }
    exit 0
}
if ($running.Count) { Write-Host 'Collector already running.'; exit 0 }
& $python $scriptPath --doctor
if ($LASTEXITCODE -ne 0) { throw 'Configuration check failed.' }
$pythonExe = $python
$process = Start-Process -FilePath $pythonExe -ArgumentList @('-u', ('"' + $scriptPath + '"')) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dataDir 'background.stdout.log') -RedirectStandardError (Join-Path $dataDir 'background.stderr.log') -PassThru
Start-Sleep -Seconds 2
$process.Refresh()
if ($process.HasExited) {
    Get-Content -LiteralPath (Join-Path $dataDir 'background.stderr.log') -Tail 12
    throw 'Collector exited. Check data/collector.log.'
}
Write-Host "Daily KST collector started. PID: $($process.Id). Missing API keys are reloaded from .env automatically."
