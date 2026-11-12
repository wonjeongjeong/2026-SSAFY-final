param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'SSAFY-SeoulSubway-DailyCollector'
$startupFolder = [Environment]::GetFolderPath('Startup')
$startupLink = Join-Path $startupFolder ($taskName + '.lnk')
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
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $startupLink) { Remove-Item -LiteralPath $startupLink }
    Write-Host 'Automatic startup removed. Running collector is unchanged.'
    exit 0
}
$pythonExe = $python
$pythonWindowless = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (Test-Path -LiteralPath $pythonWindowless) { $pythonExe = $pythonWindowless }
$scriptPath = Join-Path $projectRoot 'collector.py'
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument ('-u "' + $scriptPath + '"') -WorkingDirectory $projectRoot
# Persistent runner checks windows in KST. Daily trigger also recovers a stopped runner.
$triggers = @((New-ScheduledTaskTrigger -AtLogOn), (New-ScheduledTaskTrigger -Daily -At '06:25'), (New-ScheduledTaskTrigger -Daily -At '17:25'))
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description 'Seoul subway collection; KST windows, weekday partitions, local API budgets.'
try {
    Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
    if (Test-Path -LiteralPath $startupLink) { Remove-Item -LiteralPath $startupLink }
    Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State
    Write-Host 'Scheduled task registered. PC must be awake, connected, and this user logged in.'
} catch {
    # Restricted school PCs can deny Task Scheduler registration for ordinary users.
    # A current-user login shortcut needs no password or administrator rights.
    Write-Host 'Task Scheduler registration failed. Installing current-user login startup instead.'
    New-Item -ItemType Directory -Path $startupFolder -Force | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($startupLink)
    $shortcut.TargetPath = Join-Path $PSHOME 'powershell.exe'
    $shortcut.Arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'background.ps1') + '" start'
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.WindowStyle = 7
    $shortcut.Save()
    Write-Host "Login startup registered: $startupLink"
    Write-Host 'The persistent runner handles daily KST windows. A manually stopped runner starts again at next login.'
}
