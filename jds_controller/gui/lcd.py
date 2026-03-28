from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Sequence

import tkinter as tk

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except Exception:  # pragma: no cover - optional runtime dependency
    Image = ImageDraw = ImageFont = ImageTk = None  # type: ignore


# Fallback segmented glyphs for environments where Pillow/TTF rendering is not
# available. This remains as a safety net, but the preferred path is now the
# real DSEG font rendered into a canvas overlay.
_SEGMENTS: Dict[str, Sequence[str]] = {
    " ": (),
    "-": ("g1", "g2"),
    "_": ("d",),
    "/": ("j", "k"),
    "\\": ("h", "m"),
    ".": ("dp",),
    ":": ("cp1", "cp2"),
    "0": ("a", "b", "c", "d", "e", "f"),
    "1": ("b", "c"),
    "2": ("a", "b", "g1", "g2", "e", "d"),
    "3": ("a", "b", "g1", "g2", "c", "d"),
    "4": ("f", "g1", "g2", "b", "c"),
    "5": ("a", "f", "g1", "g2", "c", "d"),
    "6": ("a", "f", "g1", "g2", "c", "d", "e"),
    "7": ("a", "b", "c"),
    "8": ("a", "b", "c", "d", "e", "f", "g1", "g2"),
    "9": ("a", "b", "c", "d", "f", "g1", "g2"),
    "A": ("a", "b", "c", "e", "f", "g1", "g2"),
    "B": ("c", "d", "e", "f", "g1", "g2", "i", "l"),
    "C": ("a", "d", "e", "f"),
    "D": ("b", "c", "d", "e", "g1", "g2", "i", "l"),
    "E": ("a", "d", "e", "f", "g1", "g2"),
    "F": ("a", "e", "f", "g1", "g2"),
    "G": ("a", "c", "d", "e", "f", "g2"),
    "H": ("b", "c", "e", "f", "g1", "g2"),
    "I": ("a", "d", "i", "l"),
    "J": ("b", "c", "d", "e"),
    "K": ("e", "f", "g1", "k", "m"),
    "L": ("d", "e", "f"),
    "M": ("b", "c", "e", "f", "h", "j"),
    "N": ("b", "c", "e", "f", "h", "m"),
    "O": ("a", "b", "c", "d", "e", "f"),
    "P": ("a", "b", "e", "f", "g1", "g2"),
    "Q": ("a", "b", "c", "d", "e", "f", "m"),
    "R": ("a", "b", "e", "f", "g1", "g2", "m"),
    "S": ("a", "c", "d", "f", "g1", "g2"),
    "T": ("a", "i", "l"),
    "U": ("b", "c", "d", "e", "f"),
    "V": ("e", "f", "k", "m"),
    "W": ("b", "c", "e", "f", "k", "m"),
    "X": ("h", "j", "k", "m"),
    "Y": ("h", "j", "l"),
    "Z": ("a", "d", "j", "k"),
    "+": ("g1", "g2", "i", "l"),
}


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


@dataclass
class _Geom:
    w: float
    h: float
    t: float

    @property
    def mid_x(self) -> float:
        return self.w / 2.0

    @property
    def mid_y(self) -> float:
        return self.h / 2.0


class LcdPanel(tk.Frame):
    """A small LCD-like display for the current command state.

    Preferred rendering path: Tk Canvas for the bezel/background plus the real
    DSEG font rendered into a transparent image overlay. If Pillow or the font
    cannot be loaded, a built-in segmented fallback renderer is used instead.
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
        self._font_path = _font_path()
        self._overlay = None
        self._canvas = tk.Canvas(self, bd=0, highlightthickness=0, bg="#2b2b2b")
        self._canvas.pack(fill="both", expand=True)
        self.bind("<Configure>", self._on_resize)
        self._canvas.bind("<Configure>", self._on_resize)
        self.after_idle(self.redraw)

    def set_state(self, *, primary: str, secondary: str, time_text: str, step_text: str, progress: float) -> None:
        self._primary = (primary or "").upper()
        self._secondary = (secondary or "").upper()
        self._time_text = (time_text or "").upper()
        self._step_text = (step_text or "").upper()
        try:
            self._progress = max(0.0, min(100.0, float(progress)))
        except Exception:
            self._progress = 0.0
        self.redraw()

    def _on_resize(self, _event=None) -> None:
        try:
            w = max(240, int(self.winfo_width()))
            h = max(84, int(self.winfo_height()))
        except Exception:
            return
        if w != self._width or h != self._height:
            self._width, self._height = w, h
            self.redraw()

    def _char_width(self, size: float) -> float:
        return size * 0.68

    def _char_gap(self, size: float) -> float:
        return max(2.0, size * 0.16)

    def _text_width(self, text: str, size: float) -> float:
        if not text:
            return 0.0
        cw = self._char_width(size)
        gap = self._char_gap(size)
        return len(text) * cw + max(0, len(text) - 1) * gap

    def _segment_polys(self, x: float, y: float, geom: _Geom) -> Dict[str, Sequence[float]]:
        w, h, t = geom.w, geom.h, geom.t
        mx, my = geom.mid_x, geom.mid_y
        s = t * 0.58
        return {
            "a": (x + t, y, x + w - t, y, x + w - t - s, y + t, x + t + s, y + t),
            "d": (x + t, y + h, x + w - t, y + h, x + w - t - s, y + h - t, x + t + s, y + h - t),
            "g1": (x + t + s, y + my - t * 0.62, x + mx - s * 0.55, y + my - t * 0.62, x + mx - s * 0.1, y + my, x + t + s * 1.25, y + my),
            "g2": (x + mx + s * 0.55, y + my - t * 0.62, x + w - t - s, y + my - t * 0.62, x + w - t - s * 1.25, y + my, x + mx + s * 0.1, y + my),
            "f": (x, y + t, x + t, y + t + s, x + t, y + my - t * 0.84, x, y + my - s * 0.48),
            "b": (x + w, y + t, x + w - t, y + t + s, x + w - t, y + my - t * 0.84, x + w, y + my - s * 0.48),
            "e": (x, y + my + s * 0.4, x + t, y + my + t * 0.84, x + t, y + h - t - s, x, y + h - t),
            "c": (x + w, y + my + s * 0.4, x + w - t, y + my + t * 0.84, x + w - t, y + h - t - s, x + w, y + h - t),
            "i": (x + mx - t * 0.56, y + t * 0.95, x + mx + t * 0.56, y + t * 0.95, x + mx + t * 0.28, y + my - t * 0.98, x + mx - t * 0.28, y + my - t * 0.98),
            "l": (x + mx - t * 0.28, y + my + t * 0.18, x + mx + t * 0.28, y + my + t * 0.18, x + mx + t * 0.56, y + h - t * 0.95, x + mx - t * 0.56, y + h - t * 0.95),
            "h": (x + t * 0.4, y + t * 1.05, x + t * 1.35, y + t * 0.5, x + mx - t * 0.1, y + my - t * 0.2, x + mx - t * 0.95, y + my + t * 0.25),
            "j": (x + w - t * 0.4, y + t * 1.05, x + w - t * 1.35, y + t * 0.5, x + mx + t * 0.1, y + my - t * 0.2, x + mx + t * 0.95, y + my + t * 0.25),
            "k": (x + t * 0.55, y + h - t * 1.05, x + t * 1.45, y + h - t * 0.5, x + mx - t * 0.15, y + my + t * 0.1, x + mx - t * 1.0, y + my - t * 0.35),
            "m": (x + w - t * 0.55, y + h - t * 1.05, x + w - t * 1.45, y + h - t * 0.5, x + mx + t * 0.15, y + my + t * 0.1, x + mx + t * 1.0, y + my - t * 0.35),
        }

    def _draw_char(self, x: float, y: float, ch: str, size: float, *, fill: str, shadow: str) -> float:
        cw = self._char_width(size)
        chh = size
        t = max(1.0, size * 0.12)
        geom = _Geom(cw, chh, t)
        segs = _SEGMENTS.get(ch, _SEGMENTS.get(" ", ()))

        if ch == ":":
            r = max(1.5, t * 0.75)
            cx = x + cw * 0.5
            cy1 = y + chh * 0.34
            cy2 = y + chh * 0.69
            self._canvas.create_oval(cx - r, cy1 - r, cx + r, cy1 + r, fill=shadow, outline="")
            self._canvas.create_oval(cx - r, cy2 - r, cx + r, cy2 + r, fill=shadow, outline="")
            r2 = max(1.1, r - 1.0)
            self._canvas.create_oval(cx - r2, cy1 - r2, cx + r2, cy1 + r2, fill=fill, outline="")
            self._canvas.create_oval(cx - r2, cy2 - r2, cx + r2, cy2 + r2, fill=fill, outline="")
            return cw
        if ch == ".":
            r = max(1.5, t * 0.8)
            cx = x + cw * 0.78
            cy = y + chh * 0.88
            self._canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=shadow, outline="")
            r2 = max(1.1, r - 1.0)
            self._canvas.create_oval(cx - r2, cy - r2, cx + r2, cy + r2, fill=fill, outline="")
            return cw

        polys = self._segment_polys(x, y, geom)
        for seg in segs:
            pts = polys.get(seg)
            if not pts:
                continue
            shadow_pts = []
            for idx, val in enumerate(pts):
                shadow_pts.append(val + (1.4 if idx % 2 == 0 else 1.2))
            self._canvas.create_polygon(*shadow_pts, fill=shadow, outline="")
            self._canvas.create_polygon(*pts, fill=fill, outline="")
        return cw

    def _draw_seg_text(self, x: float, y: float, text: str, size: float, *, fill: str, shadow: str, anchor: str = "nw") -> None:
        text = (text or "").upper()
        cw = self._char_width(size)
        gap = self._char_gap(size)
        total_w = self._text_width(text, size)
        if anchor == "ne":
            x -= total_w
        elif anchor == "n":
            x -= total_w / 2.0
        for ch in text:
            step = self._draw_char(x, y, ch, size, fill=fill, shadow=shadow)
            x += step + gap

    def _load_font(self, size: int):
        if ImageFont is None or self._font_path is None:
            return None
        try:
            return ImageFont.truetype(str(self._font_path), size=size)
        except Exception:
            return None

    def _render_true_font_overlay(self, w: int, h: int, *, left: int, right: int, top: int, fg: str, shadow: str) -> bool:
        if Image is None or ImageDraw is None or ImageTk is None or self._font_path is None:
            return False

        # For DSEG this looks closer to the browser/font-preview sample than
        # heavy mask compositing: render directly with the real TTF, keep the
        # old pronounced shadow, and only supersample lightly for cleaner edges.
        scale = 2
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", getattr(Image, "LANCZOS", 1))
        img = Image.new("RGBA", (w * scale, h * scale), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        main_size = max(18, int(h * 0.42 * scale))
        time_size = max(18, int(h * 0.43 * scale))
        sub_size = max(10, int(h * 0.18 * scale))

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
        top_s = int((top - 2) * scale)
        sub_y = int((top + max(34, int(h * 0.44))) * scale)

        def draw_text(pos_x: int, pos_y: int, text: str, font, *, right_align: bool = False, shadow_dx: int = 5, shadow_dy: int = 4) -> None:
            if not text:
                return
            bbox = d.textbbox((0, 0), text, font=font)
            if not bbox:
                return
            tw = max(1, bbox[2] - bbox[0])
            x = pos_x - tw if right_align else pos_x
            y = pos_y - bbox[1]
            d.text((x + shadow_dx, y + shadow_dy), text, font=font, fill=shadow)
            d.text((x, y), text, font=font, fill=fg)

        draw_text(left_s, top_s, prim, font_main)
        draw_text(right_s, top_s, tim, font_time, right_align=True)
        draw_text(left_s, sub_y, sec, font_sub, shadow_dx=4, shadow_dy=3)
        draw_text(right_s, sub_y, step, font_sub, right_align=True, shadow_dx=4, shadow_dy=3)

        img = img.resize((w, h), resample)
        self._overlay = ImageTk.PhotoImage(img)
        self._canvas.create_image(0, 0, image=self._overlay, anchor="nw")
        return True

    def redraw(self) -> None:
        w = max(240, int(self._width or 900))
        h = max(84, int(self._height or 110))
        c = self._canvas
        c.configure(width=w, height=h)
        c.delete("all")
        self._overlay = None

        # Outer frame / bezel.
        c.create_rectangle(0, 0, w, h, fill="#1e1f1e", outline="#0b0b0b", width=0)
        c.create_rectangle(1, 1, w - 1, h - 1, outline="#4b4b4b", width=2)
        c.create_rectangle(6, 6, w - 6, h - 6, fill="#35372d", outline="#0f100f", width=2)
        c.create_rectangle(12, 12, w - 12, h - 12, fill="#b2b674", outline="#657044", width=1)
        c.create_rectangle(15, 15, w - 15, h - 15, fill="#b8c077", outline="#c7d08b", width=1)

        # Subtle glass sheen and scanlines.
        c.create_line(18, 20, w - 18, 20, fill="#dce39c")
        c.create_line(18, 22, w - 18, 22, fill="#c7cf88")
        for yy in range(20, h - 22, 3):
            col = "#a7b06a" if ((yy // 3) % 2 == 0) else "#aeb775"
            c.create_line(20, yy, w - 20, yy, fill=col)

        fg = "#15250a"
        shadow = "#77804c"
        left = 26
        right = w - 26
        top = 18

        used_true_font = self._render_true_font_overlay(w, h, left=left, right=right, top=top, fg=fg, shadow=shadow)

        if not used_true_font:
            main_size = max(16.0, h * 0.33)
            sub_size = max(8.0, h * 0.14)
            prim = self._primary[:28]
            tim = self._time_text[:10]
            sec = self._secondary[:44]
            step = self._step_text[:16]
            self._draw_seg_text(left, top, prim, main_size, fill=fg, shadow=shadow, anchor="nw")
            self._draw_seg_text(right, top, tim, main_size, fill=fg, shadow=shadow, anchor="ne")
            line2_y = top + max(30, int(h * 0.41))
            self._draw_seg_text(left, line2_y, sec, sub_size, fill=fg, shadow=shadow, anchor="nw")
            self._draw_seg_text(right, line2_y, step, sub_size, fill=fg, shadow=shadow, anchor="ne")

        # Bottom embedded status/progress strip.
        strip_margin = 20
        strip_h = max(10, int(h * 0.10))
        bx0, by0 = strip_margin, h - strip_margin - strip_h
        bx1, by1 = w - strip_margin, h - strip_margin
        c.create_rectangle(bx0, by0, bx1, by1, fill="#6f7848", outline="#263014", width=1)
        c.create_line(bx0 + 1, by0 + 1, bx1 - 1, by0 + 1, fill="#9faa6e")
        fill_w = int((bx1 - bx0 - 2) * (self._progress / 100.0))
        if fill_w > 0:
            c.create_rectangle(bx0 + 1, by0 + 1, bx0 + 1 + fill_w, by1 - 1, fill="#245d15", outline="#16390d", width=1)
            for yy in range(by0 + 2, by1 - 1, 2):
                c.create_line(bx0 + 2, yy, bx0 + 1 + fill_w - 1, yy, fill="#2f741d")

        # Tiny hardware screws in the bezel corners for a stronger instrument look.
        sr = 2
        for sx, sy in ((8, 8), (w - 8, 8), (8, h - 8), (w - 8, h - 8)):
            c.create_oval(sx - sr, sy - sr, sx + sr, sy + sr, fill="#565656", outline="#222222")
