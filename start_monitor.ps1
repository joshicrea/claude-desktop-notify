$ScriptPath = Join-Path $env:USERPROFILE ".claude\tools\pending_approval_monitor.py"

# pythonw.exe を探す（ストア版より実体版を優先）
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
  Write-Host "pythonw.exe not found. Install Python from python.org first."
  exit 1
}

# 既存プロセスがあれば停止
Get-CimInstance Win32_Process -Filter 'Name="pythonw.exe"' -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -like '*pending_approval_monitor*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Process -FilePath $PythonW -ArgumentList "`"$ScriptPath`"" -WindowStyle Hidden
Write-Host "Monitor started: $PythonW $ScriptPath"
