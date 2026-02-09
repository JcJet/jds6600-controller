#!/usr/bin/env python3
"""
JDS6600 Controller (GUI)
- Cross-platform (Windows/Linux)
- Uses jds6600 Python library + pyserial
- Command file format: CSV (see commands.example.csv)

Ubuntu GUI dependency:
  sudo apt update && sudo apt install -y python3-tk
"""

from __future__ import annotations

import sys
import json
import re
import ast
import time
import queue
import tempfile
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Tkinter is a system package on many Linux distros (python3-tk)
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:
    print("Tkinter is not available. On Ubuntu/Debian run:", file=sys.stderr)
    print("  sudo apt update && sudo apt install -y python3-tk", file=sys.stderr)
    raise

from jds_controller.commands import (
    parse_csv_commands,
    WaitStep,
    ModStep,
    CycleStep,
    CycleRangeSpec,
    estimate_remaining_run_time,
    estimate_step_duration,
    cycle_items_count,
    cycle_range_count,
)
from jds_controller.ports import (
    find_first_jds6600,
    list_serial_ports,
    list_linux_by_id_ports,
    PortInfo,
)
from jds_controller.runner import RunnerState, run_sequence
from jds_controller.device_state import read_device_state, format_device_state
from jds_controller.util import fmt_seconds


def fmt_hhmmss(sec: float) -> str:
    """Fixed-width remaining-time formatter for UI labels.

    Returns "∞" for an unbounded/unknown duration.
    """
    import math
    try:
        v = float(sec)
        if not math.isfinite(v):
            return "∞"
        total = int(round(v))
    except Exception:
        return "--:--:--"
    if total < 0:
        total = 0
    h = total // 3600
    if h > 99:
        return "99:59:59"
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

from .settings_store import load_settings, save_settings
from .resume_store import ResumeStore
from .messages import GuiMsg, MsgKind, ProgressPayload, DonePayload
from . import ui


PROJECT_GITHUB_URL = "https://github.com/JcJet/jds6600-controller"
PROJECT_TELEGRAM_URL = "https://t.me/JcJet"


@dataclass(frozen=True)
class UiPortItem:
    label: str
    port: str


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("JDS6600 Controller")
        self.geometry("1280x760")
        self.minsize(1120, 680)

        # Worker / state
        self.msgq: "queue.Queue[GuiMsg]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.state = RunnerState()
        self._running = False

        # file state
        self.current_file: Optional[Path] = None
        self._temp_run_file: Optional[Path] = None
        self._dirty = False
        self._suppress_modified = False

        # resume store (persisted execution position for a saved file)
        self.resume_store = ResumeStore()
        self._run_file_sha256: Optional[str] = None
        # If a valid persisted resume point exists for the startup file,
        # we auto-enter the same UI state as after "Start" + immediate "Pause".
        self._resume_autostart_done: bool = False

        # variables
        self.port_var = tk.StringVar(value="")
        # Default channel on first run (when no settings yet): CH1.
        self.channel_var = tk.StringVar(value="1")
        self.wait_override_enabled = tk.BooleanVar(value=False)
        self.wait_override_seconds = tk.StringVar(value="0")
        self.repeat_file_enabled = tk.BooleanVar(value=False)

        # Top-right label next to the progress bar (fixed width).
        # Shows only the estimated remaining time of the current run.
        self.remaining_time_var = tk.StringVar(value="--:--:--")
        # Keep the old status variable for internal / log-friendly messages.
        self.status_var = tk.StringVar(value="Не подключено")
        self.device_var = tk.StringVar(value="не проверено")
        self.progress_var = tk.DoubleVar(value=0.0)

        self.device_state_var = tk.StringVar(value="Нет подключения")

        # --- remaining-time countdown model (smooth UI updates) ---
        # We receive coarse estimates on step boundaries and richer checkpoints
        # during long-running steps (wait/mod). We keep a "base" remaining time
        # for the current step and a constant "tail" for later steps, then
        # count down smoothly on the UI thread.
        self._run_steps = None  # type: ignore[assignment]
        self._run_fixed_wait: Optional[float] = None
        self._rt_tail_s: float = 0.0
        self._rt_base_rem_s: float = 0.0
        self._rt_base_ts: float = 0.0
        self._rt_infinite: bool = False

        # --- device connection & status polling state ---
        # These attributes MUST exist before any auto-connect logic runs.
        self._fg = None
        self._fg_lock = threading.Lock()
        # I/O lock for operations on the active GUI connection.
        # Polling uses try-lock to avoid interfering with other GUI-side operations.
        self._io_lock = threading.Lock()
        self._connected = False
        self._connected_port = None
        self._reconnect_after_run = False
        self._reconnect_after_run_port = None
        self._poll_stop = threading.Event()
        self._poll_enabled = True
        self._poll_thread = None

        # Polling bookkeeping
        self._poll_force = threading.Event()   # wake up polling loop ASAP
        self._poll_interval = 1.0
        self._poll_last_text: Optional[str] = None
        self._poll_last_good_text: Optional[str] = None
        self._poll_last_error_ts = 0.0
        self._poll_error_throttle_sec = 5.0

        self._build_ui()
        self._load_settings_and_init()

        self.after(100, self._drain_queue)
        self.after(200, self._tick_remaining_time)
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------

    def _build_ui(self):
        # UI construction extracted into jds_controller.gui.ui
        ui.build_ui(self, github_url=PROJECT_GITHUB_URL, telegram_url=PROJECT_TELEGRAM_URL)

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        title = "JDS6600 Controller"
        if self.current_file:
            title += f" — {self.current_file.name}"
        if self._dirty:
            title += " *"
        self.title(title)

        # Resume is only valid for a saved, clean file.
        # The Start button label stays constant ("СТАРТ"); resume is handled by auto-entering
        # paused execution state on startup when applicable.
        if self._dirty:
            self.resume_store.invalidate()
        else:
            self.resume_store.load_for_file(self.current_file, dirty=self._dirty)

    def _on_modified(self, event=None):
        if self._suppress_modified:
            self.editor.edit_modified(False)
            return
        if self.editor.edit_modified():
            self._set_dirty(True)
            self.editor.edit_modified(False)

    def _browse_open(self):
        if not self._confirm_discard_if_dirty():
            return
        path = filedialog.askopenfilename(
            title="Открыть файл команд",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        self._open_file(Path(path))

    def _open_file(self, path: Path):
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть файл:\n{e}")
            return

        self._suppress_modified = True
        try:
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", txt)
            self.editor.edit_modified(False)
        finally:
            self._suppress_modified = False

        self.current_file = path
        self._set_dirty(False)
        self._clear_highlight()
        self._log(f"Открыт файл: {path}")

    def _save(self):
        if not self.current_file:
            return self._save_as()
        try:
            self.current_file.write_text(self.editor.get("1.0", "end-1c"), encoding="utf-8")
            self._set_dirty(False)
            self._log(f"Сохранено: {self.current_file}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}")

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title="Сохранить как",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        self.current_file = Path(path)
        self._save()

    def _new_template(self):
        if not self._confirm_discard_if_dirty():
            return
        template = (
            "# JDS6600 command file (CSV)\n"
            "# Format:\n"
            "#   freq,<Hz>,<optional JSON options>\n"
            "#   freq,[Hz1,Hz2,...],<optional JSON options>\n"
            "#   cycle,[Hz1,Hz2,...],on=<sec>,off=<sec>,<optional JSON options>\n"
            "#   wait,<seconds>\n"
            "#\n"
            "# Examples:\n"
            "#   freq,1000,{\"channel\":\"1+2\",\"waveform\":\"sine\",\"amplitude\":1.0}\n"
            "#   wait,2\n"
            "#   freq,2000,{\"channel\":1,\"waveform\":\"square\",\"dutycycle\":30,\"amplitude\":2.0}\n"
            "#   wait,1.5\n"
            "#\n"
            "#   # Clean cycle syntax:\n"
            "#   cycle,[1000,2000,3000],on=5,off=10,{\"channel\":\"1+2\"}\n"
            "\n"
            "freq,1000,{\"channel\":\"1+2\",\"waveform\":\"sine\",\"amplitude\":1.0}\n"
            "wait,2\n"
            "freq,2000,{\"channel\":1,\"waveform\":\"square\",\"dutycycle\":30,\"amplitude\":2.0}\n"
            "wait,1.5\n"
        )
        self._suppress_modified = True
        try:
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", template)
            self.editor.edit_modified(False)
        finally:
            self._suppress_modified = False
        self.current_file = None
        self._set_dirty(True)
        self._clear_highlight()

    def _confirm_discard_if_dirty(self) -> bool:
        if not self._dirty:
            return True
        res = messagebox.askyesnocancel("Несохранённые изменения", "Файл изменён. Сохранить изменения?")
        if res is None:
            return False
        if res is True:
            self._save()
            return not self._dirty
        # no
        return True

    # ---------------- Ports ----------------

    def _format_port_item(self, p: PortInfo) -> UiPortItem:
        extra = p.hwid or "n/a"
        if p.vid is not None and p.pid is not None:
            extra = f"VID:PID={p.vid:04x}:{p.pid:04x}"
        label = f"{p.device} ({p.description or 'n/a'}) | {extra}"
        return UiPortItem(label=label, port=p.device)

    def _refresh_ports(self):
        by_id = list_linux_by_id_ports()
        ports = list_serial_ports()

        values = []
        items = []
        if by_id:
            for p in by_id:
                values.append(p)
                items.append(UiPortItem(label=f"{p} (by-id)", port=p))
        for p in ports:
            items.append(self._format_port_item(p))
            values.append(items[-1].label)

        self._port_items = items
        self.port_combo["values"] = values

        # keep current if still present
        cur = self.port_var.get().strip()
        if cur and cur in values:
            pass
        else:
            # auto select best if empty
            if by_id:
                self.port_var.set(by_id[0])
            elif values:
                # keep combobox value consistent with its values list (prevents UI glitches)
                self.port_var.set(values[0])

        self._probe_selected_port_async()

    def _extract_port_value(self, raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        if raw.startswith("/dev/serial/by-id/"):
            return raw
        # if it's one of our labels, map to device
        for it in getattr(self, "_port_items", []):
            if it.label == raw:
                return it.port
        # else assume it's already a port string
        return raw

    def _set_led(self, state: str):
        colors = {"unknown": "#999999", "ok": "#2ecc71", "bad": "#e74c3c"}
        self.device_led.itemconfig(self._led_item, fill=colors.get(state, "#999999"))

    def _probe_selected_port_async(self):
        port = self._extract_port_value(self.port_var.get())
        if not port:
            self.device_var.set("не выбран")
            self._set_led("unknown")
            return
        self.device_var.set("проверка…")
        self._set_led("unknown")

        def worker():
            ok = False
            try:
                import jds6600
                fg = jds6600.JDS6600(port=port)
                fg.connect()
                try:
                    fg.get_channels()
                finally:
                    fg.close()
                ok = True
            except Exception:
                ok = False
            self.msgq.put(GuiMsg(MsgKind.PROBE, bool(ok)))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_detect(self):
        self.status_var.set("Поиск устройства…")
        self._log("Авто-поиск устройства…")

        def worker():
            import jds6600
            try:
                port = find_first_jds6600()
                self.msgq.put(GuiMsg(MsgKind.AUTODETECT, port or ""))
            except Exception as e:
                self.msgq.put(GuiMsg(MsgKind.ERROR, f"Авто-поиск: {e}"))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Run logic ----------------

    def _get_wait_override_seconds(self) -> float:
        try:
            v = float((self.wait_override_seconds.get() or "").strip())
            if v < 0:
                raise ValueError()
            return v
        except Exception:
            raise ValueError("Неверное значение фиксированного wait (сек). Введите число >= 0.")

    def _get_effective_commands_path_for_run(self) -> Path:
        """
        If editor has unsaved changes or file is not chosen: write temp file and run it.
        """
        text = self.editor.get("1.0", "end-1c")
        # if no current file, or dirty -> temp
        if self.current_file is None or self._dirty:
            tmp = Path(tempfile.mkstemp(prefix="jds6600_", suffix=".csv")[1])
            tmp.write_text(text, encoding="utf-8")
            self._temp_run_file = tmp
            return tmp
        return self.current_file

    def _validate(self):
        text = self.editor.get("1.0", "end-1c") if hasattr(self, "editor") else ""
        try:
            p = self._get_effective_commands_path_for_run()
            parse_csv_commands(p)
            if self.wait_override_enabled.get():
                _ = self._get_wait_override_seconds()
            messagebox.showinfo("Проверка", "Файл команд корректен.")
        except Exception as e:
            messagebox.showerror("Ошибка CSV", self._format_csv_error_for_ui(e, text))

    def _format_csv_error_for_ui(self, e: Exception, source_text: str) -> str:
        """Format parser errors with helpful context (line snippet, etc.).

        Keeps the parser as the source of truth, but adds:
          - the raw command line (from the editor)
          - for cycle parsing errors: the offending list element (best-effort)
        """
        msg = str(e).strip()
        if not source_text:
            return msg

        m = re.search(r"\bLine\s+(\d+)\b", msg)
        if not m:
            return msg

        try:
            line_no = int(m.group(1))
        except Exception:
            return msg

        lines = source_text.splitlines()
        line_text = lines[line_no - 1] if 1 <= line_no <= len(lines) else ""

        out = [msg]
        if line_text:
            out.extend(["", f"Строка {line_no}:", line_text])

        # Best-effort extraction of a failing cycle element.
        m2 = re.search(r"cycle element #(?P<pos>\d+)", msg, flags=re.IGNORECASE)
        if line_text and m2:
            try:
                pos = int(m2.group("pos"))
            except Exception:
                pos = 0
            if pos > 0:
                elem = self._try_extract_cycle_element(line_text, pos)
                if elem:
                    out.extend(["", f"Проблемный элемент #{pos}: {elem}"])

        return "\n".join(out)

    @staticmethod
    def _try_extract_cycle_element(line_text: str, pos: int) -> Optional[str]:
        """Try to extract a specific element from a cycle list in the given line.

        This is a UX helper for error dialogs only. It MUST be best-effort and never throw.
        """
        try:
            s = str(line_text)
            # Locate the first [...] token on the line.
            start = s.find("[")
            if start < 0:
                return None
            bal = 0
            end = None
            for i in range(start, len(s)):
                if s[i] == "[":
                    bal += 1
                elif s[i] == "]":
                    bal -= 1
                    if bal == 0:
                        end = i + 1
                        break
            if end is None:
                return None
            raw = s[start:end].strip()

            # Try JSON first (tolerant), then Python literal.
            cur = re.sub(r",\s*([}\]])", r"\1", raw)  # trailing commas
            cur = re.sub(r"([,{]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', cur)  # quote keys

            obj = None
            try:
                obj = json.loads(cur)
            except Exception:
                obj = ast.literal_eval(raw)

            if not isinstance(obj, (list, tuple)):
                return None
            idx = pos - 1
            if idx < 0 or idx >= len(obj):
                return None
            elem = obj[idx]

            try:
                return json.dumps(elem, ensure_ascii=False)
            except Exception:
                return repr(elem)
        except Exception:
            return None

    def _set_running_ui(self, running: bool):
        self._running = running
        self.btn_start.config(state="disabled" if running else "normal")
        self.btn_pause.config(state="normal" if running else "disabled")
        self.btn_next.config(state="normal" if running else "disabled")
        self.btn_stop.config(state="normal" if running else "disabled")
        if not running:
            self.btn_pause.config(text="Пауза")

    # ---------------- Run / resume helpers ----------------

    def _start_worker(
        self,
        *,
        port: str,
        steps,
        cmd_path: Path,
        resume_ck: Optional[dict],
        start_paused: bool,
    ) -> None:
        """Common worker start routine.

        This is used by the normal Start button and by startup auto-resume.
        """
        # Remember hash of the executed file to validate resume saving on close.
        try:
            self._run_file_sha256 = ResumeStore.file_sha256(Path(cmd_path))
        except Exception:
            self._run_file_sha256 = None

        # Clear previous checkpoint (runner will update it during execution).
        try:
            self.state.resume_checkpoint = None  # type: ignore[attr-defined]
        except Exception:
            pass

        fixed_wait = None
        if self.wait_override_enabled.get():
            try:
                fixed_wait = self._get_wait_override_seconds()
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))
                return

        self.state.paused = bool(start_paused)
        self.state.stopped = False
        self.state.skip_wait = False
        self.progress_var.set(0.0)
        self.remaining_time_var.set("--:--:--")

        # Store steps & fixed wait for the smooth remaining-time estimator.
        self._run_steps = steps
        self._run_fixed_wait = fixed_wait
        self._rt_tail_s = 0.0
        self._rt_base_rem_s = 0.0
        self._rt_base_ts = 0.0
        self._rt_infinite = False

        self._clear_highlight()
        self._set_running_ui(True)
        # Reflect paused state immediately in the UI.
        try:
            self.btn_pause.config(text="Продолжить" if self.state.paused else "Пауза")
        except Exception:
            pass

        self.status_var.set("Запуск…" if not start_paused else "Восстановлено (пауза)…")

        def on_status(msg: str):
            self.msgq.put(GuiMsg(MsgKind.STATUS, msg))

        def on_progress(i: int, total: int, est_remaining_wait: float, step) -> None:
            # runner.py calls on_progress(i, total, est_remaining, step)
            done = int(i) + 1  # i is 0-based
            line_no = getattr(step, 'source_line', None)
            try:
                line_no_int = int(line_no) if line_no is not None else None
            except Exception:
                line_no_int = None
            self.msgq.put(GuiMsg(MsgKind.PROGRESS, ProgressPayload(
                done=done,
                total=int(total),
                line=line_no_int,
                est_seconds=float(est_remaining_wait),
            )))

        def worker():
            try:
                # Ensure the GUI connection does not block the script runner.
                was_connected = self._connected
                if was_connected:
                    self._disconnect_sync()
                    self.msgq.put(GuiMsg(MsgKind.DISCONNECTED, None))
                self._reconnect_after_run = True
                self._reconnect_after_run_port = port
                rc = run_sequence(
                    steps,
                    port=port,
                    default_channel=("1+2" if self.channel_var.get()=="1+2" else self.channel_var.get()),
                    state=self.state,
                    on_status=on_status,
                    on_progress=on_progress,
                    on_device_state=lambda txt: self.msgq.put(GuiMsg(MsgKind.DEVICE_STATE, txt)),
                    state_poll_interval=1.0,
                    tick_wait_updates=False,
                    fixed_wait_seconds=fixed_wait,
                    resume=resume_ck,
                    on_checkpoint=lambda ck: self.msgq.put(GuiMsg(MsgKind.CHECKPOINT, ck)),
                )
                self.msgq.put(GuiMsg(MsgKind.DONE, DonePayload(rc=int(rc))))
            except Exception as e:
                self.msgq.put(GuiMsg(MsgKind.ERROR, str(e)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()


    def _start(self):
        if self.worker and self.worker.is_alive():
            return

        port = self._extract_port_value(self.port_var.get())
        if not port:
            messagebox.showerror("Ошибка", "Выберите порт (или нажмите Авто-поиск).")
            return

        src_text = self.editor.get("1.0", "end-1c") if hasattr(self, "editor") else ""
        try:
            cmd_path = self._get_effective_commands_path_for_run()
            steps = parse_csv_commands(cmd_path)
        except Exception as e:
            messagebox.showerror("Ошибка CSV", self._format_csv_error_for_ui(e, src_text))
            return

        self._start_worker(port=port, steps=steps, cmd_path=Path(cmd_path), resume_ck=None, start_paused=False)


    def _maybe_autostart_resume_paused(self) -> None:
        """If a valid persisted resume point exists for the current file, enter paused run state.

        This makes the UI look exactly like after pressing "СТАРТ" and then immediately "Пауза":
        - "Продолжить" (pause button) is active
        - "Следующая команда" and "Стоп" are active
        - current line is highlighted
        - "СТАРТ" stays disabled (no relabeling)
        """
        if self._resume_autostart_done:
            return
        if self._running:
            return
        if self._dirty:
            return
        if not self.resume_store.available:
            return
        if self.current_file is None or (not self.current_file.exists()):
            return

        port = self._extract_port_value(self.port_var.get())
        if not port:
            # No configured port yet; keep resume available but do not start a paused worker.
            return

        resume_ck = self.resume_store.checkpoint if isinstance(self.resume_store.checkpoint, dict) else None
        if not resume_ck:
            return

        src_text = self.editor.get("1.0", "end-1c") if hasattr(self, "editor") else ""
        try:
            steps = parse_csv_commands(self.current_file)
        except Exception as e:
            # If the file no longer parses, do not attempt to auto-run.
            self._log("Auto-resume disabled: CSV parse error")
            try:
                messagebox.showerror("Ошибка CSV", self._format_csv_error_for_ui(e, src_text))
            except Exception:
                pass
            return

        self._resume_autostart_done = True
        self._log("== AUTO-RESUME (paused) ==")
        self._start_worker(port=port, steps=steps, cmd_path=self.current_file, resume_ck=resume_ck, start_paused=True)

    def _toggle_pause(self):
        if not self._running:
            return
        was_paused = bool(self.state.paused)

        # Freeze the smooth countdown while paused (avoid time "jump" on resume).
        try:
            if (not self._rt_infinite) and self._rt_base_ts:
                now = time.monotonic()
                if not was_paused:
                    # going to pause: consume elapsed time into the base
                    delta = max(0.0, float(now - self._rt_base_ts))
                    self._rt_base_rem_s = max(0.0, float(self._rt_base_rem_s) - delta)
                    self._rt_base_ts = now
                else:
                    # going to resume: reset the base timestamp
                    self._rt_base_ts = now
        except Exception:
            pass

        self.state.paused = not was_paused
        self.btn_pause.config(text="Продолжить" if self.state.paused else "Пауза")
        self._log("== PAUSE ==" if self.state.paused else "== RESUME ==")

    def _next_command(self):
        if not self._running:
            return
        self.state.skip_wait = True
        self._log("== NEXT COMMAND (skip wait) ==")

    def _stop(self):
        if not self._running:
            return
        self.state.stopped = True
        self.state.paused = False
        self.state.skip_wait = True
        self._log("== STOP requested ==")

    # ---------------- Help / About ----------------

    def _open_url(self, url: str):
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Не удалось открыть браузер", str(e))

    def _show_help(self):
        # Extracted into jds_controller.gui.ui
        ui.show_help(self)

    def _about(self):
        messagebox.showinfo(
            "О программе",
            "JDS6600 Controller\n\nGUI/CLI утилита для управления генератором JDS6600.\n"
            f"GitHub: {PROJECT_GITHUB_URL}\nTelegram: {PROJECT_TELEGRAM_URL} (@JcJet)"
        )

    # ---------------- Editor context menu & shortcuts ----------------

    def _build_editor_context_menu(self):
        # Extracted into jds_controller.gui.ui
        ui.build_editor_context_menu(self)

    def _show_editor_context_menu(self, event):
        """Show context menu for the command editor."""
        menu = getattr(self, "_editor_menu", None)
        if menu is None:
            return None
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return "break"

    def _editor_select_all(self):
        self.editor.tag_add("sel", "1.0", "end-1c")
        self.editor.mark_set("insert", "1.0")
        self.editor.see("insert")

    def _editor_undo(self):
        try:
            self.editor.edit_undo()
        except Exception:
            pass

    def _editor_redo(self):
        try:
            self.editor.edit_redo()
        except Exception:
            pass

    def _on_editor_ctrl_shortcut(self, event):
        # Extracted into jds_controller.gui.ui
        return ui.on_editor_ctrl_shortcut(self, event)

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _highlight_source_line(self, source_line: int):
        # source_line is from CSV file; our Text widget line numbers start at 1
        self._clear_highlight()
        if source_line <= 0:
            return
        start = f"{source_line}.0"
        end = f"{source_line}.0 lineend"
        self.editor.tag_add("current_line", start, end)
        self.editor.see(start)

    def _clear_highlight(self):
        self.editor.tag_remove("current_line", "1.0", "end")

    # ---------------- Settings / close ----------------

    def _load_settings_and_init(self):
        s = load_settings()
        # init ports
        self._refresh_ports()

        # apply saved values
        if isinstance(s.get("channel"), str) and s["channel"] in {"1+2","1","2"}:
            ch = s["channel"]
            self.channel_var.set("1+2" if ch in {"both", "1+2"} else ch)
        if isinstance(s.get("wait_override_enabled"), bool):
            self.wait_override_enabled.set(s["wait_override_enabled"])
        if "wait_override_seconds" in s:
            self.wait_override_seconds.set(str(s.get("wait_override_seconds") or "0"))
        if isinstance(s.get("repeat_file_enabled"), bool):
            self.repeat_file_enabled.set(s["repeat_file_enabled"])

        fp = s.get("file_path")
        if isinstance(fp, str) and fp and Path(fp).exists():
            self._open_file(Path(fp))
        else:
            # load default sample if exists
            default = Path(__file__).with_name("commands.csv")
            if default.exists():
                self._open_file(default)

        port = s.get("port")
        if isinstance(port, str) and port:
            # Set as-is; if it's a label, extract will map
            self.port_var.set(port)
            self._probe_selected_port_async()
            self._connect_selected_port_async()

        # If a resume point exists for the startup file, auto-enter the paused execution state.
        # Run this after initial UI+settings setup.
        try:
            self.after(50, self._maybe_autostart_resume_paused)
        except Exception:
            pass

    def _persist_settings(self):
        s = load_settings()
        if not isinstance(s, dict):
            s = {}
        s.update({
            "file_path": str(self.current_file) if self.current_file else "",
            "port": self.port_var.get(),
            "channel": self.channel_var.get(),
            "wait_override_enabled": bool(self.wait_override_enabled.get()),
            "wait_override_seconds": self.wait_override_seconds.get(),
            "repeat_file_enabled": bool(self.repeat_file_enabled.get()),
        })
        save_settings(s)

    def _on_close(self):
        # Close flow:
        # 1) Handle unsaved edits (Save / Don't Save / Cancel)
        # 2) If a script is running/paused, persist the current execution checkpoint for a saved file
        # 3) Persist general settings
        allow_resume_save = True

        if self._dirty:
            res = messagebox.askyesnocancel("Несохранённые изменения", "Файл изменён. Сохранить изменения?")
            if res is None:
                return
            if res is True:
                self._save()
                # if save failed/cancelled, keep the app open
                if self._dirty:
                    return
            else:
                # User explicitly chose not to save changes -> do not persist resume point (avoid stale resume)
                allow_resume_save = False
                try:
                    self.resume_store.clear()
                except Exception:
                    pass

        # Persist resume checkpoint (auto-resume) only for a saved, clean file.
        if allow_resume_save:
            try:
                if self._running and (self.current_file is not None) and (not self._dirty) and self.current_file.exists():
                    ck = getattr(self.state, "resume_checkpoint", None)
                    if isinstance(ck, dict):
                        # Validate that the on-disk file matches the file content that was actually executed.
                        file_sha = ResumeStore.file_sha256(self.current_file)
                        if file_sha and (self._run_file_sha256 is None or file_sha == self._run_file_sha256):
                            self.resume_store.persist(self.current_file, dirty=self._dirty, checkpoint=ck, executed_sha256=self._run_file_sha256)
            except Exception:
                pass

        # Persist basic settings (file path, port, etc.). This must NOT wipe the resume field.
        try:
            self._persist_settings()
        except Exception:
            pass

        # Cleanup temp run file if any
        if self._temp_run_file and self._temp_run_file.exists():
            try:
                self._temp_run_file.unlink()
            except Exception:
                pass

        try:
            self._poll_stop.set()
        except Exception:
            pass
        try:
            self._disconnect_sync()
        except Exception:
            pass
        self.destroy()


    # ---------------- Queue processing ----------------

    def _drain_queue(self):
        """Process queued messages from worker threads.

        The GUI uses a queue to communicate from non-UI threads back to Tk.
        We accept both the new typed GuiMsg protocol and legacy (kind, payload)
        tuples for backward compatibility.
        """
        try:
            while True:
                item = self.msgq.get_nowait()

                # Backward compatibility: allow (kind, payload) tuples
                if isinstance(item, tuple) and len(item) == 2:
                    kind_raw, payload = item
                    try:
                        kind = MsgKind(str(kind_raw))
                    except Exception:
                        continue
                    msg = GuiMsg(kind=kind, payload=payload)
                else:
                    msg = item

                if not isinstance(msg, GuiMsg):
                    continue

                kind = msg.kind
                payload = msg.payload

                if kind == MsgKind.STATUS:
                    self.status_var.set(str(payload))
                    self._log(str(payload))

                elif kind == MsgKind.PROBE:
                    ok = bool(payload)
                    if ok:
                        self.device_var.set("устройство найдено")
                        self._set_led("ok")
                    else:
                        self.device_var.set("не найдено")
                        self._set_led("bad")

                elif kind == MsgKind.AUTODETECT:
                    port = str(payload or "")
                    if port:
                        self.port_var.set(port)
                        self._probe_selected_port_async()
                        # Auto-connect after successful auto-detect
                        self._connect_selected_port_async()
                        self.status_var.set(f"Найдено устройство: {port}")
                        self._log(f"Авто-поиск: найдено на {port}")
                    else:
                        self.status_var.set("Устройство не найдено")
                        self._log("Авто-поиск: устройство не найдено")

                elif kind == MsgKind.CONNECTED:
                    port = str(payload or "")
                    self._set_connected_ui(True, port)
                    # Wake the polling loop so the status bar shows real device state ASAP.
                    try:
                        self._poll_force.set()
                    except Exception:
                        pass
                    # Keep the top status label in sync (avoid being stuck on 'Не подключено' at startup).
                    try:
                        if (not self._running) and (self.status_var.get() in ("Не подключено", "")):
                            self.status_var.set("Подключено")
                    except Exception:
                        pass

                elif kind == MsgKind.DISCONNECTED:
                    self._set_connected_ui(False)
                    try:
                        if not self._running:
                            self.status_var.set("Не подключено")
                    except Exception:
                        pass

                elif kind == MsgKind.CONNECT_ERROR:
                    self._set_connected_ui(False)
                    self._log(f"Ошибка подключения: {payload}")
                    messagebox.showerror("Ошибка подключения", str(payload))

                elif kind == MsgKind.DEVICE_STATE:
                    txt = str(payload)
                    self.device_state_var.set(txt)
                    # Keep polling bookkeeping in sync with UI updates
                    self._poll_last_text = txt
                    if txt and txt not in {"Нет подключения", "Подключено (нет данных)"}:
                        self._poll_last_good_text = txt

                elif kind == MsgKind.CHECKPOINT:
                    # Update smooth remaining-time model during long steps.
                    if isinstance(payload, dict):
                        self._remaining_apply_checkpoint(payload)

                elif kind == MsgKind.LOG:
                    self._log(str(payload))

                elif kind == MsgKind.PROGRESS:
                    done = total = 0
                    line = None
                    est = 0.0
                    try:
                        if isinstance(payload, ProgressPayload):
                            done = int(payload.done)
                            total = int(payload.total)
                            line = payload.line
                            est = float(payload.est_seconds)
                        elif isinstance(payload, dict):
                            done = int(payload.get('done', 0))
                            total = int(payload.get('total', 0))
                            line = payload.get('line')
                            est = float(payload.get('est', 0.0))
                        elif isinstance(payload, str) and payload.strip().startswith('{'):
                            data = json.loads(payload)
                            done = int(data.get('done', 0))
                            total = int(data.get('total', 0))
                            line = data.get('line')
                            est = float(data.get('est', 0.0) or 0.0)
                    except Exception:
                        pass
                    if total > 0:
                        pct = min(100.0, (done / total) * 100.0)
                        self.progress_var.set(pct)
                    if isinstance(line, int):
                        self._highlight_source_line(line)
                    if total > 0:
                        # Set countdown base estimate (the timer will update smoothly).
                        self._remaining_set_from_estimate(est)

                elif kind == MsgKind.DONE:
                    rc = 0
                    try:
                        if isinstance(payload, DonePayload):
                            rc = int(payload.rc)
                        elif isinstance(payload, dict):
                            rc = int(payload.get('rc', 0))
                        elif isinstance(payload, str) and payload.strip().startswith('{'):
                            obj = json.loads(payload)
                            if isinstance(obj, dict):
                                rc = int(obj.get('rc', 0))
                    except Exception:
                        rc = 0

                    self.progress_var.set(100.0)
                    self.remaining_time_var.set("00:00:00")
                    self.status_var.set("Остановлено" if rc == 4 else "Готово")
                    self._set_running_ui(False)
                    self._clear_highlight()

                    # Completed or stopped: clear persisted resume point (no longer relevant).
                    try:
                        self.resume_store.clear()
                    except Exception:
                        pass

                    # cleanup temp run file
                    if self._temp_run_file and self._temp_run_file.exists():
                        try:
                            self._temp_run_file.unlink()
                        except Exception:
                            pass
                        self._temp_run_file = None

                    # Auto-repeat file (start again from the beginning) if enabled.
                    should_repeat = (rc == 0 and bool(self.repeat_file_enabled.get()))
                    if should_repeat:
                        try:
                            self.status_var.set("Повтор файла: перезапуск")
                            self._log("Повтор файла: запуск заново")
                        except Exception:
                            pass
                        # Do not reconnect in-between repeats to avoid port contention.
                        self._reconnect_after_run = False
                        try:
                            self.resume_store.clear()
                        except Exception:
                            pass
                        self.after(200, self._start)
                        continue

                    # Reconnect after run so that status polling continues in idle.
                    if self._reconnect_after_run:
                        self._reconnect_after_run = False
                        if not self._connected:
                            self._connect_selected_port_async()

                elif kind == MsgKind.ERROR:
                    self.status_var.set("Ошибка")
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Ошибка", str(payload))
                    self._set_running_ui(False)
                    self._clear_highlight()
                    # Restore connection after an error as well.
                    if self._reconnect_after_run:
                        self._reconnect_after_run = False
                        if not self._connected:
                            self._connect_selected_port_async()

        except queue.Empty:
            pass

        self.after(100, self._drain_queue)

    # ---------------- Remaining time (smooth countdown) ----------------

    def _remaining_set_from_estimate(self, est_total_seconds: float) -> None:
        """Set the countdown model from a coarse total estimate (seconds)."""
        now = time.monotonic()
        try:
            import math
            if not math.isfinite(float(est_total_seconds)):
                self._rt_infinite = True
                self._rt_tail_s = 0.0
                self._rt_base_rem_s = 0.0
                self._rt_base_ts = now
                self.remaining_time_var.set("∞")
                return
        except Exception:
            pass

        self._rt_infinite = False
        self._rt_tail_s = 0.0
        try:
            self._rt_base_rem_s = max(0.0, float(est_total_seconds))
        except Exception:
            self._rt_base_rem_s = 0.0
        self._rt_base_ts = now
        self.remaining_time_var.set(fmt_hhmmss(self._rt_base_rem_s))

    def _remaining_apply_checkpoint(self, ck: dict) -> None:
        """Update countdown model from a runner checkpoint (step boundary / wait / mod)."""
        if not self._running:
            return
        if not isinstance(ck, dict):
            return
        if self._run_steps is None:
            return

        now = time.monotonic()
        try:
            step_index = int(ck.get("step_index", 0))
        except Exception:
            step_index = 0
        if step_index < 0:
            step_index = 0

        steps = self._run_steps
        if step_index >= len(steps):
            return

        within = ck.get("within") if isinstance(ck.get("within"), dict) else None

        # Tail: remaining time AFTER the current step.
        tail = estimate_remaining_run_time(steps, step_index + 1, fixed_wait=self._run_fixed_wait)

        # Current step remaining (best effort)
        step = steps[step_index]
        cur = 0.0
        try:
            import math
            # Wait step: use precise remaining if provided
            if isinstance(step, WaitStep):
                if within and within.get("kind") == "wait" and "remaining" in within:
                    cur = max(0.0, float(within.get("remaining", 0.0)))
                else:
                    cur = float(estimate_step_duration(step, fixed_wait=self._run_fixed_wait))

            # Mod step: derive remaining from k/updates when possible
            elif isinstance(step, ModStep):
                if bool(step.repeat):
                    self._rt_infinite = True
                    self._rt_tail_s = 0.0
                    self._rt_base_rem_s = 0.0
                    self._rt_base_ts = now
                    self.remaining_time_var.set("∞")
                    return

                leg_s = max(0.0, float(step.time_s))
                legs_total = 2 if str(step.direction) == "rise-and-fall" else 1
                total_s = leg_s * float(legs_total)

                if within and within.get("kind") == "mod":
                    leg = str(within.get("leg", "")).strip().lower()
                    try:
                        k = int(within.get("k", 0))
                    except Exception:
                        k = 0
                    try:
                        updates = int(within.get("updates", 1))
                    except Exception:
                        updates = 1
                    if updates <= 0:
                        updates = 1
                    frac = k / float(updates)
                    if frac < 0.0:
                        frac = 0.0
                    if frac > 1.0:
                        frac = 1.0
                    rem_leg = leg_s * (1.0 - frac)

                    if str(step.direction) == "rise-and-fall":
                        if leg == "rise":
                            cur = rem_leg + leg_s  # full fall leg still ahead
                        else:
                            cur = rem_leg
                    else:
                        cur = rem_leg
                else:
                    cur = total_s

            # Cycle step: best-effort remaining based on (item_i, sub_k/sub_n) checkpoints
            elif isinstance(step, CycleStep):
                # Effective waits (fixed-wait override applies when wait > 0)
                def _eff_wait(w: float | None) -> float:
                    if w is None:
                        return 0.0
                    try:
                        wv = float(w)
                    except Exception:
                        return 0.0
                    if wv <= 0:
                        return 0.0
                    if self._run_fixed_wait is not None:
                        try:
                            return max(0.0, float(self._run_fixed_wait))
                        except Exception:
                            return max(0.0, wv)
                    return max(0.0, wv)

                on_s = _eff_wait(getattr(step, "on_wait", 0.0))
                off_s = _eff_wait(getattr(step, "off_wait", None)) if getattr(step, "off_wait", None) is not None else 0.0
                per_point = float(on_s + off_s)

                # Fallback: full step duration
                cur = float(estimate_step_duration(step, fixed_wait=self._run_fixed_wait))

                if within and within.get("kind") in {"cycle", "cycle_wait"} and per_point >= 0.0:
                    try:
                        item_i = int(within.get("item_i", 0))
                    except Exception:
                        item_i = 0
                    try:
                        sub_k = int(within.get("sub_k", 0))
                    except Exception:
                        sub_k = 0
                    try:
                        sub_n = int(within.get("sub_n", 0))
                    except Exception:
                        sub_n = 0

                    if item_i < 0:
                        item_i = 0
                    if sub_k < 0:
                        sub_k = 0

                    # Count remaining points from current position (inclusive), without materializing ranges.
                    rem_points_incl = 0
                    items = getattr(step, "items", []) or []
                    if item_i < len(items):
                        # current item
                        it0 = items[item_i]
                        if isinstance(it0, CycleRangeSpec):
                            total_n = sub_n if sub_n > 0 else cycle_range_count(it0)
                            if total_n < 0:
                                total_n = 0
                            if sub_k >= total_n:
                                rem_points_incl = 0
                            else:
                                rem_points_incl = int(total_n - sub_k)
                        else:
                            rem_points_incl = 1

                        # following items
                        for it in items[item_i + 1 :]:
                            if isinstance(it, CycleRangeSpec):
                                rem_points_incl += int(cycle_range_count(it))
                            else:
                                rem_points_incl += 1

                    # Current point remaining time (best effort)
                    cur_point_rem = per_point
                    if within.get("kind") == "cycle_wait":
                        phase = str(within.get("phase", "")).strip().lower()
                        try:
                            rem_phase = float(within.get("remaining", 0.0))
                        except Exception:
                            rem_phase = 0.0
                        rem_phase = max(0.0, rem_phase)
                        if phase == "on":
                            cur_point_rem = rem_phase + float(off_s)
                        elif phase == "off":
                            cur_point_rem = rem_phase
                        else:
                            cur_point_rem = rem_phase
                    else:
                        # kind == cycle: right after setting the frequency, before on-wait
                        cur_point_rem = per_point

                    rem_after = max(0, int(rem_points_incl) - 1)
                    cur = float(cur_point_rem) + float(rem_after) * float(per_point)

            else:
                cur = 0.0

            # If any part is infinite -> infinite
            if (not math.isfinite(float(tail))) or (not math.isfinite(float(cur))):
                self._rt_infinite = True
                self._rt_tail_s = 0.0
                self._rt_base_rem_s = 0.0
                self._rt_base_ts = now
                self.remaining_time_var.set("∞")
                return
        except Exception:
            # If estimation fails, don't break the UI.
            cur = 0.0

        self._rt_infinite = False
        try:
            self._rt_tail_s = max(0.0, float(tail))
        except Exception:
            self._rt_tail_s = 0.0
        self._rt_base_rem_s = max(0.0, float(cur))
        self._rt_base_ts = now

    def _tick_remaining_time(self) -> None:
        """Update the remaining time label smoothly (counts down between checkpoints)."""
        try:
            if self._running:
                if self._rt_infinite:
                    self.remaining_time_var.set("∞")
                else:
                    if self._rt_base_ts:
                        now = time.monotonic()
                        # When paused, freeze countdown (base is updated in _toggle_pause).
                        delta = 0.0 if bool(getattr(self.state, "paused", False)) else (now - self._rt_base_ts)
                        cur = max(0.0, float(self._rt_base_rem_s) - float(delta))
                        total = cur + max(0.0, float(self._rt_tail_s))
                        self.remaining_time_var.set(fmt_hhmmss(total))
        finally:
            self.after(200, self._tick_remaining_time)



    # --- Connection & status polling -------------------------------------------------

    def _set_connected_ui(self, connected: bool, port: str = "") -> None:
        """Update UI when connection state changes (runs in UI thread)."""
        self._connected = connected
        self._connected_port = (port or self._connected_port) if connected else None
        if hasattr(self, "btn_connect"):
            self.btn_connect.configure(text="Отключиться" if connected else "Подключиться")
        if connected:
            # Force next poll to update the status bar (prevents it from being stuck on "Подключено...").
            self._poll_last_text = None
            try:
                self._poll_force.set()
            except Exception:
                pass
        else:
            self.device_state_var.set("Нет подключения")
            self._poll_last_text = "Нет подключения"
            self._poll_last_good_text = None
            try:
                self._poll_force.set()
            except Exception:
                pass

    def _toggle_connection(self) -> None:
        if self._running:
            # during execution we keep the script priority; manual connect/disconnect is disabled
            self._log("Во время выполнения сценария подключение управляется автоматически.")
            return
        if self._connected:
            self._disconnect_async()
        else:
            self._connect_selected_port_async()

    def _connect_selected_port_async(self) -> None:
        port = self._extract_port_value(self.port_var.get())
        if not port:
            self._log("Не выбран порт.")
            return
        # Already connected to this port
        if self._connected and self._connected_port == port:
            return

        def worker():
            import jds6600
            try:
                fg = jds6600.JDS6600(port=port)
                fg.connect()
                with self._fg_lock:
                    # close previous connection if any
                    try:
                        if self._fg is not None:
                            self._fg.close()
                    except Exception:
                        pass
                    self._fg = fg
                    self._connected = True
                    self._connected_port = port
                self.msgq.put(GuiMsg(MsgKind.CONNECTED, port))
            except Exception as e:
                self.msgq.put(GuiMsg(MsgKind.CONNECT_ERROR, str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _disconnect_sync(self) -> None:
        """Close current connection. Can be called from any thread."""
        with self._fg_lock:
            fg = self._fg
            self._fg = None
            self._connected = False
            self._connected_port = None
        try:
            if fg is not None:
                fg.close()
        except Exception:
            pass

    def _disconnect_async(self) -> None:
        def worker():
            self._disconnect_sync()
            self.msgq.put(GuiMsg(MsgKind.DISCONNECTED, None))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_loop(self) -> None:
        """Poll generator state (~1 Hz) when the GUI is idle.

        - While a script is running, polling is suspended (runner updates device state during wait).
        - If there's no connection, we show "Нет подключения".
        - Errors are logged to the UI (throttled) and do not erase the last known good state.
        """
        while not self._poll_stop.is_set():
            if self._running:
                # Do not overwrite runner updates.
                self._poll_force.wait(timeout=0.2)
                self._poll_force.clear()
                continue

            # Wait for the next tick or a forced wake (e.g. after connect).
            self._poll_force.wait(timeout=float(getattr(self, "_poll_interval", 1.0)))
            self._poll_force.clear()

            if self._poll_stop.is_set():
                break

            # Snapshot connection and fg reference quickly.
            if not self._connected:
                text = "Нет подключения"
                if text != self._poll_last_text:
                    self.msgq.put(GuiMsg(MsgKind.DEVICE_STATE, text))
                    self._poll_last_text = text
                continue

            with self._fg_lock:
                fg = self._fg

            if fg is None:
                text = "Нет подключения"
                if text != self._poll_last_text:
                    self.msgq.put(GuiMsg(MsgKind.DEVICE_STATE, text))
                    self._poll_last_text = text
                continue

            # Non-blocking try-lock: if something else is using the GUI connection, skip this tick.
            if not self._io_lock.acquire(blocking=False):
                continue

            try:
                try:
                    text = format_device_state(read_device_state(fg))
                    self._poll_last_good_text = text
                except Exception as e:
                    # Keep last known good state. Log error (throttled).
                    now = time.monotonic()
                    if now - float(self._poll_last_error_ts) >= float(self._poll_error_throttle_sec):
                        self._poll_last_error_ts = now
                        self.msgq.put(GuiMsg(MsgKind.LOG, f"Status poll error: {e}"))
                    text = self._poll_last_good_text or "Подключено (нет данных)"
            finally:
                try:
                    self._io_lock.release()
                except Exception:
                    pass

            if text != self._poll_last_text:
                self.msgq.put(GuiMsg(MsgKind.DEVICE_STATE, text))
                self._poll_last_text = text


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
