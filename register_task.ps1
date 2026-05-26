# Register Claude Code Pending Approval Monitor for auto-start at logon
# Uses Windows Startup folder (no scheduled task / no admin needed).

$StartupDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup"
$VbsPath = Join-Path $StartupDir "ClaudeCodePendingApprovalMonitor.vbs"
$Script = Join-Path $env:USERPROFILE ".claude\tools\pending_approval_monitor.py"

$candidates = @(
  (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.13-64\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.12-64\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"),
  (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\pythonw.exe")
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
  Write-Host "pythonw.exe not found. Install Python from python.org first."
  exit 1
}

if (-not (Test-Path $StartupDir)) {
  New-Item -ItemType Directory -Path $StartupDir -Force | Out-Null
}

$q = [char]34
# VBS: Run "cmd", windowstyle, waitOnReturn
# cmd needs to be: "pythonw.exe" "script.py"
# In VBS string literal that becomes: """pythonw.exe"" ""script.py"""
$vbsContent = "CreateObject(${q}WScript.Shell${q}).Run ${q}${q}${q}${PythonW}${q}${q} ${q}${q}${Script}${q}${q}${q}, 0, False"
Set-Content -Path $VbsPath -Value $vbsContent -Encoding ASCII -Force

Write-Host "Startup VBS created: $VbsPath"
Write-Host "Python: $PythonW"
Write-Host "Script: $Script"
Write-Host "Will auto-start at next logon."
