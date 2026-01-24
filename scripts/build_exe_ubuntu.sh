#!/usr/bin/env bash
set -euo pipefail

python3 -c "import sys; print(sys.version)"
python3 -m pip install -U pip
python3 -m pip install -r requirements.txt
python3 -m pip install pyinstaller

pyinstaller --noconfirm --onefile --name jds6600_controller run_cli.py

echo "Build finished: dist/jds6600_controller"
