#!/usr/bin/env bash
# Start vibalytics (run install.sh first if this is a fresh checkout)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"

if [ ! -f "$VENV/bin/python" ]; then
  echo "No virtual environment found. Run:  bash install.sh"
  exit 1
fi

if [ ! -d "$SCRIPT_DIR/frontend/dist" ]; then
  echo "Frontend not built. Run:  bash install.sh"
  exit 1
fi

echo "Starting vibalytics at http://localhost:8000"
cd "$SCRIPT_DIR/backend"
exec "$VENV/bin/uvicorn" main:app --host 0.0.0.0 --port 8000
