#!/usr/bin/env bash
# Jalankan SERIALX di Linux (Ubuntu 22.04+). Backend C# opsional.
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
python -c "import PySide6" 2>/dev/null || pip install -r requirements.txt
if [ ! -f bin/serialx-server ] && command -v dotnet >/dev/null; then
  echo "[SERIALX] Membangun backend C#..."
  dotnet publish backend/SerialX.Server -c Release -r linux-x64 --self-contained -o bin || \
    echo "[SERIALX] build C# gagal -> fallback Python."
fi
exec python main.py "$@"
