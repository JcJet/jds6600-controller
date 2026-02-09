from __future__ import annotations

import os
import sys
import time
import math
from dataclasses import dataclass
from typing import Optional


def fmt_seconds(sec: float) -> str:
    try:
        sec = float(sec)
    except Exception:
        return "--"
    if not math.isfinite(sec):
        return "∞"
    sec = max(0.0, sec)
    if sec < 60:
        return f"{sec:.1f}s"
    m = int(sec // 60)
    s = sec - m * 60
    if m < 60:
        return f"{m}m {s:.0f}s"
    h = int(m // 60)
    m2 = m - h * 60
    return f"{h}h {m2}m"


@dataclass
class KeyHelp:
    pause: str = "P"
    quit: str = "Q"
    help: str = "H"


class KeyReader:
    """
    Non-blocking single-key reader for terminal sessions.
    Works on Windows and POSIX terminals.
    """
    def __init__(self) -> None:
        self._is_windows = os.name == "nt"
        self._active = bool(sys.stdin.isatty())
        self._old_settings = None

    def __enter__(self) -> "KeyReader":
        if not self._active:
            return self
        if self._is_windows:
            return self
        import termios
        import tty
        fd = sys.stdin.fileno()
        self._old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._active or self._is_windows or self._old_settings is None:
            return
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_settings)

    def get_key(self) -> Optional[str]:
        if not self._active:
            return None
        if self._is_windows:
            import msvcrt
            if msvcrt.kbhit():
                return msvcrt.getwch()
            return None
        import select
        r, _, _ = select.select([sys.stdin], [], [], 0)
        if r:
            return sys.stdin.read(1)
        return None


def sleep_with_control(
    seconds: float,
    *,
    is_paused,
    is_stopped,
    is_skip=None,
    on_tick=None,
    tick_interval: float = 0.25
) -> None:
    """
    Sleep for given seconds while supporting pause/stop.
    - is_paused(): bool
    - is_stopped(): bool
    - is_skip(): bool (optional)
    - on_tick(remaining_seconds: float) optional
    """
    end = time.monotonic() + max(0.0, float(seconds))
    last_tick = 0.0
    while True:
        if is_stopped():
            return
        if is_skip is not None and is_skip():
            return
        if is_paused():
            time.sleep(0.05)
            end += 0.05
            continue
        now = time.monotonic()
        remaining = end - now
        if remaining <= 0:
            if on_tick:
                on_tick(0.0)
            return
        if on_tick and (now - last_tick) >= tick_interval:
            on_tick(max(0.0, remaining))
            last_tick = now
        time.sleep(0.05)
