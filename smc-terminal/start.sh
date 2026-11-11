#!/usr/bin/env bash
# Linux/macOS: runs the gateway (which supervises the Python engine) and serves the UI in the browser.
# Windows users: double-click start.bat for the desktop app.
set -euo pipefail
cd "$(dirname "$0")"
WEB_PORT="${WEB_PORT:-5080}"
PY="${PYTHON:-python3}"

command -v "$PY" >/dev/null || { echo "Python 3.10+ tidak ditemukan"; exit 1; }
command -v dotnet >/dev/null || { echo ".NET SDK 8+ tidak ditemukan"; exit 1; }

if [ ! -x engine/.venv/bin/python ]; then "$PY" -m venv engine/.venv; fi
if ! cmp -s engine/requirements.txt engine/.venv/requirements.installed; then
  engine/.venv/bin/python -m pip install --disable-pip-version-check -q -r engine/requirements.txt
  cp engine/requirements.txt engine/.venv/requirements.installed
fi
dotnet build gateway/SmcTerminal.Gateway.csproj -c Release -nologo -v q

echo "SHARX INVEST: http://127.0.0.1:$WEB_PORT/  (Ctrl+C untuk berhenti)"
cd gateway && exec dotnet run -c Release --no-build -- --urls "http://127.0.0.1:$WEB_PORT" --Terminal:ManageEngine=true
