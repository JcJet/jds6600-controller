#!/usr/bin/env bash
set -euo pipefail

echo "== JDS6600 Controller installer (Ubuntu/Debian) =="

# 1) System deps
if command -v apt >/dev/null 2>&1; then
  sudo apt update
  sudo apt install -y python3 python3-venv python3-pip python3-tk
else
  echo "This installer currently supports apt-based systems (Ubuntu/Debian)."
  echo "Please install: python3, python3-venv, python3-pip, python3-tk"
fi

# 2) venv + deps
cd "$(dirname "$0")/.."
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -r requirements.txt

echo
echo "Done."
echo "Run:"
echo "  source .venv/bin/activate"
echo "  python run_gui.py"
echo
echo "If you need serial access without sudo:"
echo "  sudo usermod -aG dialout $USER"
echo "  (then log out/in)"
