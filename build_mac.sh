#!/bin/sh
# GE150 preset loader - build macOS app (dist/GE150PresetLoader.app + zip)
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo ".venv not found. Create it first:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pyinstaller"
  exit 1
fi
.venv/bin/python tools/build_exe.py
