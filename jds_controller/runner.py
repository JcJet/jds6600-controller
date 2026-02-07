from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, List

from .device_state import read_device_state, format_device_state

import jds6600

from .commands import FreqStep, Step, WaitStep, StopStep, ModStep, estimate_remaining_wait_time
from .util import fmt_seconds, sleep_with_control


@dataclass
class RunnerState:
    paused: bool = False
    stopped: bool = False
    skip_wait: bool = False
    resume_checkpoint: Optional[Dict[str, Any]] = None


StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, float, Step], None]
# Progress callback args: (index, total, est_remaining_seconds, step)


def _channels_from_selector(sel: Any, default_sel: str = "both") -> List[int]:
    if sel is None or sel == "":
        sel = default_sel
    if isinstance(sel, int):
        return [sel] if sel in (1, 2) else [1, 2]
    if isinstance(sel, str):
        s = sel.strip().lower()
        if s in {"1", "ch1", "channel1"}:
            return [1]
        if s in {"2", "ch2", "channel2"}:
            return [2]
        if s in {"both", "all", "12", "1+2"}:
            return [1, 2]
    return [1, 2]


def _call_set_method(fg: Any, key: str, *, channel: int, value: Any) -> None:
    method_name = f"set_{key}"
    method = getattr(fg, method_name, None)
    if not callable(method):
        raise AttributeError(f"Device object has no method '{method_name}' for option '{key}'")
    sig = inspect.signature(method)
    params = sig.parameters
    kwargs: Dict[str, Any] = {}
    if "channel" in params:
        kwargs["channel"] = channel
    if "value" in params:
        kwargs["value"] = value
        method(**kwargs)
    else:
        # fallback to positional
        if "channel" in params:
            method(channel=channel, value=value)  # type: ignore
        else:
            method(value)  # type: ignore


def _apply_channel_settings(fg: Any, channel: int, settings: Dict[str, Any]) -> None:
    for key, val in settings.items():
        if key in {"channel", "channels", "ch1", "ch2", "channel1", "channel2"}:
            continue
        if key == "frequency":
            fg.set_frequency(channel=channel, value=float(val))
            continue
        _call_set_method(fg, key, channel=channel, value=val)


def _estimate_remaining_wait(steps: Sequence[Step], start_index: int, fixed_wait: Optional[float]) -> float:
    if fixed_wait is None:
        return estimate_remaining_wait_time(steps, start_index)
    cnt = 0
    for s in steps[start_index:]:
        if isinstance(s, WaitStep):
            cnt += 1
    return float(cnt) * float(fixed_wait)




def _clamp(x: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(x)))


def _voltage_by_freq(f_hz: float) -> float:
    """Adaptive voltage curve (approximation).

    Based on: clamp(C * pow(f_hz, k), 5, 20)
    """
    f = float(f_hz)
    if f <= 0:
        return 5.0
    C = 1.835
    k = 0.223
    return _clamp(C * pow(f, k), 5.0, 20.0)
def run_sequence(
    steps: Sequence[Step],
    *,
    port: str,
    dry_run: bool = False,
    default_channel: str = "both",
    state: Optional[RunnerState] = None,
    on_status: Optional[StatusCallback] = None,
    on_progress: Optional[ProgressCallback] = None,
    tick_wait_updates: bool = True,
    fixed_wait_seconds: Optional[float] = None,
    on_device_state: Optional[Callable[[str], None]] = None,
    state_poll_interval: float = 1.0,
    resume: Optional[Dict[str, Any]] = None,
    on_checkpoint: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> int:
    """
    Run steps on the device. Returns exit code:
      0 - ok
      4 - stopped by user
    """
    if state is None:
        state = RunnerState()
    # --- resume / checkpoint ---
    # resume format (v=1):
    #   {"v":1,"step_index":int,"within":{...}}
    resume_step_index = 0
    resume_within: Optional[Dict[str, Any]] = None
    if isinstance(resume, dict):
        try:
            if int(resume.get("v", 1)) == 1:
                resume_step_index = max(0, int(resume.get("step_index", 0)))
                w = resume.get("within")
                if isinstance(w, dict):
                    resume_within = dict(w)
        except Exception:
            resume_step_index = 0
            resume_within = None

    _last_checkpoint_notify = 0.0

    def _set_checkpoint(step_index: int, step: Step, within: Optional[Dict[str, Any]] = None) -> None:
        """Update state.resume_checkpoint with a serializable dict. Never raise."""
        nonlocal _last_checkpoint_notify
        ck: Dict[str, Any] = {
            "v": 1,
            "step_index": int(step_index),
            "step_kind": type(step).__name__,
            "source_line": int(getattr(step, "source_line", 0) or 0),
        }
        if within is not None:
            ck["within"] = within
        # Store on shared state so GUI can read it even if queue/UI is blocked.
        try:
            state.resume_checkpoint = ck  # type: ignore[attr-defined]
        except Exception:
            pass

        if on_checkpoint is None:
            return
        now = time.monotonic()
        # Throttle UI callbacks to avoid flooding when update_ms is small.
        if _last_checkpoint_notify and (now - _last_checkpoint_notify) < 0.2:
            return
        _last_checkpoint_notify = now
        try:
            on_checkpoint(ck)
        except Exception:
            pass


    def status(msg: str) -> None:
        if on_status:
            on_status(msg)

    def progress(i: int, est_remaining: float, step: Step) -> None:
        if on_progress:
            on_progress(i, len(steps), est_remaining, step)

    def is_paused() -> bool:
        return bool(state.paused)

    def is_stopped() -> bool:
        return bool(state.stopped)

    def is_skip() -> bool:
        return bool(getattr(state, 'skip_wait', False))

    fg = None
    mod_both_warning_sent = False
    try:
        if not dry_run:
            fg = jds6600.JDS6600(port=port)
            fg.connect()
            status(f"Connected to {port}")

        total = len(steps)

        for i in range(resume_step_index, total):
            step = steps[i]

            # checkpoint at step boundary
            _set_checkpoint(i, step, None)

            if state.stopped:
                status("Stopped.")
                try:
                    state.resume_checkpoint = None  # type: ignore[attr-defined]
                except Exception:
                    pass
                return 4

            est_remaining = _estimate_remaining_wait(steps, i + 1, fixed_wait_seconds)
            progress(i, est_remaining, step)

            # Pause gate
            while state.paused and not state.stopped:
                sleep_with_control(0.1, is_paused=is_paused, is_stopped=is_stopped)

            if state.stopped:
                status("Stopped.")
                try:
                    state.resume_checkpoint = None  # type: ignore[attr-defined]
                except Exception:
                    pass
                return 4

            if isinstance(step, FreqStep):
                opts = step.options or {}
                chs = _channels_from_selector(opts.get("channel"), default_channel)

                status(f"[{i+1}/{total}] freq={step.hz} Hz (line {step.source_line}) | remaining waits: {fmt_seconds(est_remaining)}")

                if dry_run:
                    continue

                # enable/disable outputs if requested
                if "channels" in opts and isinstance(opts["channels"], dict):
                    c = opts["channels"]
                    fg.set_channels(
                        channel1=bool(c.get("channel1", c.get("ch1", True))),
                        channel2=bool(c.get("channel2", c.get("ch2", True))),
                    )

                # per-channel overrides
                per_ch: Dict[int, Dict[str, Any]] = {}
                if "ch1" in opts and isinstance(opts["ch1"], dict):
                    per_ch[1] = dict(opts["ch1"])
                if "ch2" in opts and isinstance(opts["ch2"], dict):
                    per_ch[2] = dict(opts["ch2"])

                if per_ch:
                    for ch, st in per_ch.items():
                        hz = float(st.pop("frequency", step.hz))
                        fg.set_frequency(channel=ch, value=hz)
                        _apply_channel_settings(fg, ch, st)
                else:
                    for ch in chs:
                        fg.set_frequency(channel=ch, value=float(step.hz))
                        _apply_channel_settings(fg, ch, opts)


            elif isinstance(step, ModStep):
                opts = step.options or {}
                status(
                    f"[{i+1}/{total}] mod start={step.start_hz}Hz end={step.end_hz}Hz "
                    f"time={step.time_s}s update={step.update_ms}ms dir={step.direction} adaptive-voltage={step.adaptive_voltage} "
                    f"repeat={step.repeat} (line {step.source_line}) | remaining waits: {fmt_seconds(est_remaining)}"
                )

                if dry_run:
                    continue

                if on_device_state is not None:
                    try:
                        on_device_state("Режим FM модуляции")
                    except Exception:
                        pass

                # enable/disable outputs if requested
                if "channels" in opts and isinstance(opts["channels"], dict):
                    c = opts["channels"]
                    fg.set_channels(
                        channel1=bool(c.get("channel1", c.get("ch1", True))),
                        channel2=bool(c.get("channel2", c.get("ch2", True))),
                    )

                # per-channel overrides (optional)
                per_ch: Dict[int, Dict[str, Any]] = {}
                if "ch1" in opts and isinstance(opts["ch1"], dict):
                    per_ch[1] = dict(opts["ch1"])
                if "ch2" in opts and isinstance(opts["ch2"], dict):
                    per_ch[2] = dict(opts["ch2"])

                sweep_channels = sorted(set(per_ch.keys())) if per_ch else _channels_from_selector(opts.get("channel"), default_channel)

                if (not mod_both_warning_sent) and len(sweep_channels) == 2:
                    mod_both_warning_sent = True
                    status(
                        "ВНИМАНИЕ! Модуляция запущена в режиме двух каналов. "
                        "При тестировании в этом режиме была обнаружена нестабильность сигнала.\n"
                        "Рекомендуется выбрать один канал, добавив к комманде  {\"channel\":\"1\"}. "
                        "И если требуется именно два канала, использовать функцию синхронизации в настройках генератора."
                    )

                # Apply constant settings once (waveform/duty/etc.).
                # If adaptive-voltage is enabled, we intentionally do NOT take amplitude from options.
                def _apply_static(ch: int, st: Dict[str, Any]) -> None:
                    st2 = dict(st)
                    st2.pop("frequency", None)
                    if step.adaptive_voltage:
                        st2.pop("amplitude", None)
                    _apply_channel_settings(fg, ch, st2)

                if per_ch:
                    for ch, st in per_ch.items():
                        _apply_static(ch, st)
                else:
                    base = dict(opts)
                    # remove non-setting keys
                    base.pop("channel", None)
                    base.pop("channels", None)
                    base.pop("ch1", None)
                    base.pop("ch2", None)
                    _apply_static(1, base) if 1 in sweep_channels else None
                    _apply_static(2, base) if 2 in sweep_channels else None

                # time_s is per sweep leg
                leg_seconds = max(0.001, float(step.time_s))

                # Resume inside mod (frequency sweep) step, if applicable.
                _resume_mod: Optional[Dict[str, Any]] = None
                if i == resume_step_index and isinstance(resume_within, dict) and resume_within.get("kind") == "mod":
                    _resume_mod = dict(resume_within)
                    # consume resume so it only applies to this step once
                    resume_within = None


                ui_update_interval = max(0.2, float(step.update_ms) / 1000.0)
                _last_ui_update = 0.0

                def _emit_mod_status(freq_hz: float, voltage: Optional[float]) -> None:
                    nonlocal _last_ui_update
                    if on_device_state is None:
                        return
                    now = time.monotonic()
                    if _last_ui_update and (now - _last_ui_update) < ui_update_interval:
                        return
                    _last_ui_update = now
                    try:
                        if voltage is None:
                            on_device_state(f"Режим FM модуляции: {freq_hz:.2f} Hz")
                        else:
                            on_device_state(f"Режим FM модуляции: {freq_hz:.2f} Hz, {voltage:.2f} V")
                    except Exception:
                        # never interfere with execution
                        pass


                def set_freq_and_adaptive_amp(freq_hz: float) -> None:
                    for ch in sweep_channels:
                        fg.set_frequency(channel=ch, value=float(freq_hz))
                    if step.adaptive_voltage:
                        v = _voltage_by_freq(float(freq_hz))
                        for ch in sweep_channels:
                            fg.set_amplitude(channel=ch, value=float(v))
                        _emit_mod_status(float(freq_hz), float(v))
                    else:
                        _emit_mod_status(float(freq_hz), None)

                def _calc_resume_start_k(saved_k: int, saved_updates: int, new_updates: int) -> int:
                    if new_updates <= 0:
                        return 0
                    try:
                        frac = float(saved_k) / float(max(1, saved_updates))
                    except Exception:
                        frac = 0.0
                    k2 = int(round(frac * float(new_updates)))
                    if k2 < 0:
                        return 0
                    if k2 > new_updates:
                        return new_updates
                    return k2

                def _consume_skip() -> bool:
                    # GUI "Next command" sets state.skip_wait=True. Reuse it for mod as well.
                    if getattr(state, 'skip_wait', False):
                        state.skip_wait = False
                        return True
                    return False

                def sweep(from_hz: float, to_hz: float, *, leg: str, apply_resume: bool = False) -> str:
                    # returns: "ok" | "stopped" | "skipped"
                    update_interval = max(0.001, float(step.update_ms) / 1000.0)
                    updates = int(max(1, round(leg_seconds / update_interval)))
                    if updates <= 0:
                        updates = 1

                    start_k = 0
                    # Apply resume only once, only for the matching leg.
                    nonlocal _resume_mod
                    if apply_resume and _resume_mod and str(_resume_mod.get("leg", "")).lower() == str(leg).lower():
                        try:
                            saved_k = int(_resume_mod.get("k", 0))
                        except Exception:
                            saved_k = 0
                        try:
                            saved_updates = int(_resume_mod.get("updates", updates))
                        except Exception:
                            saved_updates = updates
                        start_k = _calc_resume_start_k(saved_k, saved_updates, updates)
                        _resume_mod = None  # consume

                    if start_k < 0:
                        start_k = 0
                    if start_k > updates:
                        start_k = updates

                    for k in range(start_k, updates + 1):
                        if state.stopped:
                            return "stopped"
                        if _consume_skip():
                            return "skipped"

                        frac = k / float(updates)
                        freq = float(from_hz) + (float(to_hz) - float(from_hz)) * frac

                        _set_checkpoint(i, step, {
                            "kind": "mod",
                            "leg": str(leg),
                            "k": int(k),
                            "updates": int(updates),
                            "from_hz": float(from_hz),
                            "to_hz": float(to_hz),
                        })

                        set_freq_and_adaptive_amp(freq)
                        if k < updates:
                            sleep_with_control(
                                leg_seconds / float(updates),
                                is_paused=is_paused,
                                is_stopped=is_stopped,
                                is_skip=is_skip,
                            )
                            if _consume_skip():
                                return "skipped"
                    return "ok"

                # Build the modulation plan based on direction
                def run_one_cycle(*, apply_resume: bool = False) -> str:
                    d = (step.direction or "").strip().lower()
                    if d == "rise":
                        return sweep(step.start_hz, step.end_hz, leg="rise", apply_resume=apply_resume)
                    if d == "fall":
                        return sweep(step.end_hz, step.start_hz, leg="fall", apply_resume=apply_resume)

                    # rise-and-fall
                    # If resuming inside the fall leg, skip the rise leg in the first cycle.
                    if apply_resume and _resume_mod and str(_resume_mod.get("leg", "")).lower() == "fall":
                        return sweep(step.end_hz, step.start_hz, leg="fall", apply_resume=True)

                    r1 = sweep(step.start_hz, step.end_hz, leg="rise", apply_resume=apply_resume)
                    if r1 != "ok":
                        return r1
                    return sweep(step.end_hz, step.start_hz, leg="fall", apply_resume=False)

                result = "ok"
                if step.repeat:
                    first_cycle = True
                    while not state.stopped:
                        if _consume_skip():
                            result = "skipped"
                            break
                        result = run_one_cycle(apply_resume=first_cycle)
                        if result != "ok":
                            break
                        first_cycle = False
                else:
                    result = run_one_cycle(apply_resume=True)

                if result == "stopped" or state.stopped:
                    status("Stopped.")
                    try:
                        state.resume_checkpoint = None  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    return 4

                # If user pressed "Next command" during mod, end this step early and continue.
                if result == "skipped":
                    status("Skipped mod (next command).")

                try:
                    state.resume_checkpoint = None  # type: ignore[attr-defined]
                except Exception:
                    pass

            elif isinstance(step, StopStep):
                status(f"[{i+1}/{total}] stop (line {step.source_line}) | remaining waits: {fmt_seconds(est_remaining)}")
                if dry_run:
                    continue
                fg.set_channels(channel1=False, channel2=False)

            elif isinstance(step, WaitStep):
                eff_seconds = float(fixed_wait_seconds) if fixed_wait_seconds is not None else float(step.seconds)
                # Resume inside a wait step (if available)
                if i == resume_step_index and isinstance(resume_within, dict) and resume_within.get("kind") == "wait":
                    try:
                        eff_seconds = max(0.0, float(resume_within.get("remaining", eff_seconds)))
                    except Exception:
                        pass
                    # consume resume so it only applies once
                    resume_within = None
                # If user requested 'next', skip this wait immediately (also allows skipping the next wait if pressed earlier)
                if getattr(state, 'skip_wait', False):
                    state.skip_wait = False
                    eff_seconds = 0.0
                status(f"[{i+1}/{total}] wait {eff_seconds}s (line {step.source_line}) | remaining waits: {fmt_seconds(est_remaining)}")

                _last_state_poll = 0.0

                def on_tick(rem: float) -> None:
                    nonlocal _last_state_poll
                    _set_checkpoint(i, step, {"kind": "wait", "remaining": float(rem)})
                    if tick_wait_updates:
                        status(f"  waiting... {fmt_seconds(rem)} left")
                    if on_device_state is None:
                        return
                    now = time.monotonic()
                    if now - _last_state_poll < state_poll_interval:
                        return
                    _last_state_poll = now
                    try:
                        on_device_state(format_device_state(read_device_state(fg)))
                    except Exception:
                        # status polling must never interfere with execution
                        pass

                if dry_run:
                    continue

                sleep_with_control(
                    eff_seconds,
                    is_paused=is_paused,
                    is_stopped=is_stopped,
                    is_skip=is_skip,
                    on_tick=on_tick if (tick_wait_updates or on_device_state is not None) else None,
                    tick_interval=0.25
                )
                # If skip was pressed during the wait, consume it
                if getattr(state, 'skip_wait', False):
                    state.skip_wait = False
            else:
                raise RuntimeError(f"Unknown step type: {type(step)}")

        status("Done.")
        try:
            state.resume_checkpoint = None  # type: ignore[attr-defined]
        except Exception:
            pass
        return 0

    finally:
        if fg is not None:
            try:
                fg.close()
            except Exception:
                pass
