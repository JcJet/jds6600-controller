"""Persisted auto-resume state for a command file.

The GUI supports continuing script execution after an application restart.
Resume must be safe:
- only for a saved file
- only if file contents are identical to what was executed (SHA256 matches)

This module centralizes validation, persistence and clearing logic so the
main App code stays readable.
"""

from __future__ import annotations

import os
import time
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

from .settings_store import load_settings, save_settings


class ResumeStore:
    """Handles persisting and validating a single resume point."""

    def __init__(self) -> None:
        self.available: bool = False
        self.info: Optional[Dict[str, Any]] = None
        self.checkpoint: Optional[Dict[str, Any]] = None

    @staticmethod
    def file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                h.update(chunk)
        return h.hexdigest()

    def invalidate(self) -> None:
        """Clear in-memory state only (keeps persisted resume)."""
        self.available = False
        self.info = None
        self.checkpoint = None

    def clear(self) -> None:
        """Remove persisted resume point and clear in-memory state."""
        s = load_settings()
        if not isinstance(s, dict):
            s = {}
        if 'resume' in s:
            try:
                del s['resume']
            except Exception:
                pass
            save_settings(s)
        self.invalidate()

    def load_for_file(self, path: Optional[Path], *, dirty: bool) -> None:
        """Load persisted resume if it matches this saved file."""
        self.invalidate()
        if dirty or path is None or (not path.exists()):
            return

        s = load_settings()
        if not isinstance(s, dict):
            return
        r = s.get('resume')
        if not isinstance(r, dict):
            return
        if int(r.get('v', 0)) != 1:
            return

        fp = r.get('file_path')
        fsha = r.get('file_sha256')
        ck = r.get('checkpoint')
        if not (isinstance(fp, str) and fp and isinstance(fsha, str) and fsha and isinstance(ck, dict)):
            return

        try:
            cur_abs = os.path.abspath(str(path))
            saved_abs = os.path.abspath(fp)
        except Exception:
            cur_abs = str(path)
            saved_abs = fp
        if cur_abs != saved_abs:
            return

        try:
            cur_sha = self.file_sha256(path)
        except Exception:
            return
        if cur_sha != fsha:
            return

        self.available = True
        self.info = r
        self.checkpoint = ck

    def persist(self, path: Optional[Path], *, dirty: bool, checkpoint: Dict[str, Any], executed_sha256: Optional[str] = None) -> None:
        """Persist resume checkpoint for the given saved, clean file."""
        if dirty or path is None or (not path.exists()):
            return
        try:
            file_sha = self.file_sha256(path)
        except Exception:
            return
        if executed_sha256 and executed_sha256 != file_sha:
            return
        s = load_settings()
        if not isinstance(s, dict):
            s = {}
        info = {
            'v': 1,
            'file_path': str(path),
            'file_sha256': file_sha,
            'checkpoint': checkpoint,
            'saved_at': int(time.time()),
        }
        s['resume'] = info
        save_settings(s)
        self.available = True
        self.info = info
        self.checkpoint = checkpoint
