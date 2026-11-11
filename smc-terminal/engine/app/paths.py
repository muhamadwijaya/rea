"""Where the engine keeps durable files (virtual-trading journal, dividend calendar cache).

The gateway passes its data folder in SHARX_DATA_DIR; run on its own, the engine uses the same default
location (Windows: %LOCALAPPDATA%\\SharxInvest, elsewhere ~/.local/share/sharx-invest).
"""

from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    configured = os.environ.get("SHARX_DATA_DIR")
    if configured:
        path = Path(configured)
    elif os.name == "nt":
        path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SharxInvest"
    else:
        path = Path.home() / ".local" / "share" / "sharx-invest"
    path.mkdir(parents=True, exist_ok=True)
    return path
