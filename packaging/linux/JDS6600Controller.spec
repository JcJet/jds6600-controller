# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

project_dir = Path.cwd().resolve()  # build scripts cd into project root
entry = project_dir / "run_gui.py"

datas = [
    (str(project_dir / "commands.csv"), "."),
    (str(project_dir / "commands.example.csv"), "."),
    (str(project_dir / "README.md"), "."),
]

# include icon if present (optional)
icon_ico = project_dir / "assets" / "icon.ico"
icon_png = project_dir / "assets" / "icon.png"
if icon_ico.exists():
    icon = str(icon_ico)
elif icon_png.exists():
    icon = str(icon_png)
else:
    icon = None

a = Analysis(
    [str(entry)],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="JDS6600Controller",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,   # no console window
    icon=icon,       # PNG works on Linux; on Windows you should replace with .ico for a proper icon
)
