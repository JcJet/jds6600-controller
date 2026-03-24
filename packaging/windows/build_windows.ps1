Param(
  [string]$Python = "python",
  [ValidateSet("auto", "32", "64")]
  [string]$Arch = "auto",
  [switch]$LegacyWin7,
  [string]$PyInstallerVersion = "",
  [string]$PillowVersion = ""
)

$ErrorActionPreference = "Stop"

Write-Host "== JDS6600Controller: Windows build =="
if ($LegacyWin7) {
  Write-Host "Target: Windows 7 legacy"
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
Set-Location $ProjectRoot

# Detect arch of selected Python
$detectedArch = & $Python -c "import struct; print('win32' if struct.calcsize('P')==4 else 'win64')"
if ($LASTEXITCODE -ne 0) { throw "Failed to run Python to detect architecture." }

if ($Arch -ne "auto") {
  $expected = if ($Arch -eq "32") { "win32" } else { "win64" }
  if ($detectedArch -ne $expected) {
    throw "Selected Python architecture ($detectedArch) does not match requested -Arch $Arch."
  }
}

$arch = $detectedArch

# Per-arch / per-target venv (so builds can coexist on one machine)
$venvSuffix = if ($LegacyWin7) { "$arch-win7legacy" } else { $arch }
$VenvDir = ".venv-$venvSuffix"
& $Python -m venv $VenvDir
$PyExe = Join-Path $VenvDir "Scripts\python.exe"
& $PyExe -m pip install -U pip wheel setuptools
& $PyExe -m pip install -r requirements.txt

if ([string]::IsNullOrWhiteSpace($PyInstallerVersion)) {
  $PyInstallerVersion = if ($LegacyWin7) { "pyinstaller==5.13.2" } else { "pyinstaller" }
}
if ([string]::IsNullOrWhiteSpace($PillowVersion)) {
  $PillowVersion = if ($LegacyWin7) { "pillow<11" } else { "pillow" }
}

# Pillow helps PyInstaller convert PNG icon to ICO automatically.
& $PyExe -m pip install $PyInstallerVersion $PillowVersion

# Build EXE (GUI, no console)
& $PyExe -m PyInstaller packaging\windows\JDS6600Controller.spec --clean --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

# Save target-specific copy (so builds can exist side-by-side)
if (-not (Test-Path "dist\JDS6600Controller.exe")) {
  throw "Expected dist\JDS6600Controller.exe not found."
}

if ($LegacyWin7) {
  $out = "dist\JDS6600Controller-win7-legacy.exe"
} else {
  $out = "dist\JDS6600Controller-$arch.exe"
}
Copy-Item "dist\JDS6600Controller.exe" $out -Force
Write-Host "Built: $out"

if ($LegacyWin7) {
  $launcherSrc = "packaging\windows\JDS6600Controller-win7-legacy-launch.cmd"
  if (Test-Path $launcherSrc) {
    Copy-Item $launcherSrc "dist\JDS6600Controller-win7-legacy-launch.cmd" -Force
  }
  if (Test-Path "WINDOWS7-LEGACY-README.txt") {
    Copy-Item "WINDOWS7-LEGACY-README.txt" "dist\WINDOWS7-LEGACY-README.txt" -Force
  }
}

if ($LegacyWin7) {
  Write-Host "Note: Windows 7 requires Python 3.8-based build and KB2533623 on the target system."
}
