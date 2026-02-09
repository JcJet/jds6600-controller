from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox


def build_ui(app, *, github_url: str, telegram_url: str) -> None:
    """Build all Tk widgets for the main window.

    Extracted from the previously monolithic `run_gui.py` to keep layout-related
    code separate from execution / device logic.
    """
    pad = {"padx": 8, "pady": 6}

    # style
    try:
        import tkinter.font as tkfont
        style = ttk.Style(app)
        big_font = tkfont.nametofont("TkDefaultFont").copy()
        big_font.configure(size=max(12, big_font.cget("size") + 4), weight="bold")
        style.configure("Big.TButton", font=big_font, padding=(18, 10))
    except Exception:
        pass

    # menu
    menubar = tk.Menu(app)

    filemenu = tk.Menu(menubar, tearoff=False)
    filemenu.add_command(label="Открыть…", command=app._browse_open)
    filemenu.add_command(label="Сохранить", command=app._save)
    filemenu.add_command(label="Сохранить как…", command=app._save_as)
    filemenu.add_separator()
    filemenu.add_command(label="Новый шаблон", command=app._new_template)
    filemenu.add_separator()
    filemenu.add_command(label="Выход", command=app._on_close)
    menubar.add_cascade(label="Файл", menu=filemenu)

    runmenu = tk.Menu(menubar, tearoff=False)
    runmenu.add_command(label="Старт", command=app._start)
    runmenu.add_command(label="Пауза/Продолжить", command=app._toggle_pause)
    runmenu.add_command(label="Следующая команда (пропустить wait)", command=app._next_command)
    runmenu.add_command(label="Стоп", command=app._stop)
    runmenu.add_separator()
    runmenu.add_command(label="Проверить CSV", command=app._validate)
    menubar.add_cascade(label="Выполнение", menu=runmenu)

    helpmenu = tk.Menu(menubar, tearoff=False)
    helpmenu.add_command(label="Краткая помощь\tF1", command=app._show_help)
    helpmenu.add_command(label="GitHub…", command=lambda: app._open_url(github_url))
    helpmenu.add_separator()
    helpmenu.add_command(label="О программе", command=app._about)
    menubar.add_cascade(label="Справка", menu=helpmenu)

    app.config(menu=menubar)
    app.bind("<F1>", lambda e: app._show_help())

    # top controls (ports)
    frm_top = ttk.LabelFrame(app, text="Подключение")
    frm_top.pack(fill="x", **pad)

    ttk.Label(frm_top, text="Порт:").grid(row=0, column=0, sticky="e", **pad)
    app.port_combo = ttk.Combobox(frm_top, textvariable=app.port_var, width=65, state="readonly")
    app.port_combo.grid(row=0, column=1, sticky="we", **pad)
    app.port_combo.bind("<<ComboboxSelected>>", lambda e: app._probe_selected_port_async())

    ttk.Button(frm_top, text="Обновить", command=app._refresh_ports).grid(row=0, column=2, **pad)
    ttk.Button(frm_top, text="Найти и подключиться", command=app._auto_detect).grid(row=0, column=3, **pad)
    app.btn_connect = ttk.Button(frm_top, text="Подключиться", command=app._toggle_connection)
    app.btn_connect.grid(row=0, column=4, **pad)

    # Fixed-size device status area (prevents combobox width jumps when text changes)
    app._devinfo = ttk.Frame(frm_top)
    app._devinfo.grid(row=0, column=5, sticky="w", padx=(6, 0), pady=6)
    try:
        app._devinfo.configure(width=240)
        app._devinfo.grid_propagate(False)
    except Exception:
        pass

    app.device_led = tk.Canvas(app._devinfo, width=14, height=14, highlightthickness=0)
    app.device_led.pack(side="left")
    app._led_item = app.device_led.create_oval(2, 2, 12, 12, fill="#999999", outline="")
    app.device_label = ttk.Label(app._devinfo, textvariable=app.device_var, width=22, anchor="w")
    app.device_label.pack(side="left", padx=(8, 0))

    frm_top.columnconfigure(1, weight=1)
    frm_top.columnconfigure(5, weight=0, minsize=240)

    # controls (start/stop etc)
    frm_ctrl = ttk.Frame(app)
    frm_ctrl.pack(fill="x", **pad)

    app.btn_start = ttk.Button(frm_ctrl, text="СТАРТ", command=app._start, style="Big.TButton")
    app.btn_start.pack(side="left", padx=8)

    app.btn_pause = ttk.Button(frm_ctrl, text="Пауза", command=app._toggle_pause, state="disabled")
    app.btn_pause.pack(side="left", padx=8)

    app.btn_next = ttk.Button(frm_ctrl, text="Следующая команда", command=app._next_command, state="disabled")
    app.btn_next.pack(side="left", padx=8)

    app.btn_stop = ttk.Button(frm_ctrl, text="Стоп", command=app._stop, state="disabled")
    app.btn_stop.pack(side="left", padx=8)

    ttk.Separator(frm_ctrl, orient="vertical").pack(side="left", fill="y", padx=10)

    ttk.Label(frm_ctrl, text="Канал (по умолчанию):").pack(side="left", padx=(4, 6))
    ttk.Combobox(frm_ctrl, textvariable=app.channel_var, state="readonly",
                 values=["1+2", "1", "2"], width=8).pack(side="left")

    ttk.Separator(frm_ctrl, orient="vertical").pack(side="left", fill="y", padx=10)

    # wait override + repeat file (stacked in one column)
    frm_waitcol = ttk.Frame(frm_ctrl)
    frm_waitcol.pack(side="left", padx=(6, 6), anchor="n")

    app.chk_wait_override = ttk.Checkbutton(
        frm_waitcol,
        text="Фиксированный wait (сек):",
        variable=app.wait_override_enabled
    )
    app.chk_wait_override.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 2))

    app.ent_wait_override = ttk.Entry(frm_waitcol, textvariable=app.wait_override_seconds, width=8)
    app.ent_wait_override.grid(row=0, column=1, sticky="w", pady=(0, 2))

    app.chk_repeat_file = ttk.Checkbutton(frm_waitcol, text="Повтор файла", variable=app.repeat_file_enabled)
    app.chk_repeat_file.grid(row=1, column=0, columnspan=2, sticky="w")

    ttk.Button(frm_ctrl, text="Проверить CSV", command=app._validate).pack(side="right", padx=8)

    # progress + status
    frm_status = ttk.Frame(app)
    frm_status.pack(fill="x", **pad)

    app.pb = ttk.Progressbar(frm_status, mode="determinate", maximum=100.0, variable=app.progress_var)
    app.pb.pack(fill="x", expand=True, side="left", padx=(0, 10))

    # Fixed-width label so progress bar doesn't shift as text changes.
    # Shows only the estimated remaining time for the current run.
    ttk.Label(frm_status, textvariable=app.remaining_time_var, width=10, anchor="e").pack(side="right")

    # editor + log split
    frm_mid = ttk.PanedWindow(app, orient="horizontal")
    frm_mid.pack(fill="both", expand=True, **pad)

    # editor frame
    frm_editor = ttk.LabelFrame(frm_mid, text="Файл команд (редактируемый)")
    frm_mid.add(frm_editor, weight=3)

    app.editor = tk.Text(frm_editor, wrap="char", undo=True)
    app.editor.pack(side="left", fill="both", expand=True)

    vs = ttk.Scrollbar(frm_editor, orient="vertical", command=app.editor.yview)
    vs.pack(side="right", fill="y")
    app.editor.configure(yscrollcommand=vs.set)


    app.editor.tag_configure("current_line", background="#fff3c4")
    app.editor.bind("<<Modified>>", app._on_modified)

    # editor context menu + reliable Ctrl shortcuts (works even with RU layout on Windows)
    app._build_editor_context_menu()
    app.editor.bind("<Button-3>", app._show_editor_context_menu, add=True)
    app.editor.bind("<Control-Button-1>", app._show_editor_context_menu, add=True)  # macOS
    app.editor.bind("<Control-KeyPress>", app._on_editor_ctrl_shortcut, add=True)

    # log frame
    frm_log = ttk.LabelFrame(frm_mid, text="Лог выполнения")
    frm_mid.add(frm_log, weight=2)

    # NOTE: pack order matters.
    # If you pack the Text first with fill="both", it can consume all space and
    # the scrollbar gets squeezed into a tiny widget (looks like a weird mini-scrollbar
    # in the corner). Pack the scrollbar first, then the Text.
    vsl = ttk.Scrollbar(frm_log, orient="vertical")
    vsl.pack(side="right", fill="y")

    app.log = tk.Text(
        frm_log,
        height=10,
        wrap="word",
        state="disabled",
        yscrollcommand=vsl.set,
    )
    app.log.pack(side="left", fill="both", expand=True)
    vsl.configure(command=app.log.yview)


    # Bottom status bar (generator state)
    ttk.Separator(app, orient="horizontal").pack(fill="x")
    frm_statusbar = ttk.Frame(app)
    frm_statusbar.pack(fill="x", padx=6, pady=(2, 6))
    ttk.Label(frm_statusbar, textvariable=app.device_state_var, anchor="w").pack(fill="x")



def show_help(app) -> None:
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


def build_editor_context_menu(app) -> None:
    # Context menu for the command editor (right click)
    app._editor_menu = tk.Menu(app, tearoff=0)
    app._editor_menu.add_command(label="Отменить", command=app._editor_undo)
    app._editor_menu.add_command(label="Повторить", command=app._editor_redo)
    app._editor_menu.add_separator()
    app._editor_menu.add_command(label="Вырезать", command=lambda: app.editor.event_generate("<<Cut>>"))
    app._editor_menu.add_command(label="Копировать", command=lambda: app.editor.event_generate("<<Copy>>"))
    app._editor_menu.add_command(label="Вставить", command=lambda: app.editor.event_generate("<<Paste>>"))
    app._editor_menu.add_separator()
    app._editor_menu.add_command(label="Выделить всё", command=app._editor_select_all)


def on_editor_ctrl_shortcut(app, event):
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
        app._editor_select_all()
        return "break"
    if action == "<<Undo>>":
        app._editor_undo()
        return "break"
    if action == "<<Redo>>":
        app._editor_redo()
        return "break"

    # Copy/Cut/Paste
    app.editor.event_generate(action)
    return "break"
