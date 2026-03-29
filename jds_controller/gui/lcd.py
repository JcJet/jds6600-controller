from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont

LCD_BG_OUTER = "#1e1f1e"
LCD_BG_BEZEL = "#35372d"
LCD_BG_SCREEN_1 = "#b2b674"
LCD_BG_SCREEN_2 = "#b8c077"
LCD_FG = "#15250a"
LCD_SHADOW = "#77804c"
LCD_STRIP_BG = "#6f7848"
LCD_STRIP_FILL = "#245d15"


class LcdPanel(tk.Frame):
    """Lightweight LCD-like current-command panel.

    This renderer intentionally uses only Tk Canvas + system fonts. It stays
    responsive on slower machines, including Windows 7, while still keeping the
    same information density and overall instrument-like appearance.
    """

    def __init__(self, master, *, width: int = 900, height: int = 110, **kwargs):
        super().__init__(master, bd=0, highlightthickness=0, **kwargs)
        self.configure(bg="#2b2b2b")
        self._width = width
        self._height = height
        self._primary = "NO ACTIVE COMMAND"
        self._secondary = "PRESS START TO BEGIN"
        self._time_text = "--:--:--"
        self._step_text = ""
        self._progress = 0.0
        self._last_state_signature = None
        self._tk_font_family = None
        self._canvas = tk.Canvas(self, bd=0, highlightthickness=0, bg="#2b2b2b")
        self._canvas.pack(fill="both", expand=True)
        self.bind("<Configure>", self._on_resize)
        self._canvas.bind("<Configure>", self._on_resize)
        self.after_idle(self.redraw)

    def _resolve_tk_font_family(self) -> str:
        if self._tk_font_family:
            return self._tk_font_family
        try:
            fams = {str(name) for name in tkfont.families(self)}
        except Exception:
            fams = set()
        candidates = [
            "Consolas",
            "DejaVu Sans Mono",
            "Liberation Mono",
            "Ubuntu Mono",
            "Courier New",
            "Lucida Console",
            "Monaco",
            "Menlo",
            "TkFixedFont",
            "TkDefaultFont",
        ]
        for name in candidates:
            if name in fams:
                self._tk_font_family = name
                return name
        self._tk_font_family = "TkDefaultFont"
        return self._tk_font_family

    def _state_signature(self) -> tuple:
        progress_px = 0
        try:
            fill_width = max(1, int(self._width) - 42)
            progress_px = int(round((max(0.0, min(100.0, float(self._progress))) / 100.0) * fill_width))
        except Exception:
            try:
                progress_px = int(round(float(self._progress)))
            except Exception:
                progress_px = 0
        return (
            int(self._width or 0),
            int(self._height or 0),
            self._primary,
            self._secondary,
            self._time_text,
            self._step_text,
            progress_px,
        )

    def set_state(self, *, primary: str, secondary: str, time_text: str, step_text: str, progress: float) -> None:
        self._primary = (primary or "").upper()
        self._secondary = (secondary or "").upper()
        self._time_text = (time_text or "").upper()
        self._step_text = (step_text or "").upper()
        try:
            self._progress = max(0.0, min(100.0, float(progress)))
        except Exception:
            self._progress = 0.0
        sig = self._state_signature()
        if sig == self._last_state_signature:
            return
        self._last_state_signature = sig
        self.redraw()

    def _on_resize(self, _event=None) -> None:
        try:
            w = max(240, int(self.winfo_width()))
            h = max(84, int(self.winfo_height()))
        except Exception:
            return
        if w != self._width or h != self._height:
            self._width, self._height = w, h
            self._last_state_signature = None
            self.redraw()

    def _draw_bezel(self, w: int, h: int) -> tuple[int, int, int, int]:
        c = self._canvas
        c.create_rectangle(0, 0, w, h, fill=LCD_BG_OUTER, outline="#0b0b0b", width=0)
        c.create_rectangle(1, 1, w - 1, h - 1, outline="#4b4b4b", width=2)
        c.create_rectangle(6, 6, w - 6, h - 6, fill=LCD_BG_BEZEL, outline="#0f100f", width=2)
        c.create_rectangle(12, 12, w - 12, h - 12, fill=LCD_BG_SCREEN_1, outline="#657044", width=1)
        c.create_rectangle(15, 15, w - 15, h - 15, fill=LCD_BG_SCREEN_2, outline="#c7d08b", width=1)
        c.create_line(18, 20, w - 18, 20, fill="#dce39c")
        c.create_line(18, 22, w - 18, 22, fill="#c7cf88")
        for yy in range(20, h - 22, 3):
            col = "#a7b06a" if ((yy // 3) % 2 == 0) else "#aeb775"
            c.create_line(20, yy, w - 20, yy, fill=col)
        sr = 2
        for sx, sy in ((8, 8), (w - 8, 8), (8, h - 8), (w - 8, h - 8)):
            c.create_oval(sx - sr, sy - sr, sx + sr, sy + sr, fill="#565656", outline="#222222")
        return 26, 18, w - 26, h - 26

    def _draw_simple_text_layer(self, w: int, h: int, *, left: int, right: int, top: int) -> None:
        c = self._canvas
        family = self._resolve_tk_font_family()

        strip_h = max(10, int(h * 0.10))
        strip_margin = 20
        text_bottom = h - strip_margin - strip_h - 6
        available_h = max(28, text_bottom - top)

        main_size = max(15, int(available_h * 0.44))
        time_size = max(15, int(available_h * 0.45))
        sub_size = max(9, int(available_h * 0.18))
        main_font = tkfont.Font(self, family=family, size=main_size, weight="bold")
        time_font = tkfont.Font(self, family=family, size=time_size, weight="bold")
        sub_font = tkfont.Font(self, family=family, size=sub_size, weight="bold")

        prim = self._primary[:30]
        tim = self._time_text[:10]
        sec = self._secondary[:50]
        step = self._step_text[:16]

        main_ls = max(1, int(main_font.metrics("linespace")))
        sub_ls = max(1, int(sub_font.metrics("linespace")))
        line1_y = top + 5
        preferred_line2_y = line1_y + main_ls - 2
        max_line2_y = max(line1_y + 2, text_bottom - sub_ls)
        line2_y = min(preferred_line2_y, max_line2_y)

        def draw_shadowed_text(x: int, y: int, text: str, font, *, anchor: str = "nw") -> None:
            if not text:
                return
            c.create_text(x + 2, y + 2, text=text, anchor=anchor, fill=LCD_SHADOW, font=font)
            c.create_text(x, y, text=text, anchor=anchor, fill=LCD_FG, font=font)

        draw_shadowed_text(left, line1_y, prim, main_font, anchor="nw")
        draw_shadowed_text(right, line1_y, tim, time_font, anchor="ne")
        draw_shadowed_text(left, line2_y, sec, sub_font, anchor="nw")
        draw_shadowed_text(right, line2_y, step, sub_font, anchor="ne")

    def _draw_progress_strip(self, w: int, h: int) -> None:
        c = self._canvas
        strip_margin = 20
        strip_h = max(10, int(h * 0.10))
        bx0, by0 = strip_margin, h - strip_margin - strip_h
        bx1, by1 = w - strip_margin, h - strip_margin
        c.create_rectangle(bx0, by0, bx1, by1, fill=LCD_STRIP_BG, outline="#263014", width=1)
        c.create_line(bx0 + 1, by0 + 1, bx1 - 1, by0 + 1, fill="#9faa6e")
        fill_w = int((bx1 - bx0 - 2) * (self._progress / 100.0))
        if fill_w > 0:
            c.create_rectangle(bx0 + 1, by0 + 1, bx0 + 1 + fill_w, by1 - 1, fill=LCD_STRIP_FILL, outline="#16390d", width=1)
            for yy in range(by0 + 2, by1 - 1, 2):
                c.create_line(bx0 + 2, yy, bx0 + 1 + fill_w - 1, yy, fill="#2f741d")

    def redraw(self) -> None:
        w = max(240, int(self._width or 900))
        h = max(84, int(self._height or 110))
        self._last_state_signature = self._state_signature()
        c = self._canvas
        c.configure(width=w, height=h)
        c.delete("all")

        left, top, right, _bottom = self._draw_bezel(w, h)
        self._draw_simple_text_layer(w, h, left=left, right=right, top=top)
        self._draw_progress_strip(w, h)
