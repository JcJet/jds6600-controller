@echo off
setlocal

REM Build a Windows .exe using PyInstaller.
REM Run from project root in an activated venv.

python -c "import sys; print(sys.version)"
python -m pip install -U pip
pip install -r requirements.txt
pip install pyinstaller

pyinstaller --noconfirm --onefile --name JDS6600_Controller run_gui.py

echo.
echo Build finished. Your exe is in: dist\JDS6600_Controller.exe
echo.

endlocal
