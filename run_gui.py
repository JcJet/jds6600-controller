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

import os
import sys
import json
import time
import hashlib
import queue
import tempfile
import threading
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

# Tkinter is a system package on many Linux distros (python3-tk)
try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:
    print("Tkinter is not available. On Ubuntu/Debian run:", file=sys.stderr)
    print("  sudo apt update && sudo apt install -y python3-tk", file=sys.stderr)
    raise

from jds_controller.commands import parse_csv_commands
from jds_controller.ports import (
    find_first_jds6600,
    list_serial_ports,
    list_linux_by_id_ports,
    PortInfo,
)
from jds_controller.runner import RunnerState, run_sequence
from jds_controller.device_state import read_device_state, format_device_state
from jds_controller.util import fmt_seconds


PROJECT_GITHUB_URL = "https://github.com/JcJet/jds6600-controller"
PROJECT_TELEGRAM_URL = "https://t.me/JcJet"


def _settings_path() -> Path:
    home = Path.home()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(home)
        return Path(base) / "JDS6600Controller" / "settings.json"
    return home / ".jds6600_controller" / "settings.json"


def load_settings() -> dict:
    p = _settings_path()
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_settings(data: dict) -> None:
    p = _settings_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        # best effort
        pass


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
        self.msgq: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.state = RunnerState()
        self._running = False

        # file state
        self.current_file: Optional[Path] = None
        self._temp_run_file: Optional[Path] = None
        self._dirty = False
        self._suppress_modified = False

        # resume state (persisted execution position)
        self._resume_available = False
        self._resume_info: Optional[dict] = None
        self._resume_checkpoint: Optional[dict] = None
        self._run_file_sha256: Optional[str] = None

        # variables
        self.port_var = tk.StringVar(value="")
        self.channel_var = tk.StringVar(value="1+2")
        self.wait_override_enabled = tk.BooleanVar(value=False)
        self.wait_override_seconds = tk.StringVar(value="0")
        self.repeat_file_enabled = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Не подключено")
        self.device_var = tk.StringVar(value="не проверено")
        self.progress_var = tk.DoubleVar(value=0.0)

        self.device_state_var = tk.StringVar(value="Нет подключения")

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
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        # style
        try:
            import tkinter.font as tkfont
            style = ttk.Style(self)
            big_font = tkfont.nametofont("TkDefaultFont").copy()
            big_font.configure(size=max(12, big_font.cget("size") + 4), weight="bold")
            style.configure("Big.TButton", font=big_font, padding=(18, 10))
        except Exception:
            pass

        # menu
        menubar = tk.Menu(self)

        filemenu = tk.Menu(menubar, tearoff=False)
        filemenu.add_command(label="Открыть…", command=self._browse_open)
        filemenu.add_command(label="Сохранить", command=self._save)
        filemenu.add_command(label="Сохранить как…", command=self._save_as)
        filemenu.add_separator()
        filemenu.add_command(label="Новый шаблон", command=self._new_template)
        filemenu.add_separator()
        filemenu.add_command(label="Выход", command=self._on_close)
        menubar.add_cascade(label="Файл", menu=filemenu)

        runmenu = tk.Menu(menubar, tearoff=False)
        runmenu.add_command(label="Старт", command=self._start)
        runmenu.add_command(label="Пауза/Продолжить", command=self._toggle_pause)
        runmenu.add_command(label="Следующая команда (пропустить wait)", command=self._next_command)
        runmenu.add_command(label="Стоп", command=self._stop)
        runmenu.add_separator()
        runmenu.add_command(label="Проверить CSV", command=self._validate)
        menubar.add_cascade(label="Выполнение", menu=runmenu)

        helpmenu = tk.Menu(menubar, tearoff=False)
        helpmenu.add_command(label="Краткая помощь\tF1", command=self._show_help)
        helpmenu.add_command(label="GitHub…", command=lambda: self._open_url(PROJECT_GITHUB_URL))
        helpmenu.add_separator()
        helpmenu.add_command(label="О программе", command=self._about)
        menubar.add_cascade(label="Справка", menu=helpmenu)

        self.config(menu=menubar)
        self.bind("<F1>", lambda e: self._show_help())

        # top controls (ports)
        frm_top = ttk.LabelFrame(self, text="Подключение")
        frm_top.pack(fill="x", **pad)

        ttk.Label(frm_top, text="Порт:").grid(row=0, column=0, sticky="e", **pad)
        self.port_combo = ttk.Combobox(frm_top, textvariable=self.port_var, width=65, state="readonly")
        self.port_combo.grid(row=0, column=1, sticky="we", **pad)
        self.port_combo.bind("<<ComboboxSelected>>", lambda e: self._probe_selected_port_async())

        ttk.Button(frm_top, text="Обновить", command=self._refresh_ports).grid(row=0, column=2, **pad)
        ttk.Button(frm_top, text="Найти и подключиться", command=self._auto_detect).grid(row=0, column=3, **pad)
        self.btn_connect = ttk.Button(frm_top, text="Подключиться", command=self._toggle_connection)
        self.btn_connect.grid(row=0, column=4, **pad)

        # Fixed-size device status area (prevents combobox width jumps when text changes)
        self._devinfo = ttk.Frame(frm_top)
        self._devinfo.grid(row=0, column=5, sticky="w", padx=(6, 0), pady=6)
        try:
            self._devinfo.configure(width=240)
            self._devinfo.grid_propagate(False)
        except Exception:
            pass

        self.device_led = tk.Canvas(self._devinfo, width=14, height=14, highlightthickness=0)
        self.device_led.pack(side="left")
        self._led_item = self.device_led.create_oval(2, 2, 12, 12, fill="#999999", outline="")
        self.device_label = ttk.Label(self._devinfo, textvariable=self.device_var, width=22, anchor="w")
        self.device_label.pack(side="left", padx=(8, 0))

        frm_top.columnconfigure(1, weight=1)
        frm_top.columnconfigure(5, weight=0, minsize=240)

        # controls (start/stop etc)
        frm_ctrl = ttk.Frame(self)
        frm_ctrl.pack(fill="x", **pad)

        self.btn_start = ttk.Button(frm_ctrl, text="СТАРТ", command=self._start, style="Big.TButton")
        self.btn_start.pack(side="left", padx=8)

        self.btn_pause = ttk.Button(frm_ctrl, text="Пауза", command=self._toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=8)

        self.btn_next = ttk.Button(frm_ctrl, text="Следующая команда", command=self._next_command, state="disabled")
        self.btn_next.pack(side="left", padx=8)

        self.btn_stop = ttk.Button(frm_ctrl, text="Стоп", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)

        ttk.Separator(frm_ctrl, orient="vertical").pack(side="left", fill="y", padx=10)

        ttk.Label(frm_ctrl, text="Канал (по умолчанию):").pack(side="left", padx=(4, 6))
        ttk.Combobox(frm_ctrl, textvariable=self.channel_var, state="readonly",
                     values=["1+2", "1", "2"], width=8).pack(side="left")

        ttk.Separator(frm_ctrl, orient="vertical").pack(side="left", fill="y", padx=10)

        # wait override + repeat file (stacked in one column)
        frm_waitcol = ttk.Frame(frm_ctrl)
        frm_waitcol.pack(side="left", padx=(6, 6), anchor="n")

        self.chk_wait_override = ttk.Checkbutton(
            frm_waitcol,
            text="Фиксированный wait (сек):",
            variable=self.wait_override_enabled
        )
        self.chk_wait_override.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 2))

        self.ent_wait_override = ttk.Entry(frm_waitcol, textvariable=self.wait_override_seconds, width=8)
        self.ent_wait_override.grid(row=0, column=1, sticky="w", pady=(0, 2))

        self.chk_repeat_file = ttk.Checkbutton(frm_waitcol, text="Повтор файла", variable=self.repeat_file_enabled)
        self.chk_repeat_file.grid(row=1, column=0, columnspan=2, sticky="w")

        ttk.Button(frm_ctrl, text="Проверить CSV", command=self._validate).pack(side="right", padx=8)

        # progress + status
        frm_status = ttk.Frame(self)
        frm_status.pack(fill="x", **pad)

        self.pb = ttk.Progressbar(frm_status, mode="determinate", maximum=100.0, variable=self.progress_var)
        self.pb.pack(fill="x", expand=True, side="left", padx=(0, 10))

        ttk.Label(frm_status, textvariable=self.status_var).pack(side="right")

        # editor + log split
        frm_mid = ttk.PanedWindow(self, orient="horizontal")
        frm_mid.pack(fill="both", expand=True, **pad)

        # editor frame
        frm_editor = ttk.LabelFrame(frm_mid, text="Файл команд (редактируемый)")
        frm_mid.add(frm_editor, weight=3)

        self.editor = tk.Text(frm_editor, wrap="char", undo=True)
        self.editor.pack(side="left", fill="both", expand=True)

        vs = ttk.Scrollbar(frm_editor, orient="vertical", command=self.editor.yview)
        vs.pack(side="right", fill="y")
        self.editor.configure(yscrollcommand=vs.set)


        self.editor.tag_configure("current_line", background="#fff3c4")
        self.editor.bind("<<Modified>>", self._on_modified)

        # editor context menu + reliable Ctrl shortcuts (works even with RU layout on Windows)
        self._build_editor_context_menu()
        self.editor.bind("<Button-3>", self._show_editor_context_menu, add=True)
        self.editor.bind("<Control-Button-1>", self._show_editor_context_menu, add=True)  # macOS
        self.editor.bind("<Control-KeyPress>", self._on_editor_ctrl_shortcut, add=True)

        # log frame
        frm_log = ttk.LabelFrame(frm_mid, text="Лог выполнения")
        frm_mid.add(frm_log, weight=2)

        # NOTE: pack order matters.
        # If you pack the Text first with fill="both", it can consume all space and
        # the scrollbar gets squeezed into a tiny widget (looks like a weird mini-scrollbar
        # in the corner). Pack the scrollbar first, then the Text.
        vsl = ttk.Scrollbar(frm_log, orient="vertical")
        vsl.pack(side="right", fill="y")

        self.log = tk.Text(
            frm_log,
            height=10,
            wrap="word",
            state="disabled",
            yscrollcommand=vsl.set,
        )
        self.log.pack(side="left", fill="both", expand=True)
        vsl.configure(command=self.log.yview)


        # Bottom status bar (generator state)
        ttk.Separator(self, orient="horizontal").pack(fill="x")
        frm_statusbar = ttk.Frame(self)
        frm_statusbar.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Label(frm_statusbar, textvariable=self.device_state_var, anchor="w").pack(fill="x")

    # ---------------- File ops ----------------

    def _set_dirty(self, dirty: bool):
        self._dirty = dirty
        title = "JDS6600 Controller"
        if self.current_file:
            title += f" — {self.current_file.name}"
        if self._dirty:
            title += " *"
        self.title(title)

        # resume is only valid for a saved, clean file
        if self._dirty:
            self._resume_available = False
            self._resume_info = None
            self._resume_checkpoint = None
            self._update_start_button_label()
        else:
            self._load_resume_for_current_file()

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
        self._load_resume_for_current_file()

    def _save(self):
        if not self.current_file:
            return self._save_as()
        try:
            self.current_file.write_text(self.editor.get("1.0", "end-1c"), encoding="utf-8")
            self._set_dirty(False)
            self._log(f"Сохранено: {self.current_file}")
            self._load_resume_for_current_file()
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
            self.msgq.put(("probe", "1" if ok else "0"))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_detect(self):
        self.status_var.set("Поиск устройства…")
        self._log("Авто-поиск устройства…")

        def worker():
            import jds6600
            try:
                port = find_first_jds6600()
                self.msgq.put(("autodetect", port or ""))
            except Exception as e:
                self.msgq.put(("error", f"Авто-поиск: {e}"))

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
        try:
            p = self._get_effective_commands_path_for_run()
            parse_csv_commands(p)
            if self.wait_override_enabled.get():
                _ = self._get_wait_override_seconds()
            messagebox.showinfo("Проверка", "Файл команд корректен.")
        except Exception as e:
            messagebox.showerror("Ошибка CSV", str(e))

    def _set_running_ui(self, running: bool):
        self._running = running
        self.btn_start.config(state="disabled" if running else "normal")
        self.btn_pause.config(state="normal" if running else "disabled")
        self.btn_next.config(state="normal" if running else "disabled")
        self.btn_stop.config(state="normal" if running else "disabled")
        if not running:
            self.btn_pause.config(text="Пауза")
            # When run finishes, restore the correct Start/Resume label.
            try:
                self._update_start_button_label()
            except Exception:
                pass

    # ---------------- Resume helpers (auto-resume for saved, clean file) ----------------

    def _file_sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _update_start_button_label(self) -> None:
        """Update Start button label based on whether a valid resume point exists."""
        if hasattr(self, "btn_start"):
            if (not self._running) and self._resume_available and (not self._dirty) and self.current_file is not None:
                self.btn_start.configure(text="ПРОДОЛЖИТЬ")
            else:
                self.btn_start.configure(text="СТАРТ")

    def _clear_resume(self) -> None:
        """Remove persisted resume point from settings and reset in-memory state."""
        s = load_settings()
        if not isinstance(s, dict):
            s = {}
        if "resume" in s:
            try:
                del s["resume"]
            except Exception:
                pass
            save_settings(s)

        self._resume_available = False
        self._resume_info = None
        self._resume_checkpoint = None
        self._update_start_button_label()

    def _persist_resume(self, checkpoint: dict) -> None:
        """Persist resume checkpoint for the current file (saved + clean only)."""
        if self.current_file is None or self._dirty or (not self.current_file.exists()):
            return
        file_sha = self._file_sha256(self.current_file)
        s = load_settings()
        if not isinstance(s, dict):
            s = {}
        info = {
            "v": 1,
            "file_path": str(self.current_file),
            "file_sha256": file_sha,
            "checkpoint": checkpoint,
            "saved_at": int(time.time()),
        }
        s["resume"] = info
        save_settings(s)
        self._resume_available = True
        self._resume_info = info
        self._resume_checkpoint = checkpoint
        self._update_start_button_label()

    def _load_resume_for_current_file(self) -> None:
        """Load persisted resume point if it matches the current saved file."""
        self._resume_available = False
        self._resume_info = None
        self._resume_checkpoint = None

        # resume only for saved + clean file
        if self._dirty or self.current_file is None or (not self.current_file.exists()):
            self._update_start_button_label()
            return

        s = load_settings()
        if not isinstance(s, dict):
            self._update_start_button_label()
            return
        r = s.get("resume")
        if not isinstance(r, dict):
            self._update_start_button_label()
            return
        if int(r.get("v", 0)) != 1:
            self._update_start_button_label()
            return

        fp = r.get("file_path")
        fsha = r.get("file_sha256")
        ck = r.get("checkpoint")
        if not (isinstance(fp, str) and fp and isinstance(fsha, str) and fsha and isinstance(ck, dict)):
            self._update_start_button_label()
            return

        try:
            cur_abs = os.path.abspath(str(self.current_file))
            saved_abs = os.path.abspath(fp)
        except Exception:
            cur_abs = str(self.current_file)
            saved_abs = fp
        if cur_abs != saved_abs:
            self._update_start_button_label()
            return

        try:
            cur_sha = self._file_sha256(self.current_file)
        except Exception:
            self._update_start_button_label()
            return
        if cur_sha != fsha:
            self._update_start_button_label()
            return

        self._resume_available = True
        self._resume_info = r
        self._resume_checkpoint = ck
        self._update_start_button_label()

    def _start(self):
        if self.worker and self.worker.is_alive():
            return

        port = self._extract_port_value(self.port_var.get())
        if not port:
            messagebox.showerror("Ошибка", "Выберите порт (или нажмите Авто-поиск).")
            return

        resume_ck: Optional[dict] = None
        try:
            # Auto-resume only for a saved, clean file (no dialogs).
            if self._resume_available and (not self._dirty) and self.current_file is not None and self.current_file.exists():
                cmd_path = self.current_file
                resume_ck = self._resume_checkpoint if isinstance(self._resume_checkpoint, dict) else None
            else:
                cmd_path = self._get_effective_commands_path_for_run()
            steps = parse_csv_commands(cmd_path)
        except Exception as e:
            messagebox.showerror("Ошибка CSV", str(e))
            return

        # Remember hash of the executed file to validate resume saving on close.
        try:
            self._run_file_sha256 = self._file_sha256(Path(cmd_path))
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

        self.state.paused = False
        self.state.stopped = False
        self.state.skip_wait = False
        self.progress_var.set(0.0)
        self._clear_highlight()
        self._set_running_ui(True)
        self.status_var.set("Запуск…")

        def on_status(msg: str):
            self.msgq.put(("status", msg))

        def on_progress(i: int, total: int, est_remaining_wait: float, step) -> None:
            # runner.py calls on_progress(i, total, est_remaining, step)
            done = int(i) + 1  # i is 0-based
            line_no = getattr(step, "source_line", None)
            try:
                line_no_int = int(line_no) if line_no is not None else None
            except Exception:
                line_no_int = None
            payload = json.dumps({
                "done": done,
                "total": int(total),
                "line": line_no_int,
                "est": float(est_remaining_wait),
            })
            self.msgq.put(("progress", payload))
        def worker():
            import jds6600
            try:
                # Ensure the GUI connection does not block the script runner.
                was_connected = self._connected
                if was_connected:
                    self._disconnect_sync()
                    self.msgq.put(("disconnected", ""))
                self._reconnect_after_run = True
                self._reconnect_after_run_port = port
                rc = run_sequence(
                    steps,
                    port=port,
                    default_channel=("1+2" if self.channel_var.get()=="1+2" else self.channel_var.get()),
                    state=self.state,
                    on_status=on_status,
                    on_progress=on_progress,
                    on_device_state=lambda txt: self.msgq.put(("device_state", txt)),
                    state_poll_interval=1.0,
                    tick_wait_updates=False,
                    fixed_wait_seconds=fixed_wait,
                    resume=resume_ck,
                )
                self.msgq.put(("done", json.dumps({"rc": int(rc)})))
            except Exception as e:
                self.msgq.put(("error", str(e)))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _toggle_pause(self):
        if not self._running:
            return
        self.state.paused = not self.state.paused
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
        HELP_TEXT = """Формат файла команд (CSV):

  freq,<Hz>[,<опциональные настройки>]
  wait,<секунды>
  stop
  cycle,[Hz1,Hz2,...],on=<сек>,off=<сек>[,<опциональные настройки>]

Примеры:
  freq,1000,{"channel":"1+2","waveform":"sine","amplitude":1.0}
  wait,2
  freq,2000,{"channel":1,"waveform":"square","dutycycle":30,"amplitude":2.0}

  cycle,[1000,2000,3000],on=5,off=10,{"channel":"1+2","waveform":"sine","amplitude":1.0}

Настройки (3-й параметр) — рекомендуем строгий JSON (двойные кавычки, без лишних запятых).
Для удобства допускается сокращённый вариант без кавычек у ключей/строк, но лучше писать JSON.

Разделитель CSV определяется автоматически: запятая, точка-с-запятой или таб.
Если редактируете в Excel/LibreOffice и файл ломается — попробуйте разделитель ';' или редактируйте здесь, в программе.

Кнопка «Следующая команда» пропускает текущий wait.
Опция «Фиксированный wait» заменяет длительность всех wait во время выполнения.
В редакторе есть контекстное меню (ПКМ) и горячие клавиши копировать/вставить (Ctrl+C/Ctrl+V)."""
        messagebox.showinfo("Краткая помощь", HELP_TEXT)

    def _about(self):
        messagebox.showinfo(
            "О программе",
            "JDS6600 Controller\n\nGUI/CLI утилита для управления генератором JDS6600.\n"
            f"GitHub: {PROJECT_GITHUB_URL}\nTelegram: {PROJECT_TELEGRAM_URL} (@JcJet)"
        )

    

    # ---------------- Editor context menu & shortcuts ----------------

    def _build_editor_context_menu(self):
        # Context menu for the command editor (right click)
        self._editor_menu = tk.Menu(self, tearoff=0)
        self._editor_menu.add_command(label="Отменить", command=self._editor_undo)
        self._editor_menu.add_command(label="Повторить", command=self._editor_redo)
        self._editor_menu.add_separator()
        self._editor_menu.add_command(label="Вырезать", command=lambda: self.editor.event_generate("<<Cut>>"))
        self._editor_menu.add_command(label="Копировать", command=lambda: self.editor.event_generate("<<Copy>>"))
        self._editor_menu.add_command(label="Вставить", command=lambda: self.editor.event_generate("<<Paste>>"))
        self._editor_menu.add_separator()
        self._editor_menu.add_command(label="Выделить всё", command=self._editor_select_all)

    def _show_editor_context_menu(self, event):
        try:
            self.editor.focus_set()
            # Place cursor where the user clicked
            try:
                self.editor.mark_set("insert", f"@{event.x},{event.y}")
            except Exception:
                pass
            self._editor_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._editor_menu.grab_release()
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
        """Fix Ctrl+C / Ctrl+V etc. for non-Latin keyboard layouts (Windows)."""
        sym = (event.keysym or "").lower()

        # Russian layout: C -> 'с' (Cyrillic_es), V -> 'м' (Cyrillic_em), X -> 'ч' (Cyrillic_che), A -> 'ф' (Cyrillic_ef)
        mapping = {
            "c": "<<Copy>>",
            "cyrillic_es": "<<Copy>>",
            "v": "<<Paste>>",
            "cyrillic_em": "<<Paste>>",
            "x": "<<Cut>>",
            "cyrillic_che": "<<Cut>>",
            "a": "<<SelectAll>>",
            "cyrillic_ef": "<<SelectAll>>",
            "z": "<<Undo>>",
            "cyrillic_ya": "<<Undo>>",
            "y": "<<Redo>>",
            "cyrillic_en": "<<Redo>>",
        }

        action = mapping.get(sym)
        if not action:
            return None

        if action == "<<SelectAll>>":
            self._editor_select_all()
            return "break"
        if action == "<<Undo>>":
            self._editor_undo()
            return "break"
        if action == "<<Redo>>":
            self._editor_redo()
            return "break"

        # Copy/Cut/Paste
        self.editor.event_generate(action)
        return "break"

# ---------------- Logging / editor highlight ----------------

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

        self._update_start_button_label()

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
                    self._clear_resume()
                except Exception:
                    pass

        # Persist resume checkpoint (auto-resume) only for a saved, clean file.
        if allow_resume_save:
            try:
                if self._running and (self.current_file is not None) and (not self._dirty) and self.current_file.exists():
                    ck = getattr(self.state, "resume_checkpoint", None)
                    if isinstance(ck, dict):
                        # Validate that the on-disk file matches the file content that was actually executed.
                        file_sha = self._file_sha256(self.current_file)
                        if file_sha and (self._run_file_sha256 is None or file_sha == self._run_file_sha256):
                            self._persist_resume(checkpoint=ck)
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
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "status":
                    self.status_var.set(payload)
                    self._log(payload)
                elif kind == "probe":
                    if payload == "1":
                        self.device_var.set("устройство найдено")
                        self._set_led("ok")
                    else:
                        self.device_var.set("не найдено")
                        self._set_led("bad")
                elif kind == "autodetect":
                    if payload:
                        self.port_var.set(payload)
                        self._probe_selected_port_async()
                        # Auto-connect after successful auto-detect
                        self._connect_selected_port_async()
                        self.status_var.set(f"Найдено устройство: {payload}")
                        self._log(f"Авто-поиск: найдено на {payload}")
                    else:
                        self.status_var.set("Устройство не найдено")
                        self._log("Авто-поиск: устройство не найдено")

                elif kind == "connected":
                    port = str(payload)
                    self._set_connected_ui(True, port)
                    # Wake the polling loop so the status bar shows real device state ASAP.
                    try:
                        self._poll_force.set()
                    except Exception:
                        pass

                elif kind == "disconnected":
                    self._set_connected_ui(False)
                    # _set_connected_ui already sets "Нет подключения".

                elif kind == "connect_error":
                    self._set_connected_ui(False)
                    self._log(f"Ошибка подключения: {payload}")
                    messagebox.showerror("Ошибка подключения", str(payload))

                elif kind == "device_state":
                    txt = str(payload)
                    self.device_state_var.set(txt)
                    # Keep polling bookkeeping in sync with UI updates
                    self._poll_last_text = txt
                    if txt and txt not in {"Нет подключения", "Подключено (нет данных)"}:
                        self._poll_last_good_text = txt
                elif kind == "log":
                    self._log(str(payload))
                elif kind == "progress":
                    try:
                        data = json.loads(payload)
                        done = int(data["done"])
                        total = int(data["total"])
                        line = data.get("line")
                        est = float(data.get("est") or 0.0)
                        pct = 0.0 if total <= 0 else min(100.0, (done / total) * 100.0)
                        self.progress_var.set(pct)
                        if isinstance(line, int):
                            self._highlight_source_line(line)
                        self.status_var.set(f"Выполнение: {done}/{total} | осталось ожиданий ~ {fmt_seconds(est)}")
                    except Exception:
                        pass
                elif kind == "done":
                    rc = 0
                    try:
                        obj = json.loads(payload) if payload else {}
                        if isinstance(obj, dict):
                            rc = int(obj.get("rc", 0))
                    except Exception:
                        rc = 0

                    self.progress_var.set(100.0)
                    if rc == 4:
                        self.status_var.set("Остановлено")
                    else:
                        self.status_var.set("Готово")
                    self._set_running_ui(False)
                    self._clear_highlight()

                    # Completed or stopped: clear persisted resume point (no longer relevant).
                    try:
                        self._clear_resume()
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
                    should_repeat = (
                        rc == 0
                        and bool(self.repeat_file_enabled.get())
                    )
                    if should_repeat:
                        try:
                            self.status_var.set("Повтор файла: перезапуск")
                            self._log("Повтор файла: запуск заново")
                        except Exception:
                            pass
                        # Do not reconnect in-between repeats to avoid port contention.
                        self._reconnect_after_run = False
                        try:
                            self._clear_resume()
                        except Exception:
                            pass
                        self.after(200, self._start)
                        continue
                    # Reconnect after run so that status polling continues in idle.
                    if self._reconnect_after_run:
                        self._reconnect_after_run = False
                        if not self._connected:
                            self._connect_selected_port_async()
                elif kind == "error":
                    self.status_var.set("Ошибка")
                    self._log(f"ERROR: {payload}")
                    messagebox.showerror("Ошибка", payload)
                    self._set_running_ui(False)
                    self._clear_highlight()
                    # Restore connection after an error as well.
                    if self._reconnect_after_run:
                        self._reconnect_after_run = False
                        if not self._connected:
                            self._connect_selected_port_async()
                else:
                    pass
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)



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
                self.msgq.put(("connected", port))
            except Exception as e:
                self.msgq.put(("connect_error", str(e)))

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
            self.msgq.put(("disconnected", ""))

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
                    self.msgq.put(("device_state", text))
                    self._poll_last_text = text
                continue

            with self._fg_lock:
                fg = self._fg

            if fg is None:
                text = "Нет подключения"
                if text != self._poll_last_text:
                    self.msgq.put(("device_state", text))
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
                        self.msgq.put(("log", f"Status poll error: {e}"))
                    text = self._poll_last_good_text or "Подключено (нет данных)"
            finally:
                try:
                    self._io_lock.release()
                except Exception:
                    pass

            if text != self._poll_last_text:
                self.msgq.put(("device_state", text))
                self._poll_last_text = text


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
