# JDS6600 Controller installer (Windows 10/11)
# Run in PowerShell from project root:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#   .\scripts\install_windows.ps1

$ErrorActionPreference = "Stop"

Write-Host "== JDS6600 Controller installer (Windows) =="

# Check python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
  Write-Host "Python not found. Install Python 3 from python.org and re-run." -ForegroundColor Red
  exit 1
}

Set-Location (Join-Path $PSScriptRoot "..")

python -m venv .venv
.\.venv\Scripts\python -m pip install -U pip
.\.venv\Scripts\pip install -r requirements.txt

Write-Host ""
Write-Host "Done."
Write-Host "Run GUI:"
Write-Host "  .\.venv\Scripts\python run_gui.py"
Write-Host ""
Write-Host "Note: On Windows tkinter is included with standard python.org builds."
Write-Host "If COM port access fails, ensure the device driver is installed (CH340/CH341 for VID:PID 1a86:7523)."
