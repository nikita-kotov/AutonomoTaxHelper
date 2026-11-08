#!/bin/sh
# Starts Autonomo Tax Helper on macOS and Linux: the console stays open while the app runs.
# The first start creates the virtual environment and installs the packages.
# Open the interface at http://127.0.0.1:5057/ (the browser opens by itself); stop with Exit in the page or Ctrl+C.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv || { echo "python3 with the venv module is required (Debian/Ubuntu: sudo apt install python3-venv)."; exit 1; }
fi
if ! .venv/bin/python -c "import flask, PIL" 2>/dev/null; then
  echo "Installing requirements..."
  .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
exec .venv/bin/python app.py
