$TaskName = "ClaudeCodePendingApprovalMonitor"
$Script = Join-Path $env:USERPROFILE ".claude\tools\pending_approval_monitor.py"

$candidates = @(
  (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.13-64\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.12-64\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe")
)
$PythonW = $null
foreach ($c in $candidates) {
  if (Test-Path $c) { $PythonW = $c; break }
}
if (-not $PythonW) {
  $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($cmd) { $PythonW = $cmd.Path }
}
if (-not $PythonW) {
  Write-Host "pythonw.exe not found."
  exit 1
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -StartWhenAvailable `
  -DontStopOnIdleEnd `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

Register-ScheduledTask -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Settings $settings `
  -Principal $principal `
  -Description "Claude Code pending approval monitor" | Out-Null

Write-Host "Task registered: $TaskName"
