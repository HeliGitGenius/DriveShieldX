# DriveShieldX — one-command boot for Windows PowerShell
# Usage (once):  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#         then:  .\start-windows.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

Write-Host ""
Write-Host "======================================================="
Write-Host "  DriveShieldX - starting local development stack      "
Write-Host "======================================================="
Write-Host ""

if (-not (Test-Path .env)) { Copy-Item .env.example .env }

Write-Host "[1/3] Preparing Python virtual environment..."
if (-not (Test-Path .venv)) {
    python -m venv .venv
}
. .\.venv\Scripts\Activate.ps1
pip install --quiet --upgrade pip

Write-Host "[2/3] Installing Python dependencies (a few minutes on first run)..."
pip install --quiet -r requirements.txt

python -c "from database.db_manager import init_database; init_database()"

# load env vars from .env
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)\s*=\s*(.*)$') {
        $k = $matches[1].Trim(); $v = $matches[2].Trim().Trim('"')
        [Environment]::SetEnvironmentVariable($k, $v, 'Process')
    }
}

Write-Host "[3/3] Launching Streamlit dashboard on http://localhost:8501 ..."
$streamlit = Start-Process -FilePath "$root\.venv\Scripts\python.exe" `
    -ArgumentList "-m","streamlit","run","dashboard/app.py",`
                  "--server.port","8501","--server.address","0.0.0.0",`
                  "--server.headless","true","--browser.gatherUsageStats","false" `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$root\dashboard.log" -RedirectStandardError "$root\dashboard.err.log"
$streamlit.Id | Out-File -Encoding ascii "$root\dashboard.pid"

Start-Sleep -Seconds 8
Start-Process "http://localhost:8501"
Write-Host ""
Write-Host "======================================================="
Write-Host "  DriveShieldX is booting."
Write-Host "  Open  http://localhost:8501"
Write-Host ""
Write-Host "  Sign in (demo):"
Write-Host "     Admin             : admin@speedcam.com    / admin123"
Write-Host "     Traffic officer   : officer@speedcam.com  / officer123"
Write-Host "     Vehicle owner     : use 'Sign up as vehicle owner' on the first page"
Write-Host ""
Write-Host "  Logs:  Get-Content .\dashboard.log -Wait"
Write-Host "  Stop:  .\stop-windows.ps1"
Write-Host "======================================================="
