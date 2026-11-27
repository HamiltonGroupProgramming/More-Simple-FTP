"""Small JSON settings file for More Simple FTP (remembers choices such as the look)."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional


def settings_path() -> str:
    """Where settings live: %APPDATA%\\MoreSimpleFTP on Windows, ~/.config/MoreSimpleFTP elsewhere."""
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "MoreSimpleFTP", "settings.json")


def load_settings(path: Optional[str] = None) -> Dict[str, Any]:
    """Read settings; a missing or broken file just means "use the defaults"."""
    try:
        with open(path or settings_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: Dict[str, Any], path: Optional[str] = None) -> None:
    path = path or settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)
