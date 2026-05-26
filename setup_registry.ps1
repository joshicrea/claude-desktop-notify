$AppId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
$KeyPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\$AppId"
if (-not (Test-Path $KeyPath)) {
  New-Item -Path $KeyPath -Force | Out-Null
}
Set-ItemProperty -Path $KeyPath -Name 'Enabled' -Value 1 -Type DWord
Set-ItemProperty -Path $KeyPath -Name 'ShowBanner' -Value 1 -Type DWord
Set-ItemProperty -Path $KeyPath -Name 'ShowInActionCenter' -Value 1 -Type DWord
Write-Host "Registry updated: Enabled=1 ShowBanner=1 ShowInActionCenter=1"
