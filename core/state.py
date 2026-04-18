from __future__ import annotations

import os
from pathlib import Path


def get_data_dir() -> Path:
    """Return the runtime data directory for SURF.

    Priority:
    1. SURF_DATA_DIR env var
    2. local .surf/ folder in the project root
    """
    custom = os.getenv("SURF_DATA_DIR")
    if custom:
        return Path(custom).expanduser().resolve()

    root = Path(__file__).resolve().parent.parent
    return root / ".surf"


def ensure_data_dir() -> Path:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def data_file(name: str) -> Path:
    return ensure_data_dir() / name
