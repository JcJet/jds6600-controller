#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-python3}"

# Ensure we run from the project root
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

echo "== JDS6600Controller: AppImage build =="

$PYTHON -m venv .venv
source .venv/bin/activate
pip install -U pip wheel setuptools
pip install -r requirements.txt
pip install pyinstaller

# Build one-file binary
pyinstaller packaging/linux/JDS6600Controller.spec --clean --noconfirm

mkdir -p AppDir/usr/bin AppDir/usr/share/applications AppDir/usr/share/icons/hicolor/256x256/apps

# copy binary
cp -f dist/JDS6600Controller AppDir/usr/bin/JDS6600Controller

# icon (AppImage expects a top-level icon file matching Icon=... in .desktop)
if [ -f assets/icon.png ]; then
  cp -f assets/icon.png AppDir/JDS6600Controller.png
  # also keep a copy in standard location (optional)
  cp -f assets/icon.png AppDir/usr/share/icons/hicolor/256x256/apps/JDS6600Controller.png || true
fi

# desktop file (AppImage expects a top-level .desktop)
cat > AppDir/JDS6600Controller.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=JDS6600 Controller
Exec=JDS6600Controller
Icon=JDS6600Controller
Categories=Utility;
Terminal=false
EOF

# also keep a copy in standard location (optional)
cp -f AppDir/JDS6600Controller.desktop AppDir/usr/share/applications/JDS6600Controller.desktop || true

# AppRun
cat > AppDir/AppRun <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/JDS6600Controller" "$@"
EOF
chmod +x AppDir/AppRun
chmod +x AppDir/usr/bin/JDS6600Controller

# appimagetool (download if missing)
if ! command -v appimagetool >/dev/null 2>&1; then
  echo "Downloading appimagetool..."
  curl -L -o appimagetool https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x appimagetool

  # GitHub Actions runners often do not provide /dev/fuse, so running AppImages
  # directly may fail with: dlopen(): error loading libfuse.so.2
  # We avoid this by extracting appimagetool and running its AppRun directly.
  echo "Extracting appimagetool (FUSE-less mode)..."
  rm -rf squashfs-root
  ./appimagetool --appimage-extract >/dev/null
  APPIMAGETOOL=./squashfs-root/AppRun
else
  APPIMAGETOOL=appimagetool
fi

ARCH=x86_64 "$APPIMAGETOOL" AppDir "JDS6600Controller-x86_64.AppImage"
echo "Done: JDS6600Controller-x86_64.AppImage"
