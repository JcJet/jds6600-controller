from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _call_with_channel(obj: Any, method_name: str, channel: int) -> Any:
    """
    Call method on JDS6600 object, trying keyword argument 'channel' first,
    then falling back to positional. This keeps compatibility with slight API variations.
    """
    m = getattr(obj, method_name)
    try:
        return m(channel=channel)
    except TypeError:
        return m(channel)


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _normalize_wave(v: Any) -> str:
    if v is None:
        return "unknown"
    # common returns: int code or string
    if isinstance(v, str):
        return v.strip().lower()
    return str(v).strip().lower()


def read_device_state(fg: Any) -> Dict[str, Dict[str, Any]]:
    """
    Read current generator state from device.

    Returns:
      {
        "ch1": {"on": bool, "wave": str, "freq_hz": float|None, "ampl_v": float|None, "offs_v": float|None, "duty_pct": float|None},
        "ch2": {...}
      }
    """
    # channels on/off
    ch1_on = ch2_on = None
    try:
        # jds6600 typically has get_channels()
        ch = fg.get_channels()
        # can be tuple/list of two bools
        if isinstance(ch, (tuple, list)) and len(ch) >= 2:
            ch1_on, ch2_on = bool(ch[0]), bool(ch[1])
    except Exception:
        pass

    out: Dict[str, Dict[str, Any]] = {}
    for ch in (1, 2):
        on = ch1_on if ch == 1 else ch2_on
        wave = None
        freq = None
        ampl = None
        offs = None
        duty = None

        # Each getter call is isolated: if one fails, others can still work.
        try:
            wave = _normalize_wave(_call_with_channel(fg, "get_waveform", ch))
        except Exception:
            wave = "unknown"

        try:
            freq = _safe_float(_call_with_channel(fg, "get_frequency", ch))
        except Exception:
            freq = None

        try:
            ampl = _safe_float(_call_with_channel(fg, "get_amplitude", ch))
        except Exception:
            ampl = None

        try:
            offs = _safe_float(_call_with_channel(fg, "get_offset", ch))
        except Exception:
            offs = None

        try:
            duty = _safe_float(_call_with_channel(fg, "get_dutycycle", ch))
        except Exception:
            duty = None

        out[f"ch{ch}"] = {
            "on": bool(on) if on is not None else None,
            "wave": wave,
            "freq_hz": freq,
            "ampl_v": ampl,
            "offs_v": offs,
            "duty_pct": duty,
        }
    return out


def _fmt_hz(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    # 10000.0 -> 10000
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))}Hz"
    # keep up to 3 decimals but trim zeros
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return f"{s}Hz"


def _fmt_v(v: Optional[float], decimals: int = 2) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{decimals}f}v"


def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    return f"{v:.1f}%"


def format_device_state(state: Dict[str, Dict[str, Any]]) -> str:
    def fmt_ch(ch_key: str, label: str) -> str:
        s = state.get(ch_key, {})
        on = s.get("on")
        on_txt = "on" if on is True else ("off" if on is False else "n/a")
        wave = s.get("wave", "unknown")
        freq = _fmt_hz(s.get("freq_hz"))
        ampl = _fmt_v(s.get("ampl_v"), 1)
        offs = _fmt_v(s.get("offs_v"), 2)
        duty = _fmt_pct(s.get("duty_pct"))
        return f"{label}={on_txt}, wave: {wave}, freq: {freq}, ampl:{ampl}, offs: {offs}, duty: {duty}"

    return f"{fmt_ch('ch1','CH1')};  {fmt_ch('ch2','CH2')}"
