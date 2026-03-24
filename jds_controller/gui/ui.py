from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox


def build_ui(app, *, github_url: str, telegram_url: str) -> None:
    """Build all Tk widgets for the main window."""
    t = app.tr
    pad = {"padx": 8, "pady": 6}

    try:
        import tkinter.font as tkfont
        style = ttk.Style(app)
        big_font = tkfont.nametofont("TkDefaultFont").copy()
        big_font.configure(size=max(12, big_font.cget("size") + 4), weight="bold")
        style.configure("Big.TButton", font=big_font, padding=(18, 10))
    except Exception:
        pass

    menubar = tk.Menu(app)

    filemenu = tk.Menu(menubar, tearoff=False)
    filemenu.add_command(label=t("menu_open"), command=app._browse_open)
    filemenu.add_command(label=t("menu_save"), command=app._save)
    filemenu.add_command(label=t("menu_save_as"), command=app._save_as)
    filemenu.add_separator()
    filemenu.add_command(label=t("menu_new_template"), command=app._new_template)
    filemenu.add_separator()
    filemenu.add_command(label=t("menu_exit"), command=app._on_close)
    menubar.add_cascade(label=t("menu_file"), menu=filemenu)

    runmenu = tk.Menu(menubar, tearoff=False)
    runmenu.add_command(label=t("menu_start"), command=app._start)
    runmenu.add_command(label=t("menu_pause_resume"), command=app._toggle_pause)
    runmenu.add_command(label=t("menu_next_command"), command=app._next_command)
    runmenu.add_command(label=t("menu_stop"), command=app._stop)
    runmenu.add_separator()
    runmenu.add_checkbutton(label=t("menu_enable_outputs_on_start"), variable=app.enable_outputs_on_start)
    runmenu.add_checkbutton(label=t("menu_disable_outputs_on_finish"), variable=app.disable_outputs_on_finish)
    runmenu.add_checkbutton(label=t("menu_shutdown_pc_on_finish"), variable=app.shutdown_pc_on_finish)
    runmenu.add_separator()
    runmenu.add_command(label=t("menu_validate"), command=app._validate)
    menubar.add_cascade(label=t("menu_run"), menu=runmenu)

    langmenu = tk.Menu(menubar, tearoff=False)
    langmenu.add_radiobutton(label=t("lang_ru"), value="ru", variable=app.lang_var, command=lambda: app._change_language("ru"))
    langmenu.add_radiobutton(label=t("lang_en"), value="en", variable=app.lang_var, command=lambda: app._change_language("en"))
    menubar.add_cascade(label=t("menu_language"), menu=langmenu)

    helpmenu = tk.Menu(menubar, tearoff=False)
    helpmenu.add_command(label=t("menu_quick_help"), command=app._show_help)
    helpmenu.add_command(label=t("menu_github"), command=lambda: app._open_url(github_url))
    helpmenu.add_separator()
    helpmenu.add_command(label=t("menu_about"), command=app._about)
    menubar.add_cascade(label=t("menu_help"), menu=helpmenu)

    app.config(menu=menubar)
    app.bind("<F1>", lambda e: app._show_help())

    frm_top = ttk.LabelFrame(app, text=t("group_connection"))
    frm_top.pack(fill="x", **pad)

    ttk.Label(frm_top, text=t("label_port")).grid(row=0, column=0, sticky="e", **pad)
    app.port_combo = ttk.Combobox(frm_top, textvariable=app.port_var, width=65, state="readonly")
    app.port_combo.grid(row=0, column=1, sticky="we", **pad)
    app.port_combo.bind("<<ComboboxSelected>>", lambda e: app._probe_selected_port_async())

    ttk.Button(frm_top, text=t("btn_refresh"), command=app._refresh_ports).grid(row=0, column=2, **pad)
    ttk.Button(frm_top, text=t("btn_find_connect"), command=app._auto_detect).grid(row=0, column=3, **pad)
    app.btn_connect = ttk.Button(frm_top, text=t("btn_connect"), command=app._toggle_connection)
    app.btn_connect.grid(row=0, column=4, **pad)

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

    frm_ctrl = ttk.Frame(app)
    frm_ctrl.pack(fill="x", **pad)

    app.btn_start = ttk.Button(frm_ctrl, text=t("btn_start_big"), command=app._start, style="Big.TButton")
    app.btn_start.pack(side="left", padx=8)

    app.btn_pause = ttk.Button(frm_ctrl, text=t("btn_pause"), command=app._toggle_pause, state="disabled")
    app.btn_pause.pack(side="left", padx=8)

    app.btn_next = ttk.Button(frm_ctrl, text=t("btn_next_command"), command=app._next_command, state="disabled")
    app.btn_next.pack(side="left", padx=8)

    app.btn_stop = ttk.Button(frm_ctrl, text=t("btn_stop"), command=app._stop, state="disabled")
    app.btn_stop.pack(side="left", padx=8)

    ttk.Separator(frm_ctrl, orient="vertical").pack(side="left", fill="y", padx=10)

    ttk.Label(frm_ctrl, text=t("label_default_channel")).pack(side="left", padx=(4, 6))
    ttk.Combobox(frm_ctrl, textvariable=app.channel_var, state="readonly", values=["1+2", "1", "2"], width=8).pack(side="left")

    ttk.Separator(frm_ctrl, orient="vertical").pack(side="left", fill="y", padx=10)

    frm_waitcol = ttk.Frame(frm_ctrl)
    frm_waitcol.pack(side="left", padx=(6, 6), anchor="n")

    app.chk_wait_override = ttk.Checkbutton(frm_waitcol, text=t("label_fixed_wait"), variable=app.wait_override_enabled)
    app.chk_wait_override.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=(0, 2))

    app.ent_wait_override = ttk.Entry(frm_waitcol, textvariable=app.wait_override_seconds, width=8)
    app.ent_wait_override.grid(row=0, column=1, sticky="w", pady=(0, 2))

    app.chk_repeat_file = ttk.Checkbutton(frm_waitcol, text=t("label_repeat_file"), variable=app.repeat_file_enabled)
    app.chk_repeat_file.grid(row=1, column=0, columnspan=2, sticky="w")

    ttk.Button(frm_ctrl, text=t("menu_validate"), command=app._validate).pack(side="right", padx=8)

    frm_status = ttk.Frame(app)
    frm_status.pack(fill="x", **pad)

    app.pb = ttk.Progressbar(frm_status, mode="determinate", maximum=100.0, variable=app.progress_var)
    app.pb.pack(fill="x", expand=True, side="left", padx=(0, 10))

    ttk.Label(frm_status, textvariable=app.remaining_time_var, width=10, anchor="e").pack(side="right")

    frm_mid = ttk.PanedWindow(app, orient="horizontal")
    frm_mid.pack(fill="both", expand=True, **pad)

    frm_editor = ttk.LabelFrame(frm_mid, text=t("group_commands_file"))
    frm_mid.add(frm_editor, weight=3)

    app.editor = tk.Text(frm_editor, wrap="char", undo=True)
    app.editor.pack(side="left", fill="both", expand=True)

    vs = ttk.Scrollbar(frm_editor, orient="vertical", command=app.editor.yview)
    vs.pack(side="right", fill="y")
    app.editor.configure(yscrollcommand=vs.set)

    app.editor.tag_configure("current_line", background="#fff3c4")
    app.editor.bind("<<Modified>>", app._on_modified)

    app._build_editor_context_menu()
    app.editor.bind("<Button-3>", app._show_editor_context_menu, add=True)
    app.editor.bind("<Control-Button-1>", app._show_editor_context_menu, add=True)
    app.editor.bind("<Control-KeyPress>", app._on_editor_ctrl_shortcut, add=True)

    frm_log = ttk.LabelFrame(frm_mid, text=t("group_log"))
    frm_mid.add(frm_log, weight=2)

    vsl = ttk.Scrollbar(frm_log, orient="vertical")
    vsl.pack(side="right", fill="y")

    app.log = tk.Text(frm_log, height=10, wrap="word", state="disabled", yscrollcommand=vsl.set)
    app.log.pack(side="left", fill="both", expand=True)
    vsl.configure(command=app.log.yview)

    ttk.Separator(app, orient="horizontal").pack(fill="x")
    frm_statusbar = ttk.Frame(app)
    frm_statusbar.pack(fill="x", padx=6, pady=(2, 6))
    ttk.Label(frm_statusbar, textvariable=app.device_state_var, anchor="w").pack(fill="x")


def show_help(app) -> None:
    messagebox.showinfo(app.tr("help_title"), app.tr("help_text"))


def build_editor_context_menu(app) -> None:
    app._editor_menu = tk.Menu(app, tearoff=0)
    app._editor_menu.add_command(label=app.tr("ctx_undo"), command=app._editor_undo)
    app._editor_menu.add_command(label=app.tr("ctx_redo"), command=app._editor_redo)
    app._editor_menu.add_separator()
    app._editor_menu.add_command(label=app.tr("ctx_cut"), command=lambda: app.editor.event_generate("<<Cut>>"))
    app._editor_menu.add_command(label=app.tr("ctx_copy"), command=lambda: app.editor.event_generate("<<Copy>>"))
    app._editor_menu.add_command(label=app.tr("ctx_paste"), command=lambda: app.editor.event_generate("<<Paste>>"))
    app._editor_menu.add_separator()
    app._editor_menu.add_command(label=app.tr("ctx_select_all"), command=app._editor_select_all)


def on_editor_ctrl_shortcut(app, event):
    sym = (event.keysym or "").lower()
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

    app.editor.event_generate(action)
    return "break"
