from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, List

import jds6600

from .commands import FreqStep, Step, WaitStep, StopStep, estimate_remaining_wait_time
from .util import fmt_seconds, sleep_with_control


@dataclass
class RunnerState:
    paused: bool = False
    stopped: bool = False
    skip_wait: bool = False


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
) -> int:
    """
    Run steps on the device. Returns exit code:
      0 - ok
      4 - stopped by user
    """
    if state is None:
        state = RunnerState()

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
    try:
        if not dry_run:
            fg = jds6600.JDS6600(port=port)
            fg.connect()
            status(f"Connected to {port}")

        total = len(steps)

        for i, step in enumerate(steps):
            if state.stopped:
                status("Stopped.")
                return 4

            est_remaining = _estimate_remaining_wait(steps, i + 1, fixed_wait_seconds)
            progress(i, est_remaining, step)

            # Pause gate
            while state.paused and not state.stopped:
                sleep_with_control(0.1, is_paused=is_paused, is_stopped=is_stopped)

            if state.stopped:
                status("Stopped.")
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

            elif isinstance(step, StopStep):
                status(f"[{i+1}/{total}] stop (line {step.source_line}) | remaining waits: {fmt_seconds(est_remaining)}")
                if dry_run:
                    continue
                fg.set_channels(channel1=False, channel2=False)

            elif isinstance(step, WaitStep):
                eff_seconds = float(fixed_wait_seconds) if fixed_wait_seconds is not None else float(step.seconds)
                # If user requested 'next', skip this wait immediately (also allows skipping the next wait if pressed earlier)
                if getattr(state, 'skip_wait', False):
                    state.skip_wait = False
                    eff_seconds = 0.0
                status(f"[{i+1}/{total}] wait {eff_seconds}s (line {step.source_line}) | remaining waits: {fmt_seconds(est_remaining)}")

                def on_tick(rem: float) -> None:
                    if tick_wait_updates:
                        status(f"  waiting... {fmt_seconds(rem)} left")

                if dry_run:
                    continue

                sleep_with_control(
                    eff_seconds,
                    is_paused=is_paused,
                    is_stopped=is_stopped,
                    is_skip=is_skip,
                    on_tick=on_tick if tick_wait_updates else None,
                    tick_interval=0.25
                )
                # If skip was pressed during the wait, consume it
                if getattr(state, 'skip_wait', False):
                    state.skip_wait = False
            else:
                raise RuntimeError(f"Unknown step type: {type(step)}")

        status("Done.")
        return 0

    finally:
        if fg is not None:
            try:
                fg.close()
            except Exception:
                pass
