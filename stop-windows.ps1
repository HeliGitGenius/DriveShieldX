# DriveShieldX - stop dashboard
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$pidFile = Join-Path $root "dashboard.pid"
if (Test-Path $pidFile) {
    $pid = Get-Content $pidFile
    try { Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue; Write-Host "stopped dashboard (pid $pid)" } catch {}
    Remove-Item $pidFile -Force
} else {
    Get-Process | Where-Object { $_.Path -like "*streamlit*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Write-Host "stopped dashboard"
}
