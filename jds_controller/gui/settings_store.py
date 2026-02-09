from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    """Return the location of settings.json.

    Windows: %APPDATA%/JDS6600Controller/settings.json
    Linux/macOS: ~/.jds6600_controller/settings.json
    """
    home = Path.home()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(home)
        return Path(base) / "JDS6600Controller" / "settings.json"
    return home / ".jds6600_controller" / "settings.json"


def load_settings() -> dict:
    """Load settings.json (best-effort)."""
    p = settings_path()
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_settings(data: dict) -> None:
    """Save settings.json (best-effort)."""
    p = settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # best effort
        pass
