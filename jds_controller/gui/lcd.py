from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import tkinter as tk
import tkinter.font as tkfont

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except Exception:  # pragma: no cover - optional runtime dependency
    Image = ImageDraw = ImageFont = ImageTk = None  # type: ignore


LCD_BG_OUTER = "#1e1f1e"
LCD_BG_BEZEL = "#35372d"
LCD_BG_SCREEN_1 = "#b2b674"
LCD_BG_SCREEN_2 = "#b8c077"
LCD_FG = "#15250a"
LCD_SHADOW = "#77804c"
LCD_STRIP_BG = "#6f7848"
LCD_STRIP_FILL = "#245d15"


def _assets_root() -> Path:
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(getattr(sys, "_MEIPASS")) / "assets"
    except Exception:
        pass
    try:
        return Path(__file__).resolve().parents[2] / "assets"
    except Exception:
        return Path("assets")


def _font_path() -> Optional[Path]:
    p = _assets_root() / "fonts" / "DSEG14Classic-Regular.ttf"
    return p if p.exists() else None


class LcdPanel(tk.Frame):
    """LCD-like current-command panel.

    Two rendering modes are supported:
    - simple (default): lightweight Tk canvas text using a broadly available
      monospace/system font. Safe for Windows 7 and slow machines.
    - heavy: the old Pillow+TTF overlay renderer with the DSEG font/effects.

    The panel automatically falls back to the lightweight path if heavy mode is
    requested but Pillow or the bundled DSEG font is unavailable.
    """

    def __init__(
        self,
        master,
        *,
        width: int = 900,
        height: int = 110,
        heavy_rendering: bool = False,
        **kwargs,
    ):
        super().__init__(master, bd=0, highlightthickness=0, **kwargs)
        self.configure(bg="#2b2b2b")
        self._width = width
        self._height = height
        self._primary = "NO ACTIVE COMMAND"
        self._secondary = "PRESS START TO BEGIN"
        self._time_text = "--:--:--"
        self._step_text = ""
        self._progress = 0.0
        self._font_path = _font_path()
        self._overlay = None
        self._last_state_signature = None
        self._heavy_rendering = bool(heavy_rendering)
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
            bool(self._heavy_rendering),
        )

    def set_render_mode(self, heavy_rendering: bool) -> None:
        heavy_rendering = bool(heavy_rendering)
        if heavy_rendering == self._heavy_rendering:
            return
        self._heavy_rendering = heavy_rendering
        self._last_state_signature = None
        self.redraw()

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

    def _load_font(self, size: int):
        if ImageFont is None or self._font_path is None:
            return None
        try:
            return ImageFont.truetype(str(self._font_path), size=size)
        except Exception:
            return None

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

    def _render_true_font_overlay(self, w: int, h: int, *, left: int, right: int, top: int, fg: str, shadow: str) -> bool:
        if not self._heavy_rendering:
            return False
        if Image is None or ImageDraw is None or ImageTk is None or self._font_path is None:
            return False

        scale = 2
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", getattr(Image, "LANCZOS", 1))
        img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        strip_h = max(10, int(h * 0.10))
        strip_margin = 20
        text_bottom = h - strip_margin - strip_h - 6
        available_h = max(28, text_bottom - top)

        main_size = max(18, int(available_h * 0.44 * scale))
        time_size = max(18, int(available_h * 0.45 * scale))
        sub_size = max(10, int(available_h * 0.18 * scale))

        font_main = self._load_font(main_size)
        font_time = self._load_font(time_size)
        font_sub = self._load_font(sub_size)
        if font_main is None or font_time is None or font_sub is None:
            return False

        prim = self._primary[:28]
        tim = self._time_text[:10]
        sec = self._secondary[:44]
        step = self._step_text[:16]

        left_s = left * scale
        right_s = right * scale

        def text_size(text: str, font) -> tuple[int, int, int]:
            bbox = d.textbbox((0, 0), text or " ", font=font)
            if not bbox:
                return 0, 0, 0
            return max(1, bbox[2] - bbox[0]), max(1, bbox[3] - bbox[1]), bbox[1]

        main_w, main_h, main_top = text_size(prim, font_main)
        _time_w, _time_h, time_top = text_size(tim, font_time)
        _sub_w, sub_h, sub_top = text_size(sec, font_sub)

        line1_y = top + 5
        preferred_line2_y = line1_y + int(main_h / scale) - 2
        max_line2_y = max(line1_y + 2, text_bottom - int(sub_h / scale))
        line2_y = min(preferred_line2_y, max_line2_y)

        line1_y_s = line1_y * scale
        line2_y_s = line2_y * scale

        def draw_text(pos_x: int, pos_y: int, text: str, font, *, right_align: bool = False, shadow_dx: int = 5, shadow_dy: int = 4, bbox_top: int = 0) -> None:
            if not text:
                return
            bbox = d.textbbox((0, 0), text, font=font)
            if not bbox:
                return
            tw = max(1, bbox[2] - bbox[0])
            x = pos_x - tw if right_align else pos_x
            y = pos_y - bbox_top
            d.text((x + shadow_dx, y + shadow_dy), text, font=font, fill=shadow)
            d.text((x, y), text, font=font, fill=fg)

        draw_text(left_s, line1_y_s, prim, font_main, bbox_top=main_top)
        draw_text(right_s, line1_y_s, tim, font_time, right_align=True, bbox_top=time_top)
        draw_text(left_s, line2_y_s, sec, font_sub, shadow_dx=4, shadow_dy=3, bbox_top=sub_top)
        draw_text(right_s, line2_y_s, step, font_sub, right_align=True, shadow_dx=4, shadow_dy=3, bbox_top=sub_top)

        img = img.resize((w, h), resample)
        self._overlay = ImageTk.PhotoImage(img)
        self._canvas.create_image(0, 0, image=self._overlay, anchor="nw")
        return True

    def _draw_simple_text_layer(self, w: int, h: int, *, left: int, right: int, top: int) -> None:
        c = self._canvas
        family = self._resolve_tk_font_family()

        # Reserve explicit vertical space for the bottom progress strip so the
        # secondary line never collides with it on smaller windows.
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
        self._overlay = None

        left, top, right, _bottom = self._draw_bezel(w, h)
        used_true_font = self._render_true_font_overlay(w, h, left=left, right=right, top=top, fg=LCD_FG, shadow=LCD_SHADOW)
        if not used_true_font:
            self._draw_simple_text_layer(w, h, left=left, right=right, top=top)
        self._draw_progress_strip(w, h)
