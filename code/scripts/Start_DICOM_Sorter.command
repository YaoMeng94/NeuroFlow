#!/bin/bash
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_BIN="${NEUROFLOW_PYTHON:-$(command -v python3 || true)}"

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "Could not find Python 3. Install NeuroFlow or set NEUROFLOW_PYTHON."
  echo "$PYTHON_BIN"
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi

if ! "$PYTHON_BIN" -c 'import tkinter, pydicom' >/dev/null 2>&1; then
  echo "This Python environment needs tkinter and pydicom before the sorter can start."
  echo "Python: $PYTHON_BIN"
  echo
  read -r -p "Press Return to close this window."
  exit 1
fi

export PYTHONPATH="$PROJECT_DIR/code${PYTHONPATH:+:$PYTHONPATH}"
cd "$PROJECT_DIR"
exec "$PYTHON_BIN" -m neuroflow.standalone_gui
