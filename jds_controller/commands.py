from __future__ import annotations

import ast
import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union


@dataclass(frozen=True)
class FreqStep:
    hz: float
    options: Dict[str, Any]
    source_line: int


@dataclass(frozen=True)
class WaitStep:
    seconds: float
    source_line: int


@dataclass(frozen=True)
class StopStep:
    source_line: int


# Public step type consumed by runner.
@dataclass(frozen=True)
class ModStep:
    """Frequency modulation (sweep) step.

    time_s: duration for one sweep leg (start->end or end->start) in seconds.
    update_ms: how often to send new frequency (and adaptive voltage) to the device, in milliseconds.
    direction:
      - rise: start -> end
      - fall: end -> start
      - rise-and-fall: start -> end -> start
    repeat: if True, repeat cycles until stopped.
    adaptive_voltage: if True, adjust amplitude based on frequency.
    """
    start_hz: float
    end_hz: float
    time_s: float
    update_ms: float
    direction: str
    adaptive_voltage: bool
    repeat: bool
    options: Dict[str, Any]
    source_line: int



@dataclass(frozen=True)
class CycleRangeSpec:
    """Lazy frequency range specification for cycle.

    Generates an inclusive sequence from start_hz to end_hz using step_hz increments.
    step_hz may be positive or negative; sign defines direction.
    """
    start_hz: float
    end_hz: float
    step_hz: float


CycleItem = Union[float, CycleRangeSpec]


@dataclass(frozen=True)
class CycleStep:
    """Cycle through a list of frequencies (supports lazy ranges).

    items: list of floats and/or CycleRangeSpec.
    on_wait: seconds to hold each frequency (if >0).
    off_wait: optional seconds to hold pause_hz between frequencies (if provided and >0).
    pause_hz: frequency to set during off_wait (default 0 Hz).
    adaptive_voltage: if True, adjust amplitude based on frequency (same curve as mod).
    """
    items: List[CycleItem]
    on_wait: float
    off_wait: Optional[float]
    pause_hz: float
    adaptive_voltage: bool
    options: Dict[str, Any]
    source_line: int


Step = Union[FreqStep, WaitStep, StopStep, ModStep, CycleStep]


# Internal raw steps used only during parsing/expansion.
@dataclass(frozen=True)
class _FreqListRaw:
    freqs_hz: List[float]
    options: Dict[str, Any]
    source_line: int


RawStep = Union[Step, _FreqListRaw]


_WAIT_ALIASES = {"wait", "sleep", "delay"}
_STOP_ALIASES = {"stop", "off", "disable"}
_FREQ_ALIASES = {"freq", "frequency", "f"}
_CYCLE_ALIASES = {"cycle", "loop"}
_MOD_ALIASES = {"mod", "modulate", "sweep"}


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except Exception:
        return False

def _parse_bool(s: str) -> bool:
    v = (s or "").strip().lower()
    if v in {"1", "true", "yes", "y", "on"}:
        return True
    if v in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid boolean '{s}' (use true/false)")


def _normalize_direction(s: str) -> str:
    v = (s or "").strip().lower().replace("_", "-")
    v = v.replace(" ", "-")
    if v in {"rise", "up", "inc", "increase"}:
        return "rise"
    if v in {"fall", "down", "dec", "decrease"}:
        return "fall"
    if v in {"rise-and-fall", "rise-fall", "up-down", "up-and-down", "riseandfall"}:
        return "rise-and-fall"
    raise ValueError(
        f"invalid direction '{s}'. Use: rise, fall, rise-and-fall"
    )



def _looks_like_list(s: str) -> bool:
    st = s.strip()
    return st.startswith("[") and st.endswith("]")


def _consume_bracketed_token(cells: List[str], start_index: int, delimiter: str) -> Tuple[str, int]:
    """Join CSV cells starting at start_index until a [...] token is balanced.

    Needed when delimiter is ',' and the list contains commas, e.g.:
      freq,[1000,2000,3000]
    becomes cells: ['freq','[1000','2000','3000]'].

    Returns (token, next_index).
    """
    if start_index >= len(cells):
        return "", start_index

    first = (cells[start_index] or "").strip()
    if not first.lstrip().startswith("["):
        return first, start_index + 1

    parts: List[str] = []
    balance = 0
    i = start_index
    while i < len(cells):
        p = (cells[i] or "").strip()
        parts.append(p)
        balance += p.count("[") - p.count("]")
        if balance <= 0:
            i += 1
            break
        i += 1

    token = delimiter.join(parts)
    return token, i


def _parse_number_list(token: str, *, line_no: int) -> List[float]:
    try:
        obj = ast.literal_eval(token)
    except Exception as e:
        raise ValueError(f"Line {line_no}: invalid list syntax for frequencies: {e}")
    if not isinstance(obj, (list, tuple)):
        raise ValueError(f"Line {line_no}: frequency list must be like [1000,2000,3000]")
    out: List[float] = []
    for x in obj:
        try:
            out.append(float(x))
        except Exception:
            raise ValueError(f"Line {line_no}: list element '{x}' is not a number")
    if not out:
        raise ValueError(f"Line {line_no}: frequency list is empty")
    return out


def _parse_cycle_items(token: str, *, line_no: int) -> List[CycleItem]:
    """Parse cycle list token allowing numbers and range objects.

    Supports list syntax like:
      [30000, 44000, {"start": 55000, "end": 200000, "step": 0.1}, 1000000]

    Notes:
      - step defaults to 1.0
      - range is inclusive (includes end when aligned)
      - does NOT materialize the range into a list (keeps CycleRangeSpec)
      - best-effort tolerance for unquoted keys inside dict items (e.g. step: 0.1)
    """
    raw = (token or "").strip()
    if not (_looks_like_list(raw)):
        raise ValueError(
            f"Line {line_no}: cycle expects a list of frequencies, e.g. [1000,2000,3000]."
        )

    # First try JSON (more user-friendly for dicts), with a few tolerant cleanups.
    cur = re.sub(r",\s*([}\]])", r"\1", raw)  # remove trailing commas
    # quote bare keys inside dicts: {start:1} -> {"start":1}
    cur = re.sub(r"([,{]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', cur)

    obj: Any = None
    try:
        obj = json.loads(cur)
    except Exception:
        try:
            obj = ast.literal_eval(raw)
        except Exception as e:
            raise ValueError(
                f"Line {line_no}: can't parse cycle frequency list. "
                f"Use a list like [30000, 44000, {{\"start\":55000,\"end\":200000,\"step\":0.1}}, 1000000]. "
                f"Details: {e}"
            )

    if not isinstance(obj, (list, tuple)):
        raise ValueError(
            f"Line {line_no}: cycle list must be like [1000,2000,3000] or include ranges like "
            f"{{\"start\":55000,\"end\":200000,\"step\":0.1}}"
        )

    import math as _math

    items: List[CycleItem] = []
    for pos, x in enumerate(obj, start=1):
        # Number => single frequency
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            fx = float(x)
            if not _math.isfinite(fx):
                raise ValueError(f"Line {line_no}: cycle element #{pos} frequency must be a finite number")
            items.append(fx)
            continue
        # Dict => range spec
        if isinstance(x, dict):
            allowed = {"start", "end", "step"}
            extra = [k for k in x.keys() if str(k) not in allowed]
            if extra:
                extras = ", ".join(sorted(map(str, extra)))
                raise ValueError(
                    f"Line {line_no}: cycle element #{pos} has unknown field(s): {extras}. "
                    f"Allowed: start, end, step. Example: {{\"start\":55000,\"end\":200000,\"step\":0.1}}"
                )
            if "start" not in x or "end" not in x:
                raise ValueError(
                    f"Line {line_no}: cycle element #{pos} range must contain 'start' and 'end'. "
                    f"Example: {{\"start\":55000,\"end\":200000,\"step\":0.1}}"
                )
            try:
                start_hz = float(x.get("start"))
                end_hz = float(x.get("end"))
            except Exception:
                raise ValueError(f"Line {line_no}: cycle element #{pos} range start/end must be numbers")
            if not (_math.isfinite(start_hz) and _math.isfinite(end_hz)):
                raise ValueError(f"Line {line_no}: cycle element #{pos} range start/end must be finite numbers")
            step_val = x.get("step", 1)
            try:
                step_hz = float(step_val)
            except Exception:
                raise ValueError(f"Line {line_no}: cycle element #{pos} range step must be a number")
            if not _math.isfinite(step_hz):
                raise ValueError(f"Line {line_no}: cycle element #{pos} range step must be a finite number")
            if step_hz == 0:
                raise ValueError(f"Line {line_no}: cycle element #{pos} range step must not be 0")
            if start_hz == end_hz:
                items.append(float(start_hz))
                continue
            # Normalize step direction to match start->end
            step_hz = abs(step_hz)
            if end_hz < start_hz:
                step_hz = -step_hz
            items.append(CycleRangeSpec(start_hz=float(start_hz), end_hz=float(end_hz), step_hz=float(step_hz)))
            continue

        raise ValueError(
            f"Line {line_no}: cycle element #{pos} must be either a number (Hz) or a range object. "
            f"Example: [30000, 44000, {{\"start\":55000,\"end\":200000,\"step\":0.1}}, 1000000]"
        )

    if not items:
        raise ValueError(f"Line {line_no}: cycle list is empty")
    return items


def _parse_json_options(raw: str, *, line_no: int) -> Dict[str, Any]:
    """Parse options.

    Supports:
      - JSON object: {"channel":"1+2","waveform":"sine"}
      - json: prefix: json:{...}
      - py: prefix: py:{...} (python dict syntax) (best-effort)

    Also does a few "user-friendly" fixes:
      - removes trailing commas
      - auto-quotes keys and simple string values (best-effort)
    """
    raw = (raw or "").strip()
    if not raw:
        return {}

    if raw.lower().startswith("json:"):
        raw = raw[5:].strip()
    if raw.lower().startswith("py:"):
        raw = raw[3:].strip()

    def try_json(s: str) -> Dict[str, Any]:
        obj = json.loads(s)
        if not isinstance(obj, dict):
            raise ValueError("options must be a JSON object")
        return obj

    # 1) strict JSON
    try:
        return try_json(raw)
    except Exception:
        pass

    # 2) tolerate trailing commas
    cur = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        return try_json(cur)
    except Exception:
        pass

    # 3) quote bare keys: {waveform:"sine"} -> {"waveform":"sine"}
    cur2 = re.sub(r"([,{]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', cur)

    # 4) quote simple bareword values: {"waveform":sine} -> {"waveform":"sine"} (best-effort)
    def _quote_val(m: re.Match) -> str:
        val = m.group(1)
        tail = m.group(2)
        if val in {"true", "false", "null"}:
            return f": {val}{tail}"
        try:
            float(val)
            return f": {val}{tail}"
        except Exception:
            return f': "{val}"{tail}'

    cur3 = re.sub(r":\s*([A-Za-z_][A-Za-z0-9_+-]*)(\s*[,}])", _quote_val, cur2)

    try:
        return try_json(cur3)
    except Exception as e:
        raise ValueError(
            f"Line {line_no}: invalid JSON options: {e}. "
            f"Hint: JSON requires double quotes and no trailing comma. Options seen: '{raw}'"
        )



def _expand_steps(raw_steps: Sequence[RawStep]) -> List[Step]:
    """Expand _FreqListRaw and _CycleRaw into a flat list of Steps."""
    out: List[Step] = []
    i = 0
    n = len(raw_steps)

    while i < n:
        s = raw_steps[i]

        # Legacy syntax: freq,[...]; optional wait; optional freq,0; optional wait
        if isinstance(s, _FreqListRaw):
            freqs = list(s.freqs_hz)

            j = i + 1
            on_wait: Optional[WaitStep] = None
            if j < n and isinstance(raw_steps[j], WaitStep):
                on_wait = raw_steps[j]  # type: ignore[assignment]
                j += 1

            pause_freq: Optional[FreqStep] = None
            off_wait: Optional[WaitStep] = None
            if j < n and isinstance(raw_steps[j], FreqStep) and float(raw_steps[j].hz) == 0.0:
                pause_freq = raw_steps[j]  # type: ignore[assignment]
                j += 1
                if j < n and isinstance(raw_steps[j], WaitStep):
                    off_wait = raw_steps[j]  # type: ignore[assignment]
                    j += 1

            for f in freqs:
                out.append(FreqStep(hz=float(f), options=s.options, source_line=s.source_line))
                if on_wait is not None:
                    out.append(WaitStep(seconds=float(on_wait.seconds), source_line=on_wait.source_line))
                if pause_freq is not None:
                    out.append(FreqStep(hz=float(pause_freq.hz), options=pause_freq.options, source_line=pause_freq.source_line))
                    if off_wait is not None:
                        out.append(WaitStep(seconds=float(off_wait.seconds), source_line=off_wait.source_line))

            i = j
            continue

        # Flat step
        out.append(s)  # type: ignore[arg-type]
        i += 1

    return out


def parse_csv_commands(path: str | Path) -> List[Step]:
    """Parse command CSV file.

    Supported commands (case-insensitive):
      - freq,<hz_or_[list]>,<optional JSON options>
      - wait,<seconds>
      - stop
      - cycle,<[list]>,<on_seconds>,<off_seconds>,<optional JSON options>
      - mod,<params...>,<optional JSON options>
        - positional: cycle,[1000,2000,3000],5,10
        - key/value:  cycle,[1000,2000,3000],on=5,off=10,pause_hz=0

    Legacy loop behavior:
      If you use freq,[list] and it is followed by optional wait, and optional
      freq,0 + optional wait, then the whole group is applied per element.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return []

    sample = "\n".join(text.splitlines()[:25])
    try:
        dialect0 = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
        # NOTE: We intentionally disable CSV quoting rules so that JSON fragments like:
        #   {"waveform":"sine","amplitude":1.0}
        # can live unquoted inside the CSV. The standard csv module would otherwise
        # treat parts starting with a quote as "quoted fields" and strip quotes.
        class _LooseDialect(csv.Dialect):
            delimiter = dialect0.delimiter
            quotechar = '"'
            escapechar = '\\'
            doublequote = True
            skipinitialspace = True
            lineterminator = '\n'
            quoting = csv.QUOTE_NONE
        dialect = _LooseDialect
    except Exception:
        dialect = csv.get_dialect("excel")

    reader = csv.reader(text.splitlines(), dialect)
    raw_steps: List[RawStep] = []

    for idx, row in enumerate(reader, start=1):
        if not row:
            continue
        row = [c.strip() for c in row]
        if not row[0] or row[0].lstrip().startswith("#"):
            continue

        cmd = (row[0] or "").strip().lower()

        if cmd in _WAIT_ALIASES:
            if len(row) < 2 or not _is_number(row[1]):
                raise ValueError(f"Line {idx}: wait expects seconds as number")
            raw_steps.append(WaitStep(seconds=float(row[1]), source_line=idx))
            continue

        if cmd in _STOP_ALIASES:
            raw_steps.append(StopStep(source_line=idx))
            continue

        if cmd in _FREQ_ALIASES:
            if len(row) < 2:
                raise ValueError(f"Line {idx}: freq expects <Hz> or <[list]> as second column")
            token, next_i = _consume_bracketed_token(row, 1, dialect.delimiter)
            token = token.strip()

            opts_raw = (dialect.delimiter.join(row[next_i:]) if next_i < len(row) else "")
            opts = _parse_json_options(opts_raw, line_no=idx) if opts_raw.strip() else {}

            if _looks_like_list(token):
                freqs = _parse_number_list(token, line_no=idx)
                raw_steps.append(_FreqListRaw(freqs_hz=freqs, options=opts, source_line=idx))
            else:
                if not _is_number(token):
                    raise ValueError(f"Line {idx}: freq expects a number (Hz) or list like [1000,2000]")
                raw_steps.append(FreqStep(hz=float(token), options=opts, source_line=idx))
            continue

        if cmd in _CYCLE_ALIASES:
            if len(row) < 2:
                raise ValueError(
                    f"Line {idx}: cycle expects a list, e.g. cycle,[1000,2000,3000],on=5,off=10"
                )
            token, next_i = _consume_bracketed_token(row, 1, dialect.delimiter)
            token = token.strip()
            if not _looks_like_list(token):
                raise ValueError(f"Line {idx}: cycle expects a frequency list like [1000,2000,3000]")
            items = _parse_cycle_items(token, line_no=idx)

            on_wait: Optional[float] = None
            off_wait: Optional[float] = None
            pause_hz: float = 0.0
            adaptive_voltage: bool = False

            j = next_i
            while j < len(row):
                cell = (row[j] or "").strip()
                if not cell:
                    j += 1
                    continue
                # options start
                if cell.lstrip().startswith("{") or cell.lower().startswith("json:") or cell.lower().startswith("py:"):
                    break

                if "=" in cell:
                    k, v = cell.split("=", 1)
                    k = k.strip().lower().replace("_", "-")
                    v = v.strip()

                    if k in {"adaptive-voltage", "adaptive", "adaptivevoltage"}:
                        adaptive_voltage = _parse_bool(v)
                        j += 1
                        continue

                    if not _is_number(v):
                        raise ValueError(f"Line {idx}: cycle parameter '{k}' must be a number")
                    fv = float(v)
                    if k in {"on", "wait", "hold", "on-wait", "onwait"}:
                        on_wait = fv
                    elif k in {"off", "pause", "off-wait", "offwait", "pause-wait", "pausewait"}:
                        off_wait = fv
                    elif k in {"pause-hz", "pause-freq", "off-hz", "off-freq"}:
                        pause_hz = fv
                    else:
                        raise ValueError(
                            f"Line {idx}: unknown cycle parameter '{k}'. Use on=, off=, pause_hz=, adaptive-voltage=true"
                        )
                    j += 1
                    continue

                # positional number
                if _is_number(cell):
                    fv = float(cell)
                    if on_wait is None:
                        on_wait = fv
                    elif off_wait is None:
                        off_wait = fv
                    else:
                        raise ValueError(
                            f"Line {idx}: too many numeric args for cycle. Use cycle,[...],on,off"
                        )
                    j += 1
                    continue

                # unknown token -> treat as start of options; allow users to have a single tail "{...}" without prefix
                break

            opts_raw = (dialect.delimiter.join(row[j:]) if j < len(row) else "")
            opts = _parse_json_options(opts_raw, line_no=idx) if opts_raw.strip() else {}

            raw_steps.append(
                CycleStep(
                    items=items,
                    on_wait=float(on_wait or 0.0),
                    off_wait=off_wait,
                    pause_hz=float(pause_hz),
                    adaptive_voltage=bool(adaptive_voltage),
                    options=opts,
                    source_line=idx,
                )
            )
            continue

        if cmd in _MOD_ALIASES:
            # mod (frequency modulation / sweep)
            # Default params:
            #   start=1, end=1000000, time=1 (seconds per sweep leg), update=50 (ms), direction=rise-and-fall,
            #   adaptive-voltage=false, repeat=true
            start_hz: Optional[float] = None
            end_hz: Optional[float] = None
            time_s: Optional[float] = None
            update_ms: Optional[float] = None

            direction: Optional[str] = None
            adaptive_voltage: Optional[bool] = None
            repeat: Optional[bool] = None

            j = 1
            positional: List[str] = []
            while j < len(row):
                cell = (row[j] or "").strip()
                if not cell:
                    j += 1
                    continue
                # options start
                if cell.lstrip().startswith("{") or cell.lower().startswith("json:") or cell.lower().startswith("py:"):
                    break
                if "=" in cell:
                    k, v = cell.split("=", 1)
                    k = k.strip().lower().replace("_", "-")
                    v = v.strip()
                    if k in {"start", "from", "start-hz", "f-start"}:
                        if not _is_number(v):
                            raise ValueError(f"Line {idx}: mod parameter '{k}' must be a number")
                        start_hz = float(v)
                    elif k in {"end", "to", "end-hz", "f-end"}:
                        if not _is_number(v):
                            raise ValueError(f"Line {idx}: mod parameter '{k}' must be a number")
                        end_hz = float(v)
                    elif k in {"time", "time-s", "s", "sec", "secs", "second", "seconds", "cycle", "cycle-s", "duration", "duration-s"}:
                        if not _is_number(v):
                            raise ValueError(f"Line {idx}: mod parameter '{k}' must be a number (seconds)")
                        time_s = float(v)
                    elif k in {"time-ms", "ms", "cycle-ms", "duration-ms"}:
                        if not _is_number(v):
                            raise ValueError(f"Line {idx}: mod parameter '{k}' must be a number (milliseconds)")
                        time_s = float(v) / 1000.0
                    elif k in {"update", "update-ms", "interval", "interval-ms", "tick", "tick-ms", "step", "step-ms"}:
                        if not _is_number(v):
                            raise ValueError(f"Line {idx}: mod parameter '{k}' must be a number (milliseconds)")
                        update_ms = float(v)
                    elif k in {"direction", "dir"}:
                        direction = _normalize_direction(v)
                    elif k in {"adaptive-voltage", "adaptive", "adaptivevoltage", "adaptive-voltage?", "adaptive_voltage"}:
                        adaptive_voltage = _parse_bool(v)
                    elif k in {"repeat", "loop"}:
                        repeat = _parse_bool(v)
                    else:
                        raise ValueError(
                            f"Line {idx}: unknown mod parameter '{k}'. Use start=, end=, time= (seconds), update= (ms), direction=, adaptive-voltage=, repeat="
                        )
                    j += 1
                    continue

                positional.append(cell)
                j += 1

            # positional: start,end,time(seconds),direction,adaptive_voltage,repeat,update-ms
            if positional:
                if len(positional) > 7:
                    raise ValueError(
                        f"Line {idx}: too many positional args for mod. Use mod,start,end,time_seconds,direction,adaptive-voltage,repeat,update_ms"
                    )
                if len(positional) >= 1:
                    if not _is_number(positional[0]):
                        raise ValueError(f"Line {idx}: mod start must be a number")
                    start_hz = float(positional[0])
                if len(positional) >= 2:
                    if not _is_number(positional[1]):
                        raise ValueError(f"Line {idx}: mod end must be a number")
                    end_hz = float(positional[1])
                if len(positional) >= 3:
                    if not _is_number(positional[2]):
                        raise ValueError(f"Line {idx}: mod time must be a number (seconds)")
                    time_s = float(positional[2])
                if len(positional) >= 4:
                    direction = _normalize_direction(positional[3])
                if len(positional) >= 5:
                    adaptive_voltage = _parse_bool(positional[4])
                if len(positional) >= 6:
                    repeat = _parse_bool(positional[5])
                if len(positional) >= 7:
                    if not _is_number(positional[6]):
                        raise ValueError(f"Line {idx}: mod update interval must be a number (milliseconds)")
                    update_ms = float(positional[6])

            start_hz = float(start_hz if start_hz is not None else 1.0)
            end_hz = float(end_hz if end_hz is not None else 1_000_000.0)
            time_s = float(time_s if time_s is not None else 1.0)
            update_ms = float(update_ms if update_ms is not None else 50.0)
            direction = direction if direction is not None else "rise-and-fall"
            adaptive_voltage = bool(adaptive_voltage) if adaptive_voltage is not None else False
            repeat = bool(repeat) if repeat is not None else True

            if start_hz < 0 or end_hz < 0:
                raise ValueError(f"Line {idx}: mod start/end must be >= 0")
            if time_s <= 0:
                raise ValueError(f"Line {idx}: mod time must be > 0 (seconds)")
            if update_ms <= 0:
                raise ValueError(f"Line {idx}: mod update interval must be > 0 (milliseconds)")

            opts_raw = (dialect.delimiter.join(row[j:]) if j < len(row) else "")
            opts = _parse_json_options(opts_raw, line_no=idx) if opts_raw.strip() else {}

            raw_steps.append(
                ModStep(
                    start_hz=float(start_hz),
                    end_hz=float(end_hz),
                    time_s=float(time_s),
                    update_ms=float(update_ms),
                    direction=str(direction),
                    adaptive_voltage=bool(adaptive_voltage),
                    repeat=bool(repeat),
                    options=opts,
                    source_line=idx,
                )
            )
            continue

        raise ValueError(
            f"Line {idx}: unknown command '{row[0]}'. Use 'freq', 'wait', 'stop', 'cycle' or 'mod'."
        )

    return _expand_steps(raw_steps)


def estimate_remaining_wait_time(steps: Sequence[Step], start_index: int) -> float:
    total = 0.0
    for s in steps[start_index:]:
        if isinstance(s, WaitStep):
            total += float(s.seconds)
    return total


def _cycle_range_count(spec: CycleRangeSpec) -> int:
    """Count inclusive points in the range without materializing it."""
    import math as _math
    try:
        start = float(spec.start_hz)
        end = float(spec.end_hz)
        step = float(spec.step_hz)
    except Exception:
        return 0
    if step == 0:
        return 0
    # ensure step direction matches span
    span = end - start
    if span == 0:
        return 1
    if span > 0 and step < 0:
        step = -step
    if span < 0 and step > 0:
        step = -step
    # number of steps (inclusive)
    try:
        n = int(_math.floor((span / step) + 1e-12)) + 1
    except Exception:
        return 0
    if n < 1:
        return 0
    return n


def _cycle_items_count(items: Sequence[CycleItem]) -> int:
    total = 0
    for it in items:
        if isinstance(it, CycleRangeSpec):
            total += _cycle_range_count(it)
        else:
            total += 1
    return total


def cycle_range_count(spec: CycleRangeSpec) -> int:
    """Count points in a CycleRangeSpec without materializing the range.

    This is the inclusive count of generated frequencies.
    """
    return _cycle_range_count(spec)


def cycle_items_count(items: Sequence[CycleItem]) -> int:
    """Count points in cycle items (floats + ranges) without materializing ranges."""
    return _cycle_items_count(items)


def estimate_step_duration(s: Step, *, fixed_wait: Optional[float] = None) -> float:
    """Estimate duration in seconds for a single step.

    - freq/stop: 0
    - wait: wait seconds (or fixed_wait if provided)
    - cycle: count(items) * (on_wait + off_wait when enabled), with fixed_wait applied per wait
    - mod: finite duration if repeat=false; infinite if repeat=true
    """
    import math as _math

    if isinstance(s, WaitStep):
        if fixed_wait is not None:
            try:
                return max(0.0, float(fixed_wait))
            except Exception:
                return max(0.0, float(s.seconds))
        return max(0.0, float(s.seconds))

    if isinstance(s, CycleStep):
        count = _cycle_items_count(s.items)
        if count <= 0:
            return 0.0

        def _eff_wait(w: Optional[float]) -> float:
            if w is None:
                return 0.0
            try:
                wv = float(w)
            except Exception:
                return 0.0
            if wv <= 0:
                return 0.0
            if fixed_wait is not None:
                try:
                    return max(0.0, float(fixed_wait))
                except Exception:
                    return max(0.0, wv)
            return max(0.0, wv)

        on = _eff_wait(s.on_wait)
        off = _eff_wait(s.off_wait) if s.off_wait is not None else 0.0
        return float(count) * float(on + off)

    if isinstance(s, ModStep):
        if bool(s.repeat):
            return _math.inf
        legs = 2 if str(s.direction) == "rise-and-fall" else 1
        try:
            return max(0.0, float(s.time_s) * float(legs))
        except Exception:
            return 0.0

    return 0.0


def estimate_remaining_run_time(
    steps: Sequence[Step],
    start_index: int,
    *,
    fixed_wait: Optional[float] = None,
) -> float:
    """Estimate remaining run time in seconds from start_index (inclusive).

    Returns math.inf if any remaining step is unbounded (e.g. mod with repeat=true).
    """
    import math as _math
    total = 0.0
    for s in steps[start_index:]:
        d = estimate_step_duration(s, fixed_wait=fixed_wait)
        if _math.isinf(d):
            return _math.inf
        total += float(d)
    return total
