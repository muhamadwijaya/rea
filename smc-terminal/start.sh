#!/usr/bin/env bash
# Linux/macOS equivalent of start.bat (Windows users: double-click start.bat).
set -euo pipefail
cd "$(dirname "$0")"
ENGINE_PORT="${ENGINE_PORT:-8765}"
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

(cd engine && exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$ENGINE_PORT") &
ENGINE_PID=$!
trap 'kill $ENGINE_PID 2>/dev/null || true' EXIT
echo "SMC Terminal: http://localhost:$WEB_PORT/"
cd gateway && dotnet run -c Release --no-build -- --urls "http://localhost:$WEB_PORT" --Terminal:EngineUrl="http://127.0.0.1:$ENGINE_PORT"
