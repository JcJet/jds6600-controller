from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import jds6600
from serial.tools import list_ports


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str
    hwid: str
    manufacturer: str
    vid: Optional[int]
    pid: Optional[int]


@dataclass(frozen=True)
class ProbeResult:
    port: str
    ok: bool
    error: Optional[str] = None


_LINUX_TTYS_RE = re.compile(r"^/dev/ttyS\d+$")


def _safe_str(x) -> str:
    return "" if x is None else str(x)


def list_serial_ports() -> List[PortInfo]:
    result: List[PortInfo] = []
    for p in list_ports.comports():
        result.append(
            PortInfo(
                device=_safe_str(getattr(p, "device", "")),
                description=_safe_str(getattr(p, "description", "")),
                hwid=_safe_str(getattr(p, "hwid", "")),
                manufacturer=_safe_str(getattr(p, "manufacturer", "")),
                vid=getattr(p, "vid", None),
                pid=getattr(p, "pid", None),
            )
        )
    return result


def list_linux_by_id_ports() -> List[str]:
    """Return stable Linux symlinks under /dev/serial/by-id (if present)."""
    if os.name != "posix":
        return []
    base = Path("/dev/serial/by-id")
    if not base.exists():
        return []
    out: List[str] = []
    for p in sorted(base.iterdir()):
        try:
            if p.is_symlink():
                out.append(str(p))
        except Exception:
            continue
    return out


def probe_port(port: str) -> ProbeResult:
    try:
        with jds6600.JDS6600(port=port) as fg:
            # A minimal read to confirm the device responds.
            # This should fail quickly on non-device ports (esp. USB serial mismatches).
            fg.get_channels()
        return ProbeResult(port=port, ok=True, error=None)
    except Exception as e:
        return ProbeResult(port=port, ok=False, error=str(e))


def _score_port(p: PortInfo) -> int:
    dev = (p.device or "").lower()
    desc = (p.description or "").lower()
    hwid = (p.hwid or "").lower()
    man = (p.manufacturer or "").lower()

    score = 0

    # Prefer USB-class serial devices
    if p.vid is not None and p.pid is not None:
        score += 120
    if "usb" in desc or "usb" in hwid or "usb" in man:
        score += 80

    # Linux: prefer ttyUSB/ttyACM, strongly de-prefer ttyS*
    if dev.startswith("/dev/ttyusb") or dev.startswith("/dev/ttyacm"):
        score += 110
    if _LINUX_TTYS_RE.match(dev):
        score -= 300  # avoid picking /dev/ttyS* by default

    # Windows: de-prefer legacy COM ports with no VID/PID information
    if os.name == "nt":
        if p.vid is None and p.pid is None and dev.startswith("com"):
            score -= 120

    # De-prefer bluetooth-ish ports
    if "bluetooth" in desc or "bluetooth" in hwid:
        score -= 200

    # Slight penalty for useless descriptions
    if desc in ("n/a", "", "unknown"):
        score -= 20

    return score


def find_first_jds6600(port_hint: Optional[str] = None) -> str:
    """
    Auto-detect strategy (noob-friendly + reliable):
      1) If port_hint provided: try it and fail loudly if not OK.
      2) On Linux, prefer stable /dev/serial/by-id/* symlinks (USB serial devices).
      3) Otherwise, rank ports: USB ports first, avoid /dev/ttyS*.
      4) Probe in that order and return first that behaves like JDS6600.
    """
    if port_hint:
        res = probe_port(port_hint)
        if res.ok:
            return res.port
        raise RuntimeError(f"Port '{port_hint}' is not accessible or not a JDS6600. Error: {res.error}")

    # Linux: try stable by-id ports first
    by_id = list_linux_by_id_ports()
    for p in by_id:
        res = probe_port(p)
        if res.ok:
            return p

    ports = list_serial_ports()
    if not ports:
        raise RuntimeError("No serial ports found.")

    # Sort by score descending
    ports_sorted = sorted(ports, key=_score_port, reverse=True)

    failures: List[str] = []
    for p in ports_sorted:
        # On Linux, skip ttyS* entirely unless nothing else works.
        if os.name == "posix" and _LINUX_TTYS_RE.match((p.device or "").lower()):
            continue
        res = probe_port(p.device)
        if res.ok:
            return p.device
        failures.append(f"{p.device}: {res.error}")

    # If nothing found, as a last resort allow ttyS* (rare, but keeps compatibility)
    if os.name == "posix":
        for p in ports_sorted:
            if not _LINUX_TTYS_RE.match((p.device or "").lower()):
                continue
            res = probe_port(p.device)
            if res.ok:
                return p.device
            failures.append(f"{p.device}: {res.error}")

    raise RuntimeError("Could not find JDS6600 on any port.\n" + "\n".join(failures))
