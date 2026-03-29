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
import subprocess
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
from .i18n import detect_language, tr as i18n_tr, translate_runtime_text
from . import ui
from .lcd import LcdPanel
from .audio import TonePlayer


PROJECT_GITHUB_URL = "https://github.com/JcJet/jds6600-controller"
PROJECT_TELEGRAM_URL = "https://t.me/JcJet"


@dataclass(frozen=True)
class UiPortItem:
    label: str
    port: str


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self._initial_settings = load_settings()
        self.lang = detect_language((self._initial_settings or {}).get("language"))
        self.title(i18n_tr(self.lang, "app_title"))
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
        self.lang_var = tk.StringVar(value=self.lang)
        # Default channel on first run (when no settings yet): CH1.
        self.channel_var = tk.StringVar(value="1")
        self.wait_override_enabled = tk.BooleanVar(value=False)
        self.wait_override_seconds = tk.StringVar(value="0")
        self.repeat_file_enabled = tk.BooleanVar(value=False)
        self.enable_outputs_on_start = tk.BooleanVar(value=True)
        self.disable_outputs_on_finish = tk.BooleanVar(value=False)
        self.shutdown_pc_on_finish = tk.BooleanVar(value=False)
        self.sound_on_finish = tk.BooleanVar(value=False)
        self.dark_theme = tk.BooleanVar(value=bool((self._initial_settings or {}).get("dark_theme", False)))
        self.heavy_lcd_rendering = tk.BooleanVar(value=bool((self._initial_settings or {}).get("heavy_lcd_rendering", False)))
        self._tone_player = TonePlayer(self)
        self._last_progress_done = 0
        self._last_freq_cue = None

        # Top-right label next to the progress bar (fixed width).
        # Shows only the estimated remaining time of the current run.
        self.remaining_time_var = tk.StringVar(value="--:--:--")
        # Keep the old status variable for internal / log-friendly messages.
        self.status_var = tk.StringVar(value=self.tr("status_not_connected"))
        self.device_var = tk.StringVar(value=self.tr("device_unchecked"))
        self.progress_var = tk.DoubleVar(value=0.0)

        self.device_state_var = tk.StringVar(value=self.tr("device_state_none"))

        # Friendly “now playing” / current-frequency panel.
        self.current_primary_var = tk.StringVar(value="NO ACTIVE COMMAND")
        self.current_secondary_var = tk.StringVar(value="PRESS START TO BEGIN")
        self.current_time_var = tk.StringVar(value="--:--:--")
        self.current_step_var = tk.StringVar(value="")
        self.current_progress_var = tk.DoubleVar(value=0.0)
        self._cp_kind: str = "idle"
        self._cp_freq_hz: Optional[float] = None
        self._cp_phase: str = ""
        self._cp_total_s: float = 0.0
        self._cp_base_rem_s: float = 0.0
        self._cp_base_ts: float = 0.0
        self._cp_infinite: bool = False

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
        self._progress_total_s: float = 0.0

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
        self._apply_theme()
        self.bind_all("<KeyPress-space>", self._on_space_pause_resume, add=True)
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

    def tr(self, key: str, **kwargs) -> str:
        return i18n_tr(self.lang, key, **kwargs)

    def _theme_palette(self) -> dict:
        if bool(self.dark_theme.get()):
            return {
                "mode": "dark",
                "bg": "#232629",
                "panel": "#2b3035",
                "panel_alt": "#31363b",
                "fg": "#e6e6e6",
                "muted_fg": "#c8c8c8",
                "entry_bg": "#1b1e21",
                "entry_fg": "#f3f3f3",
                "text_bg": "#17191c",
                "text_fg": "#f3f3f3",
                "select_bg": "#40617f",
                "select_fg": "#ffffff",
                "border": "#4b4f55",
                "accent": "#4d6f91",
                "highlight": "#574617",
                "led_bg": "#2b3035",
            }
        return {
            "mode": "light",
            "bg": "#f0f0f0",
            "panel": "#f0f0f0",
            "panel_alt": "#f7f7f7",
            "fg": "#000000",
            "muted_fg": "#333333",
            "entry_bg": "#ffffff",
            "entry_fg": "#000000",
            "text_bg": "#ffffff",
            "text_fg": "#000000",
            "select_bg": "#cfe8ff",
            "select_fg": "#000000",
            "border": "#b7b7b7",
            "accent": "#4f81bd",
            "highlight": "#fff3c4",
            "led_bg": "#f0f0f0",
        }

    def _apply_theme(self) -> None:
        palette = self._theme_palette()
        try:
            self.configure(bg=palette["bg"])
        except Exception:
            pass
        try:
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            style.configure(".", background=palette["bg"], foreground=palette["fg"], fieldbackground=palette["entry_bg"])
            style.configure("TFrame", background=palette["bg"])
            style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
            style.configure("TButton", background=palette["panel_alt"], foreground=palette["fg"], bordercolor=palette["border"])
            style.map("TButton", background=[("active", palette["accent"]), ("disabled", palette["panel_alt"])], foreground=[("disabled", palette["muted_fg"])])
            style.configure("Big.TButton", background=palette["panel_alt"], foreground=palette["fg"], bordercolor=palette["border"])
            style.map("Big.TButton", background=[("active", palette["accent"]), ("disabled", palette["panel_alt"])], foreground=[("disabled", palette["muted_fg"])])
            style.configure("TCheckbutton", background=palette["bg"], foreground=palette["fg"])
            style.map("TCheckbutton", background=[("active", palette["bg"])], foreground=[("disabled", palette["muted_fg"])])
            style.configure("TRadiobutton", background=palette["bg"], foreground=palette["fg"])
            style.configure("TLabelframe", background=palette["bg"], foreground=palette["fg"], bordercolor=palette["border"])
            style.configure("TLabelframe.Label", background=palette["bg"], foreground=palette["fg"])
            style.configure("TEntry", fieldbackground=palette["entry_bg"], foreground=palette["entry_fg"], bordercolor=palette["border"])
            style.configure("TCombobox", fieldbackground=palette["entry_bg"], foreground=palette["entry_fg"], background=palette["panel_alt"], arrowcolor=palette["fg"], bordercolor=palette["border"])
            style.map("TCombobox", fieldbackground=[("readonly", palette["entry_bg"])], foreground=[("readonly", palette["entry_fg"])], selectbackground=[("readonly", palette["select_bg"])], selectforeground=[("readonly", palette["select_fg"])])
            style.configure("Horizontal.TProgressbar", troughcolor=palette["panel_alt"], background=palette["accent"], bordercolor=palette["border"], lightcolor=palette["accent"], darkcolor=palette["accent"])
            style.configure("TScrollbar", background=palette["panel_alt"], troughcolor=palette["bg"], bordercolor=palette["border"], arrowcolor=palette["fg"])
            style.configure("TPanedwindow", background=palette["bg"])
            style.configure("TSeparator", background=palette["border"])
        except Exception:
            pass

        def walk(widget):
            try:
                if isinstance(widget, tk.Text):
                    widget.configure(background=palette["text_bg"], foreground=palette["text_fg"], insertbackground=palette["text_fg"], selectbackground=palette["select_bg"], selectforeground=palette["select_fg"])
                elif isinstance(widget, tk.Canvas):
                    widget.configure(background=palette["led_bg"], highlightbackground=palette["border"])
                elif isinstance(widget, tk.Menu):
                    widget.configure(background=palette["panel_alt"], foreground=palette["fg"], activebackground=palette["accent"], activeforeground=palette["select_fg"], tearoff=False)
                elif not isinstance(widget, ttk.Widget):
                    widget.configure(background=palette["bg"], foreground=palette["fg"])
            except Exception:
                pass
            for child in widget.winfo_children():
                walk(child)

        walk(self)
        try:
            self.editor.tag_configure("current_line", background=palette["highlight"])
        except Exception:
            pass
        for menu in getattr(self, "_menus", []):
            try:
                menu.configure(background=palette["panel_alt"], foreground=palette["fg"], activebackground=palette["accent"], activeforeground=palette["select_fg"])
            except Exception:
                pass
        self._persist_settings()

    def _toggle_dark_theme(self) -> None:
        self._apply_theme()

    def _toggle_heavy_lcd_rendering(self) -> None:
        try:
            if hasattr(self, "lcd_panel") and self.lcd_panel is not None:
                self.lcd_panel.set_render_mode(bool(self.heavy_lcd_rendering.get()))
        except Exception:
            pass
        self._persist_settings()

    def _change_language(self, lang: str) -> None:
        lang = detect_language(lang)
        if lang == self.lang:
            return
        editor_text = self.editor.get("1.0", "end-1c") if hasattr(self, "editor") else ""
        log_text = self.log.get("1.0", "end-1c") if hasattr(self, "log") else ""
        current_port = self.port_var.get()
        highlight_line = None
        try:
            ranges = self.editor.tag_ranges("current_line")
            if ranges:
                highlight_line = int(str(ranges[0]).split(".")[0])
        except Exception:
            pass
        prev_status = self.status_var.get()
        prev_device = self.device_var.get()
        prev_device_state = self.device_state_var.get()
        self.lang = lang
        self.lang_var.set(lang)
        try:
            self.config(menu="")
        except Exception:
            pass
        for child in list(self.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._build_ui()
        self._apply_theme()
        self._refresh_ports(do_probe=False)
        if current_port:
            self.port_var.set(current_port)
        self._suppress_modified = True
        try:
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", editor_text)
            self.editor.edit_modified(False)
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            if log_text:
                self.log.insert("1.0", log_text)
            self.log.configure(state="disabled")
        finally:
            self._suppress_modified = False
        if highlight_line:
            self._highlight_source_line(highlight_line)
        self.status_var.set(translate_runtime_text(prev_status, self.lang))
        self.device_var.set(translate_runtime_text(prev_device, self.lang))
        self.device_state_var.set(translate_runtime_text(prev_device_state, self.lang))
        self._refresh_current_panel()
        self._refresh_lcd_panel()
        if (not self._running) and (not self._connected):
            self.status_var.set(self.tr("status_not_connected"))
            self.device_state_var.set(self.tr("device_state_none"))
        self._set_connected_ui(self._connected, self._connected_port or "")
        self._set_led("ok" if self._connected else "unknown")
        self._set_running_ui(self._running)
        if self._running:
            self.btn_pause.config(text=self.tr("btn_resume") if self.state.paused else self.tr("btn_pause"))
        self._set_dirty(self._dirty)
        self._persist_settings()

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        title = self.tr("app_title")
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

    def _programs_dir(self) -> Path:
        """Return the preferred directory for command files.

        When bundled, prefer a sibling ``programs`` folder next to the executable.
        During development, prefer the repository-level ``programs`` folder.
        """
        candidates = []
        try:
            if getattr(sys, "frozen", False):
                candidates.append(Path(sys.executable).resolve().parent / "programs")
        except Exception:
            pass
        try:
            candidates.append(Path(__file__).resolve().parents[2] / "programs")
        except Exception:
            pass
        for c in candidates:
            try:
                if c.exists() and c.is_dir():
                    return c
            except Exception:
                pass
        return candidates[0] if candidates else Path.home()

    def _default_file_dialog_dir(self) -> str:
        try:
            if self.current_file and self.current_file.parent.exists():
                return str(self.current_file.parent)
        except Exception:
            pass
        try:
            return str(self._programs_dir())
        except Exception:
            return str(Path.home())

    def _default_startup_commands_file(self) -> Optional[Path]:
        candidates = []
        try:
            programs = self._programs_dir()
            candidates.extend([
                programs / "commands.csv",
                programs / "commands.example.csv",
            ])
        except Exception:
            pass
        try:
            root = Path(__file__).resolve().parents[2]
            candidates.extend([
                root / "commands.csv",
                root / "commands.example.csv",
            ])
        except Exception:
            pass
        for c in candidates:
            try:
                if c.exists():
                    return c
            except Exception:
                pass
        return None

    def _browse_open(self):
        if not self._confirm_discard_if_dirty():
            return
        path = filedialog.askopenfilename(
            title=self.tr("title_open"),
            initialdir=self._default_file_dialog_dir(),
            filetypes=[(self.tr("filetypes_csv"), "*.csv"), (self.tr("filetypes_all"), "*.*")]
        )
        if not path:
            return
        self._open_file(Path(path))

    def _open_file(self, path: Path):
        try:
            txt = path.read_text(encoding="utf-8")
        except Exception as e:
            messagebox.showerror(self.tr("title_error"), self.tr("msg_open_failed", error=e))
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
        self._log(self.tr("log_file_opened", path=path))

    def _save(self):
        if not self.current_file:
            return self._save_as()
        try:
            self.current_file.write_text(self.editor.get("1.0", "end-1c"), encoding="utf-8")
            self._set_dirty(False)
            self._log(self.tr("log_file_saved", path=self.current_file))
        except Exception as e:
            messagebox.showerror(self.tr("title_error"), self.tr("msg_save_failed", error=e))

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title=self.tr("title_save_as"),
            initialdir=self._default_file_dialog_dir(),
            initialfile=(self.current_file.name if self.current_file else "commands.csv"),
            defaultextension=".csv",
            filetypes=[(self.tr("filetypes_csv"), "*.csv"), (self.tr("filetypes_all"), "*.*")]
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
        res = messagebox.askyesnocancel(self.tr("title_unsaved"), self.tr("msg_unsaved"))
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

    def _refresh_ports(self, do_probe: bool = True):
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

        if do_probe:
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
            self.device_var.set(self.tr("device_not_selected"))
            self._set_led("unknown")
            return
        self.device_var.set(self.tr("device_checking"))
        self._set_led("unknown")

        def worker():
            ok = False
            try:
                if self._connected and self._connected_port == port:
                    ok = True
                else:
                    with self._io_lock:
                        if self._connected and self._connected_port == port:
                            ok = True
                        else:
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
        self.status_var.set(self.tr("status_searching_device"))
        self._log(self.tr("log_autodetect_start"))

        def worker():
            import jds6600
            try:
                port = find_first_jds6600()
                self.msgq.put(GuiMsg(MsgKind.AUTODETECT, port or ""))
            except Exception as e:
                self.msgq.put(GuiMsg(MsgKind.ERROR, self.tr("log_autodetect_error", error=e)))

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- Run logic ----------------

    def _get_wait_override_seconds(self) -> float:
        try:
            v = float((self.wait_override_seconds.get() or "").strip())
            if v < 0:
                raise ValueError()
            return v
        except Exception:
            raise ValueError(self.tr("msg_invalid_fixed_wait"))

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
            messagebox.showinfo(self.tr("title_check"), self.tr("msg_commands_ok"))
        except Exception as e:
            messagebox.showerror(self.tr("title_csv_error"), self._format_csv_error_for_ui(e, text))

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
            out.extend(["", self.tr("line_context", line=line_no), line_text])

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
                    out.extend(["", self.tr("problematic_element", pos=pos, elem=elem)])

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
            self.btn_pause.config(text=self.tr("btn_pause"))
            self._clear_current_panel()

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
                messagebox.showerror(self.tr("title_error"), str(e))
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
        self._progress_total_s = 0.0
        self._clear_current_panel()

        self._clear_highlight()
        self._set_running_ui(True)
        # Reflect paused state immediately in the UI.
        try:
            self.btn_pause.config(text=self.tr("btn_resume") if self.state.paused else self.tr("btn_pause"))
        except Exception:
            pass

        self.status_var.set(self.tr("status_launching") if not start_paused else self.tr("status_restored_paused"))

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
                    enable_outputs_on_start=bool(self.enable_outputs_on_start.get()),
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
            messagebox.showerror(self.tr("title_error"), self.tr("msg_select_port"))
            return

        src_text = self.editor.get("1.0", "end-1c") if hasattr(self, "editor") else ""
        try:
            cmd_path = self._get_effective_commands_path_for_run()
            steps = parse_csv_commands(cmd_path)
        except Exception as e:
            messagebox.showerror(self.tr("title_csv_error"), self._format_csv_error_for_ui(e, src_text))
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
            self._log(self.tr("log_auto_resume_disabled_parse"))
            try:
                messagebox.showerror(self.tr("title_csv_error"), self._format_csv_error_for_ui(e, src_text))
            except Exception:
                pass
            return

        self._resume_autostart_done = True
        self._log(self.tr("log_auto_resume"))
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
            if (not self._cp_infinite) and self._cp_base_ts:
                now2 = time.monotonic()
                if not was_paused:
                    delta2 = max(0.0, float(now2 - self._cp_base_ts))
                    self._cp_base_rem_s = max(0.0, float(self._cp_base_rem_s) - delta2)
                    self._cp_base_ts = now2
                else:
                    self._cp_base_ts = now2
        except Exception:
            pass

        self.state.paused = not was_paused
        self.btn_pause.config(text=self.tr("btn_resume") if self.state.paused else self.tr("btn_pause"))
        self._log(self.tr("log_pause") if self.state.paused else self.tr("log_resume"))

    def _next_command(self):
        if not self._running:
            return
        self.state.skip_wait = True
        self._log(self.tr("log_next_command"))

    def _stop(self):
        if not self._running:
            return
        self.state.stopped = True
        self.state.paused = False
        self.state.skip_wait = True
        self._log(self.tr("log_stop"))

    # ---------------- Help / About ----------------

    def _open_url(self, url: str):
        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror(self.tr("title_browser_error"), str(e))

    def _show_help(self):
        # Extracted into jds_controller.gui.ui
        ui.show_help(self)

    def _about(self):
        messagebox.showinfo(
            self.tr("title_about"),
            self.tr("about_text", github=PROJECT_GITHUB_URL, telegram=PROJECT_TELEGRAM_URL)
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

    def _format_freq_label(self, freq_hz: Optional[float]) -> str:
        if freq_hz is None:
            return "--"
        try:
            v = float(freq_hz)
        except Exception:
            return "--"
        if abs(v - round(v)) < 1e-9:
            return f"{int(round(v))} Hz"
        if abs(v) >= 1000:
            return f"{v:,.2f} Hz".replace(",", " ")
        return f"{v:.2f} Hz"

    def _set_current_step_badge(self, current: Optional[int], total: Optional[int]) -> None:
        try:
            cur = int(current) if current is not None else 0
            tot = int(total) if total is not None else 0
        except Exception:
            cur = 0
            tot = 0
        if cur > 0 and tot > 0:
            self.current_step_var.set(f"STEP {cur}/{tot}")
        else:
            self.current_step_var.set("")
        self._refresh_lcd_panel()

    def _refresh_lcd_panel(self) -> None:
        try:
            if hasattr(self, "lcd_panel") and self.lcd_panel is not None:
                self.lcd_panel.set_render_mode(bool(self.heavy_lcd_rendering.get()))
                self.lcd_panel.set_state(
                    primary=self.current_primary_var.get(),
                    secondary=self.current_secondary_var.get(),
                    time_text=self.current_time_var.get(),
                    step_text=self.current_step_var.get(),
                    progress=float(self.current_progress_var.get()),
                )
        except Exception:
            pass

    def _maybe_play_freq_cue(self, *, kind: str, freq_hz: Optional[float], phase: str = "", cue_key=None) -> None:
        if not bool(self.sound_on_finish.get()):
            return
        if (not self._running) or bool(getattr(self.state, "paused", False)):
            return
        if kind == "cycle" and str(phase or "").strip().lower() == "off":
            return
        if kind not in {"freq", "cycle"}:
            return
        if freq_hz is None:
            return
        try:
            key = cue_key if cue_key is not None else (kind, round(float(freq_hz), 6), str(phase or "").strip().lower())
        except Exception:
            return
        if key == self._last_freq_cue:
            return
        self._last_freq_cue = key
        try:
            self._tone_player.play("freq_change")
        except Exception:
            pass

    def _set_current_panel_state(
        self,
        *,
        kind: str,
        freq_hz: Optional[float] = None,
        phase: str = "",
        total_s: float = 0.0,
        rem_s: float = 0.0,
        infinite: bool = False,
        reset_ts: bool = True,
    ) -> None:
        self._cp_kind = str(kind or "idle")
        self._cp_freq_hz = freq_hz if freq_hz is None else float(freq_hz)
        self._cp_phase = str(phase or "")
        self._cp_infinite = bool(infinite)
        self._cp_total_s = max(0.0, float(total_s or 0.0))
        self._cp_base_rem_s = max(0.0, float(rem_s or 0.0))
        if reset_ts:
            self._cp_base_ts = time.monotonic()
        self._refresh_current_panel()

    def _refresh_current_panel(self) -> None:
        kind = self._cp_kind
        phase = (self._cp_phase or "").strip().lower()
        freq_text = self._format_freq_label(self._cp_freq_hz).upper()

        if kind == "wait":
            primary = "WAIT"
            secondary = "WAITING FOR NEXT COMMAND"
        elif kind == "freq":
            primary = freq_text
            secondary = "FIXED FREQUENCY"
        elif kind == "cycle":
            primary = freq_text if self._cp_freq_hz is not None else "CYCLE"
            if phase == "off":
                secondary = "CYCLE  •  PAUSE"
            elif phase == "on":
                secondary = "CYCLE  •  HOLD"
            else:
                secondary = "CYCLE  •  ACTIVE"
        elif kind == "mod":
            primary = freq_text if self._cp_freq_hz is not None else "FM MODULATION"
            if phase == "fall":
                secondary = "FM  •  FALL"
            elif phase == "rise":
                secondary = "FM  •  RISE"
            else:
                secondary = "FM  •  ACTIVE"
        else:
            primary = "NO ACTIVE COMMAND"
            secondary = "PRESS START TO BEGIN"

        self.current_primary_var.set(primary)
        self.current_secondary_var.set(secondary)
        if self._cp_infinite:
            self.current_time_var.set("∞")
            self.current_progress_var.set(0.0)
            self._refresh_lcd_panel()
            return
        if self._cp_total_s > 0.0:
            rem = max(0.0, float(self._cp_base_rem_s))
            self.current_time_var.set(fmt_hhmmss(rem))
            pct = max(0.0, min(100.0, (1.0 - (rem / self._cp_total_s)) * 100.0))
            self.current_progress_var.set(pct)
        else:
            self.current_time_var.set("--:--:--")
            self.current_progress_var.set(0.0)
        self._refresh_lcd_panel()

    def _current_panel_from_step_start(self, step, *, step_index: Optional[int] = None, total_steps: Optional[int] = None) -> None:
        self._set_current_step_badge((step_index + 1) if step_index is not None else None, total_steps)
        if isinstance(step, WaitStep):
            total = float(estimate_step_duration(step, fixed_wait=self._run_fixed_wait))
            self._set_current_panel_state(kind="wait", total_s=total, rem_s=total)
        elif isinstance(step, CycleStep):
            self._set_current_panel_state(kind="cycle", freq_hz=None, phase="", total_s=0.0, rem_s=0.0)
        elif isinstance(step, ModStep):
            total = max(0.0, float(getattr(step, "time_s", 0.0)))
            self._set_current_panel_state(kind="mod", freq_hz=None, phase="", total_s=total, rem_s=total)
        elif hasattr(step, "hz"):
            hz = getattr(step, "hz", None)
            self._set_current_panel_state(kind="freq", freq_hz=float(hz) if hz is not None else None, total_s=0.0, rem_s=0.0)
            try:
                self._maybe_play_freq_cue(kind="freq", freq_hz=float(hz) if hz is not None else None, cue_key=("freq", step_index))
            except Exception:
                pass
        else:
            self._clear_current_panel()

    def _apply_current_panel_checkpoint(self, ck: dict) -> None:
        if not isinstance(ck, dict):
            return
        if self._run_steps is None:
            return
        try:
            step_index = int(ck.get("step_index", 0))
        except Exception:
            return
        if step_index < 0 or step_index >= len(self._run_steps):
            return
        self._set_current_step_badge(step_index + 1, len(self._run_steps))
        step = self._run_steps[step_index]
        within = ck.get("within") if isinstance(ck.get("within"), dict) else None
        if not within:
            self._current_panel_from_step_start(step, step_index=step_index, total_steps=len(self._run_steps))
            return
        now = time.monotonic()
        if isinstance(step, WaitStep) and within.get("kind") == "wait":
            total = float(estimate_step_duration(step, fixed_wait=self._run_fixed_wait))
            rem = max(0.0, float(within.get("remaining", total)))
            self._cp_base_ts = now
            self._set_current_panel_state(kind="wait", total_s=total, rem_s=rem, reset_ts=False)
            return
        if isinstance(step, CycleStep) and within.get("kind") in {"cycle", "cycle_wait"}:
            def _eff_wait_local(w):
                if w is None:
                    return 0.0
                try:
                    wv = float(w)
                except Exception:
                    return 0.0
                if wv <= 0.0:
                    return 0.0
                if self._run_fixed_wait is not None:
                    try:
                        return max(0.0, float(self._run_fixed_wait))
                    except Exception:
                        return max(0.0, wv)
                return max(0.0, wv)
            on_s = _eff_wait_local(getattr(step, "on_wait", 0.0))
            off_s = _eff_wait_local(getattr(step, "off_wait", None)) if getattr(step, "off_wait", None) is not None else 0.0
            kind = str(within.get("kind", ""))
            phase = str(within.get("phase", "on" if kind == "cycle" else "")).strip().lower() or ("on" if kind == "cycle" else "")
            freq_hz = within.get("freq_hz")
            try:
                freq_hz = None if freq_hz is None else float(freq_hz)
            except Exception:
                freq_hz = None
            if kind == "cycle_wait":
                try:
                    rem_phase = max(0.0, float(within.get("remaining", 0.0)))
                except Exception:
                    rem_phase = 0.0
                rem = rem_phase + (off_s if phase == "on" else 0.0)
            else:
                rem = on_s + off_s
            total = max(0.0, on_s + off_s)
            self._cp_base_ts = now
            self._set_current_panel_state(kind="cycle", freq_hz=freq_hz, phase=phase, total_s=total, rem_s=rem, reset_ts=False)
            cue_key = ("cycle", step_index, within.get("item_i"), within.get("sub_k"), phase)
            self._maybe_play_freq_cue(kind="cycle", freq_hz=freq_hz, phase=phase, cue_key=cue_key)
            return
        if isinstance(step, ModStep) and within.get("kind") == "mod":
            total = max(0.0, float(getattr(step, "time_s", 0.0)))
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
            frac = max(0.0, min(1.0, k / float(updates)))
            rem = total * (1.0 - frac)
            inf = False
            freq_hz = within.get("freq_hz")
            try:
                freq_hz = None if freq_hz is None else float(freq_hz)
            except Exception:
                freq_hz = None
            self._cp_base_ts = now
            self._set_current_panel_state(kind="mod", freq_hz=freq_hz, phase=str(within.get("leg", "")), total_s=total, rem_s=rem, infinite=inf, reset_ts=False)
            return
        self._current_panel_from_step_start(step, step_index=step_index, total_steps=len(self._run_steps))

    def _clear_current_panel(self) -> None:
        self._set_current_panel_state(kind="idle", freq_hz=None, total_s=0.0, rem_s=0.0)
        self._last_freq_cue = None
        self._set_current_step_badge(None, None)

    def _play_finish_sound(self, event: str = "file_done") -> None:
        try:
            self._tone_player.play(event)
        except Exception:
            try:
                self.bell()
            except Exception:
                pass

    def _on_sound_toggle(self) -> None:
        try:
            enabled = bool(self.sound_on_finish.get())
        except Exception:
            enabled = False
        if not enabled:
            return
        try:
            started = bool(self._tone_player.play("sound_test"))
            if not started:
                try:
                    self._log("Sound test: skipped")
                except Exception:
                    pass
                return

            def _report_backend() -> None:
                try:
                    backend = self._tone_player.last_backend
                    if getattr(self._tone_player, "running_under_sudo", False):
                        self._log(f"Sound test: {backend} (launched under sudo)")
                    else:
                        self._log(f"Sound test: {backend}")
                except Exception:
                    pass

            try:
                self.after(250, _report_backend)
            except Exception:
                _report_backend()
        except Exception:
            try:
                self.bell()
                try:
                    self._log("Sound test: tk-bell fallback")
                except Exception:
                    pass
            except Exception:
                pass

    def _on_space_pause_resume(self, event=None):
        if not self._running:
            return None
        widget = None
        try:
            widget = self.focus_get()
        except Exception:
            widget = None
        text_classes = {"Text", "Entry", "TEntry", "Spinbox", "TCombobox", "Combobox"}
        try:
            if widget is not None and widget.winfo_class() in text_classes:
                return None
        except Exception:
            pass
        self._toggle_pause()
        return "break"

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
        s = self._initial_settings if isinstance(self._initial_settings, dict) else load_settings()
        # init ports
        self._refresh_ports(do_probe=False)

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
        if isinstance(s.get("enable_outputs_on_start"), bool):
            self.enable_outputs_on_start.set(s["enable_outputs_on_start"])
        if isinstance(s.get("disable_outputs_on_finish"), bool):
            self.disable_outputs_on_finish.set(s["disable_outputs_on_finish"])
        if isinstance(s.get("shutdown_pc_on_finish"), bool):
            self.shutdown_pc_on_finish.set(s["shutdown_pc_on_finish"])
        if isinstance(s.get("sound_on_finish"), bool):
            self.sound_on_finish.set(s["sound_on_finish"])
        if isinstance(s.get("dark_theme"), bool):
            self.dark_theme.set(s["dark_theme"])
        if isinstance(s.get("heavy_lcd_rendering"), bool):
            self.heavy_lcd_rendering.set(s["heavy_lcd_rendering"])
        self._apply_theme()

        fp = s.get("file_path")
        if isinstance(fp, str) and fp and Path(fp).exists():
            self._open_file(Path(fp))
        else:
            # load a default sample if available
            default = self._default_startup_commands_file()
            if default is not None and default.exists():
                self._open_file(default)

        port = s.get("port")
        if isinstance(port, str) and port:
            # Set as-is; if it's a label, extract will map.
            # Connect directly; probing here only creates a startup race on the first launch.
            self.port_var.set(port)
            self._connect_selected_port_async(silent=True)
        else:
            # On first launch without saved settings, probe the auto-selected port only.
            self._probe_selected_port_async()

        # If a resume point exists for the startup file, auto-enter the paused execution state.
        # Run this after initial UI+settings setup.
        try:
            self.after(50, self._maybe_autostart_resume_paused)
        except Exception:
            pass

    def _persist_settings(self):
        s = self._initial_settings if isinstance(self._initial_settings, dict) else load_settings()
        if not isinstance(s, dict):
            s = {}
        s.update({
            "file_path": str(self.current_file) if self.current_file else "",
            "port": self.port_var.get(),
            "channel": self.channel_var.get(),
            "wait_override_enabled": bool(self.wait_override_enabled.get()),
            "wait_override_seconds": self.wait_override_seconds.get(),
            "repeat_file_enabled": bool(self.repeat_file_enabled.get()),
            "enable_outputs_on_start": bool(self.enable_outputs_on_start.get()),
            "disable_outputs_on_finish": bool(self.disable_outputs_on_finish.get()),
            "shutdown_pc_on_finish": bool(self.shutdown_pc_on_finish.get()),
            "sound_on_finish": bool(self.sound_on_finish.get()),
            "dark_theme": bool(self.dark_theme.get()),
            "heavy_lcd_rendering": bool(self.heavy_lcd_rendering.get()),
            "language": self.lang,
        })
        save_settings(s)

    def _on_close(self):
        # Close flow:
        # 1) Handle unsaved edits (Save / Don't Save / Cancel)
        # 2) If a script is running/paused, persist the current execution checkpoint for a saved file
        # 3) Persist general settings
        allow_resume_save = True

        if self._dirty:
            res = messagebox.askyesnocancel(self.tr("title_unsaved"), self.tr("msg_unsaved"))
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
                    txt = translate_runtime_text(str(payload), self.lang)
                    self.status_var.set(txt)
                    self._log(txt)

                elif kind == MsgKind.PROBE:
                    ok = bool(payload)
                    if ok:
                        self.device_var.set(self.tr("device_found"))
                        self._set_led("ok")
                    else:
                        self.device_var.set(self.tr("device_not_found"))
                        self._set_led("bad")

                elif kind == MsgKind.AUTODETECT:
                    port = str(payload or "")
                    if port:
                        self.port_var.set(port)
                        self.device_var.set(self.tr("device_found"))
                        self._set_led("ok")
                        # Auto-connect after successful auto-detect.
                        # Do not start a separate probe here: it races with connect on first launch.
                        self._connect_selected_port_async()
                        self.status_var.set(self.tr("status_device_found", port=port))
                        self._log(self.tr("log_autodetect_found", port=port))
                    else:
                        self.status_var.set(self.tr("status_device_not_found"))
                        self._log(self.tr("log_autodetect_not_found"))

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
                        if (not self._running) and (self.status_var.get() in (self.tr("status_not_connected"), "", "Не подключено", "Not connected")):
                            self.status_var.set(self.tr("status_connected"))
                    except Exception:
                        pass

                elif kind == MsgKind.DISCONNECTED:
                    self._set_connected_ui(False)
                    try:
                        if not self._running:
                            self.status_var.set(self.tr("status_not_connected"))
                    except Exception:
                        pass

                elif kind == MsgKind.CONNECT_ERROR:
                    self._set_connected_ui(False)
                    show_popup = True
                    err_text = payload
                    try:
                        if isinstance(payload, dict):
                            err_text = str(payload.get("error", ""))
                            show_popup = bool(payload.get("show_popup", True))
                    except Exception:
                        err_text = str(payload)
                        show_popup = True
                    if show_popup:
                        self._log(self.tr("log_connect_error", error=err_text))
                        messagebox.showerror(self.tr("title_connect_error"), str(err_text))

                elif kind == MsgKind.DEVICE_STATE:
                    txt = translate_runtime_text(str(payload), self.lang)
                    self.device_state_var.set(txt)
                    # Keep polling bookkeeping in sync with UI updates
                    self._poll_last_text = txt
                    if txt and txt not in {self.tr("device_state_none"), self.tr("device_state_no_data"), "Нет подключения", "Подключено (нет данных)", "No connection", "Connected (no data)"}:
                        self._poll_last_good_text = txt

                elif kind == MsgKind.CHECKPOINT:
                    # Update smooth remaining-time model during long steps.
                    if isinstance(payload, dict):
                        self._remaining_apply_checkpoint(payload)
                        self._apply_current_panel_checkpoint(payload)

                elif kind == MsgKind.LOG:
                    self._log(translate_runtime_text(str(payload), self.lang))

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
                    if isinstance(line, int):
                        self._highlight_source_line(line)
                    try:
                        if bool(self.sound_on_finish.get()) and self._last_progress_done > 0 and int(done) > int(self._last_progress_done):
                            self.after(0, lambda: self._play_finish_sound("command_done"))
                        self._last_progress_done = max(int(done), int(self._last_progress_done))
                    except Exception:
                        pass
                    try:
                        idx = max(0, int(done) - 1)
                        if self._run_steps is not None and 0 <= idx < len(self._run_steps):
                            self._current_panel_from_step_start(self._run_steps[idx], step_index=idx, total_steps=len(self._run_steps))
                    except Exception:
                        pass
                    if total > 0:
                        # Set countdown base estimate (the timer will update smoothly).
                        self._remaining_set_from_estimate(est)
                    try:
                        import math
                        if math.isfinite(float(est)) and float(est) >= 0.0:
                            if self._progress_total_s <= 0.0:
                                self._progress_total_s = max(0.0, float(est))
                            if self._progress_total_s > 0.0:
                                pct = max(0.0, min(100.0, (1.0 - (float(est) / float(self._progress_total_s))) * 100.0))
                                self.progress_var.set(pct)
                            elif total > 0:
                                self.progress_var.set(0.0)
                        elif total > 0:
                            pct = min(100.0, (done / total) * 100.0)
                            self.progress_var.set(pct)
                    except Exception:
                        if total > 0:
                            pct = min(100.0, (done / total) * 100.0)
                            self.progress_var.set(pct)

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
                    self.status_var.set(self.tr("status_stopped") if rc == 4 else self.tr("status_done"))
                    self._set_running_ui(False)
                    self._clear_highlight()
                    self._clear_current_panel()

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
                            self.status_var.set(self.tr("status_repeat_restart"))
                            self._log(self.tr("log_repeat_restart"))
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

                    # Optional post-finish actions apply only after a real, final completion.
                    if rc == 0:
                        try:
                            self._handle_normal_finish_actions()
                        except Exception:
                            pass
                        if bool(self.sound_on_finish.get()):
                            try:
                                self.after(0, lambda: self._play_finish_sound("file_done"))
                            except Exception:
                                pass

                    # Reconnect after run so that status polling continues in idle.
                    should_shutdown_pc = bool(self.shutdown_pc_on_finish.get()) and rc == 0
                    if self._reconnect_after_run:
                        self._reconnect_after_run = False
                        if (not should_shutdown_pc) and (not self._connected):
                            self._connect_selected_port_async()

                elif kind == MsgKind.ERROR:
                    self.status_var.set(self.tr("status_error"))
                    self._log(self.tr("log_error", error=payload))
                    messagebox.showerror(self.tr("title_error"), str(payload))
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
        if self._progress_total_s <= 0.0 and self._rt_base_rem_s > 0.0:
            self._progress_total_s = float(self._rt_base_rem_s)
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
                        try:
                            import math
                            if self._progress_total_s > 0.0 and math.isfinite(float(total)):
                                pct = max(0.0, min(100.0, (1.0 - (float(total) / float(self._progress_total_s))) * 100.0))
                                self.progress_var.set(pct)
                        except Exception:
                            pass
                if self._cp_infinite:
                    self.current_time_var.set("∞")
                    self.current_progress_var.set(0.0)
                elif self._cp_total_s > 0.0 and self._cp_base_ts:
                    now2 = time.monotonic()
                    delta2 = 0.0 if bool(getattr(self.state, "paused", False)) else (now2 - self._cp_base_ts)
                    rem2 = max(0.0, float(self._cp_base_rem_s) - float(delta2))
                    self.current_time_var.set(fmt_hhmmss(rem2))
                    pct2 = max(0.0, min(100.0, (1.0 - (rem2 / self._cp_total_s)) * 100.0))
                    self.current_progress_var.set(pct2)
                self._refresh_lcd_panel()
        finally:
            self.after(200, self._tick_remaining_time)



    # --- Connection & status polling -------------------------------------------------

    def _set_connected_ui(self, connected: bool, port: str = "") -> None:
        """Update UI when connection state changes (runs in UI thread)."""
        self._connected = connected
        self._connected_port = (port or self._connected_port) if connected else None
        if hasattr(self, "btn_connect"):
            self.btn_connect.configure(text=self.tr("btn_disconnect") if connected else self.tr("btn_connect"))
        if connected:
            # Force next poll to update the status bar (prevents it from being stuck on "Подключено...").
            self._poll_last_text = None
            try:
                self._poll_force.set()
            except Exception:
                pass
        else:
            self.device_state_var.set(self.tr("device_state_none"))
            self._poll_last_text = self.tr("device_state_none")
            self._poll_last_good_text = None
            try:
                self._poll_force.set()
            except Exception:
                pass

    def _toggle_connection(self) -> None:
        if self._running:
            # during execution we keep the script priority; manual connect/disconnect is disabled
            self._log(self.tr("log_connect_managed"))
            return
        if self._connected:
            self._disconnect_async()
        else:
            self._connect_selected_port_async()

    def _connect_selected_port_async(self, silent: bool = False) -> None:
        port = self._extract_port_value(self.port_var.get())
        if not port:
            self._log(self.tr("log_port_not_selected"))
            return
        # Already connected to this port
        if self._connected and self._connected_port == port:
            return

        def worker():
            import jds6600
            last_err = None
            for attempt in range(2):
                try:
                    with self._io_lock:
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
                    return
                except Exception as e:
                    last_err = e
                    try:
                        time.sleep(0.25)
                    except Exception:
                        pass
            self.msgq.put(GuiMsg(MsgKind.CONNECT_ERROR, {"error": str(last_err), "show_popup": (not silent)}))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_normal_finish_actions(self) -> None:
        disable_outputs = bool(self.disable_outputs_on_finish.get())
        shutdown_pc = bool(self.shutdown_pc_on_finish.get())
        if not disable_outputs and not shutdown_pc:
            return

        def worker() -> None:
            if disable_outputs:
                port = self._extract_port_value(self.port_var.get())
                if port:
                    try:
                        import jds6600
                        done = False

                        # First, try the existing GUI connection if it is already alive.
                        with self._fg_lock:
                            fg_existing = self._fg
                        if fg_existing is not None:
                            try:
                                fg_existing.set_channels(channel1=False, channel2=False)
                                done = True
                            except Exception:
                                done = False

                        # If the port is still being released by the runner, wait a bit and retry.
                        if not done:
                            last_err = None
                            for _ in range(12):
                                fg = None
                                try:
                                    with self._io_lock:
                                        fg = jds6600.JDS6600(port=port)
                                        fg.connect()
                                        fg.set_channels(channel1=False, channel2=False)
                                    done = True
                                    break
                                except Exception as e:
                                    last_err = e
                                    try:
                                        time.sleep(0.25)
                                    except Exception:
                                        pass
                                finally:
                                    if fg is not None:
                                        try:
                                            fg.close()
                                        except Exception:
                                            pass
                            if not done and last_err is not None:
                                raise last_err

                        self.msgq.put(GuiMsg(MsgKind.LOG, self.tr("log_outputs_off_on_finish")))
                    except Exception as e:
                        self.msgq.put(GuiMsg(MsgKind.LOG, self.tr("log_outputs_off_failed", error=e)))
            if shutdown_pc:
                try:
                    if sys.platform.startswith("win"):
                        cmd = ["shutdown", "/s", "/t", "0"]
                    elif sys.platform == "darwin":
                        cmd = ["osascript", "-e", 'tell application "System Events" to shut down']
                    elif sys.platform.startswith("linux"):
                        try_cmds = [["shutdown", "-h", "now"], ["systemctl", "poweroff"]]
                        last_err = None
                        for cmd_try in try_cmds:
                            try:
                                subprocess.Popen(cmd_try)
                                self.msgq.put(GuiMsg(MsgKind.LOG, self.tr("log_shutdown_pc_on_finish")))
                                return
                            except Exception as e:
                                last_err = e
                        raise RuntimeError(str(last_err) if last_err else self.tr("log_shutdown_pc_unsupported"))
                    else:
                        raise RuntimeError(self.tr("log_shutdown_pc_unsupported"))
                    subprocess.Popen(cmd)
                    self.msgq.put(GuiMsg(MsgKind.LOG, self.tr("log_shutdown_pc_on_finish")))
                except Exception as e:
                    self.msgq.put(GuiMsg(MsgKind.LOG, self.tr("log_shutdown_pc_failed", error=e)))

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
                text = self.tr("device_state_none")
                if text != self._poll_last_text:
                    self.msgq.put(GuiMsg(MsgKind.DEVICE_STATE, text))
                    self._poll_last_text = text
                continue

            with self._fg_lock:
                fg = self._fg

            if fg is None:
                text = self.tr("device_state_none")
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
                        self.msgq.put(GuiMsg(MsgKind.LOG, self.tr("log_status_poll_error", error=e)))
                    text = self._poll_last_good_text or self.tr("device_state_no_data")
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
