Param(
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

Write-Host "== JDS6600Controller: Windows build =="

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectRoot

# Detect arch of selected Python
$arch = & $Python -c "import struct; print('win32' if struct.calcsize('P')==4 else 'win64')"
if ($LASTEXITCODE -ne 0) { throw "Failed to run Python to detect architecture." }

# Per-arch venv (so you can build both variants on one machine)
$VenvDir = ".venv-$arch"
& $Python -m venv $VenvDir
$PyExe = Join-Path $VenvDir "Scripts\python.exe"
& $PyExe -m pip install -U pip wheel setuptools
& $PyExe -m pip install -r requirements.txt
# Pillow helps PyInstaller convert PNG icon to ICO automatically
& $PyExe -m pip install pyinstaller pillow

# Build EXE (GUI, no console)
& $PyExe -m PyInstaller packaging\windows\JDS6600Controller.spec --clean --noconfirm
# Build EXE (GUI, no console)
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

# Save arch-specific copy (so both can exist side-by-side)
if (Test-Path "dist\JDS6600Controller.exe") {
  $out = "dist\JDS6600Controller-$arch.exe"
  Copy-Item "dist\JDS6600Controller.exe" $out -Force
  Write-Host "Built: $out"
} else {
  throw "Expected dist\JDS6600Controller.exe not found."
}

Write-Host ""
Write-Host "Tip: build both Win64 + Win32 by running this script with different Python interpreters (x64 and x86)."
