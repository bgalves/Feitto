"""Mark up a screenshot with primary colours: pen, arrow, rectangle, text.

The marks are kept as vectors (so they can be undone and re-scaled), shown live on a Tk canvas, and
burned into the pixels at the end with GDI+ (anti-aliased, built into Windows, no extra packages).
"""
import ctypes
import math
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

import snip
import ui_kit as ui
import widgets as wd

# Primary colours only.
PALETTE = [("red", "#E8231A"), ("yellow", "#FFD400"), ("green", "#00A84F"), ("blue", "#1F4FFF")]
WIDTHS = {"S": 3.0, "M": 6.0, "L": 10.0}      # pen widths, in "1200 px wide screenshot" units
TEXT_SIZES = {"S": 22.0, "M": 30.0, "L": 44.0}

# --------------------------------------------------------------------------- GDI+
gdip = ctypes.WinDLL("gdiplus")
_H, _PH = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
_F, _I = ctypes.c_float, ctypes.c_int


class PointF(ctypes.Structure):
    _fields_ = [("x", _F), ("y", _F)]


class RectF(ctypes.Structure):
    _fields_ = [("x", _F), ("y", _F), ("w", _F), ("h", _F)]


class _StartupInput(ctypes.Structure):
    _fields_ = [("GdiplusVersion", ctypes.c_uint32), ("DebugEventCallback", ctypes.c_void_p),
                ("SuppressBackgroundThread", _I), ("SuppressExternalCodecs", _I)]


def _sig(name, args):
    f = getattr(gdip, name)
    f.argtypes, f.restype = args, _I


_sig("GdiplusStartup", [ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(_StartupInput), ctypes.c_void_p])
_sig("GdipCreateBitmapFromScan0", [_I, _I, _I, _I, ctypes.c_void_p, _PH])
_sig("GdipGetImageGraphicsContext", [_H, _PH])
_sig("GdipDeleteGraphics", [_H])
_sig("GdipDisposeImage", [_H])
_sig("GdipSetSmoothingMode", [_H, _I])
_sig("GdipSetTextRenderingHint", [_H, _I])
_sig("GdipSetInterpolationMode", [_H, _I])
_sig("GdipSetPixelOffsetMode", [_H, _I])
_sig("GdipCreatePen1", [ctypes.c_uint32, _F, _I, _PH])
_sig("GdipDeletePen", [_H])
_sig("GdipSetPenStartCap", [_H, _I])
_sig("GdipSetPenEndCap", [_H, _I])
_sig("GdipSetPenLineJoin", [_H, _I])
_sig("GdipDrawLine", [_H, _H, _F, _F, _F, _F])
_sig("GdipDrawLines", [_H, _H, ctypes.POINTER(PointF), _I])
_sig("GdipDrawRectangle", [_H, _H, _F, _F, _F, _F])
_sig("GdipCreateSolidFill", [ctypes.c_uint32, _PH])
_sig("GdipDeleteBrush", [_H])
_sig("GdipFillPolygon", [_H, _H, ctypes.POINTER(PointF), _I, _I])
_sig("GdipCreateFontFamilyFromName", [ctypes.c_wchar_p, _H, _PH])
_sig("GdipDeleteFontFamily", [_H])
_sig("GdipCreateFont", [_H, _F, _I, _I, _PH])
_sig("GdipDeleteFont", [_H])
_sig("GdipDrawString", [_H, ctypes.c_wchar_p, _I, _H, ctypes.POINTER(RectF), _H, _H])
_sig("GdipDrawImageRectRectI", [_H, _H, _I, _I, _I, _I, _I, _I, _I, _I, _I, _H, _H, _H])
_sig("GdipCreateImageAttributes", [_PH])
_sig("GdipSetImageAttributesWrapMode", [_H, _I, ctypes.c_uint32, _I])
_sig("GdipDisposeImageAttributes", [_H])

_token = ctypes.c_size_t(0)
_ready = None
PF_32BPP_RGB = 0x00022009


def available():
    """Start GDI+ once; False if this Windows has no usable gdiplus."""
    global _ready
    if _ready is None:
        try:
            _ready = gdip.GdiplusStartup(ctypes.byref(_token), ctypes.byref(_StartupInput(1, None, 0, 0)), None) == 0
        except OSError:
            _ready = False
    return _ready


def _argb(hexcolor):
    h = hexcolor.lstrip("#")
    return 0xFF000000 | int(h[0:2], 16) << 16 | int(h[2:4], 16) << 8 | int(h[4:6], 16)


def arrow_parts(p1, p2, width):
    """Shaft end point and head triangle for an arrow p1 -> p2 (shared by the canvas and GDI+)."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    head = min(length * 0.7, max(18.0, width * 4.8))
    half = head * 0.45
    bx, by = p2[0] - ux * head, p2[1] - uy * head
    return ((p2[0] - ux * head * 0.75, p2[1] - uy * head * 0.75),
            [p2, (bx - uy * half, by + ux * half), (bx + uy * half, by - ux * half)])


def _bitmap(buf, w, h):
    bmp, g = ctypes.c_void_p(), ctypes.c_void_p()
    if gdip.GdipCreateBitmapFromScan0(w, h, w * 4, PF_32BPP_RGB, buf, ctypes.byref(bmp)) != 0:
        raise RuntimeError("GDI+ could not create a bitmap")
    gdip.GdipGetImageGraphicsContext(bmp, ctypes.byref(g))
    return bmp, g


def _pts(points):
    arr = (PointF * len(points))()
    for i, (x, y) in enumerate(points):
        arr[i].x, arr[i].y = x, y
    return arr


def _draw(g, a):
    kind, color = a["t"], _argb(a["c"])
    if kind == "text":
        fam, font, brush = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        gdip.GdipCreateFontFamilyFromName("Segoe UI", None, ctypes.byref(fam))
        gdip.GdipCreateFont(fam, _F(a["size"]), 1, 2, ctypes.byref(font))     # bold, pixel units
        gdip.GdipCreateSolidFill(color, ctypes.byref(brush))
        rect = RectF(a["p"][0], a["p"][1], 100000.0, 100000.0)
        gdip.GdipDrawString(g, a["text"], -1, font, ctypes.byref(rect), None, brush)
        gdip.GdipDeleteBrush(brush); gdip.GdipDeleteFont(font); gdip.GdipDeleteFontFamily(fam)
        return
    pen = ctypes.c_void_p()
    gdip.GdipCreatePen1(color, _F(a["w"]), 0, ctypes.byref(pen))
    gdip.GdipSetPenStartCap(pen, 2); gdip.GdipSetPenEndCap(pen, 2); gdip.GdipSetPenLineJoin(pen, 2)  # round
    if kind == "pen":
        pts = a["pts"] if len(a["pts"]) > 1 else [a["pts"][0], (a["pts"][0][0] + 0.01, a["pts"][0][1])]
        gdip.GdipDrawLines(g, pen, _pts(pts), len(pts))
    elif kind == "rect":
        (x1, y1), (x2, y2) = a["p1"], a["p2"]
        gdip.GdipDrawRectangle(g, pen, _F(min(x1, x2)), _F(min(y1, y2)), _F(abs(x2 - x1)), _F(abs(y2 - y1)))
    elif kind == "arrow":
        base, head = arrow_parts(a["p1"], a["p2"], a["w"])
        gdip.GdipDrawLine(g, pen, _F(a["p1"][0]), _F(a["p1"][1]), _F(base[0]), _F(base[1]))
        brush = ctypes.c_void_p()
        gdip.GdipCreateSolidFill(color, ctypes.byref(brush))
        gdip.GdipFillPolygon(g, brush, _pts(head), 3, 0)
        gdip.GdipDeleteBrush(brush)
    gdip.GdipDeletePen(pen)


def render(bgra, w, h, annotations):
    """Burn the marks into a copy of the picture (top-down BGRA in, top-down BGRA out)."""
    if not annotations:
        return bgra
    if not available():
        raise RuntimeError("GDI+ is not available")
    buf = ctypes.create_string_buffer(bgra, len(bgra))
    bmp, g = _bitmap(buf, w, h)
    try:
        gdip.GdipSetSmoothingMode(g, 4)          # anti-alias
        gdip.GdipSetTextRenderingHint(g, 4)
        for a in annotations:
            _draw(g, a)
    finally:
        gdip.GdipDeleteGraphics(g)
        gdip.GdipDisposeImage(bmp)
    return buf.raw


def scale_bgra(bgra, w, h, dw, dh):
    """High-quality resize for the on-screen preview."""
    if not available():
        return None
    src = ctypes.create_string_buffer(bgra, len(bgra))
    dst = ctypes.create_string_buffer(dw * dh * 4)
    sb, sg = _bitmap(src, w, h)
    db, dg = _bitmap(dst, dw, dh)
    attr = ctypes.c_void_p()
    gdip.GdipCreateImageAttributes(ctypes.byref(attr))
    gdip.GdipSetImageAttributesWrapMode(attr, 3, 0, 0)   # flip-tile: edge pixels do not fade to black
    try:
        gdip.GdipSetInterpolationMode(dg, 7)     # high-quality bicubic
        gdip.GdipSetPixelOffsetMode(dg, 2)
        gdip.GdipDrawImageRectRectI(dg, sb, 0, 0, dw, dh, 0, 0, w, h, 2, attr, None, None)
    finally:
        gdip.GdipDisposeImageAttributes(attr)
        for h_ in (sg, dg):
            gdip.GdipDeleteGraphics(h_)
        for h_ in (sb, db):
            gdip.GdipDisposeImage(h_)
    return dst.raw


# --------------------------------------------------------------------------- editor widget
class Annotator(tk.Frame):
    """Toolbar + canvas. .render() returns the picture with the marks burned in; .count() the number of marks."""

    def __init__(self, master, theme, bgra, w, h):
        super().__init__(master)
        self.th, self.bgra, self.w, self.h = theme, bgra, w, h
        self.k = max(1.0, max(w, h) / 1200.0)       # bigger pictures get proportionally thicker marks
        self.anns, self.live = [], None
        self.tool = tk.StringVar(value="pen")
        self.size = tk.StringVar(value="M")
        self.color = PALETTE[0][1]
        self.photo = self.sc = self.ox = self.oy = None
        self._shown = None
        self._entry = None
        S = theme.s
        theme.track(self, bg="bg")

        bar = tk.Frame(self)
        theme.track(bar, bg="bg")
        bar.pack(fill="x", padx=S(16), pady=(S(14), S(8)))
        wd.Segmented(bar, theme, [("pen", "Pen"), ("arrow", "Arrow"), ("rect", "Rectangle"), ("text", "Text")],
                     self.tool, compact=True).pack(side="left")
        self.sw = tk.Canvas(bar, width=S(34) * len(PALETTE), height=S(34), highlightthickness=0, bd=0)
        theme.track(self.sw, bg="bg")
        self.sw.pack(side="left", padx=(S(16), 0))
        self.sw.bind("<Button-1>", self._pick_color)
        wd.Segmented(bar, theme, [("S", "Thin"), ("M", "Medium"), ("L", "Thick")], self.size,
                     compact=True).pack(side="left", padx=(S(8), 0))
        wd.Button(bar, theme, "Clear", self.clear, h=34).pack(side="right")
        wd.Button(bar, theme, "Undo", self.undo, h=34).pack(side="right", padx=(0, S(10)))
        theme.on_change(self.sw, self._draw_swatches)
        self._draw_swatches()

        card = wd.Card(self, theme, radius=16)
        card.pack(fill="both", expand=True, padx=S(16), pady=(0, S(8)))
        self.cv = tk.Canvas(card.inner, highlightthickness=0, bd=0, cursor="crosshair")
        theme.track(self.cv, bg="surface")
        self.cv.pack(fill="both", expand=True)
        self.cv.bind("<Configure>", lambda e: self.after_idle(self._layout))
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._release)

    # -- model ---------------------------------------------------------------------
    def count(self):
        return len(self.anns)

    def render(self):
        return render(self.bgra, self.w, self.h, self.anns)

    def undo(self):
        if self.anns:
            self.anns.pop()
            self._redraw()

    def clear(self):
        self.anns.clear()
        self._redraw()

    # -- toolbar -------------------------------------------------------------------
    def _draw_swatches(self):
        p, S = self.th.pal, self.th.s
        self.sw.delete("all")
        for i, (_n, col) in enumerate(PALETTE):
            cx = S(17) + i * S(34)
            if col == self.color:
                self.sw.create_oval(cx - S(15), S(2), cx + S(15), S(32), outline=p["accent"], width=2)
            self.sw.create_oval(cx - S(11), S(6), cx + S(11), S(28), fill=col, outline=p["border"])

    def _pick_color(self, e):
        i = min(len(PALETTE) - 1, max(0, e.x // self.th.s(34)))
        self.color = PALETTE[i][1]
        self._draw_swatches()

    # -- picture / layout ----------------------------------------------------------------
    def _layout(self):
        cw, ch = self.cv.winfo_width(), self.cv.winfo_height()
        if cw < 50 or ch < 50:
            return
        pad = self.th.s(10)
        sc = min(1.0, (cw - 2 * pad) / self.w, (ch - 2 * pad) / self.h)
        dw, dh = max(1, int(self.w * sc)), max(1, int(self.h * sc))
        self.sc, self.ox, self.oy = sc, (cw - dw) // 2, (ch - dh) // 2
        if self._shown != (dw, dh):
            data = self.bgra if sc >= 1.0 else (scale_bgra(self.bgra, self.w, self.h, dw, dh) or self.bgra)
            self.photo = tk.PhotoImage(data=snip.bgra_to_ppm(data, dw, dh))
            self._shown = (dw, dh)
        self._redraw()

    def _redraw(self):
        if self.sc is None:
            return
        self.cv.delete("all")
        self.cv.create_image(self.ox, self.oy, anchor="nw", image=self.photo, tags="img")
        for a in self.anns:
            self._paint(a)

    def _c(self, p):   # image -> canvas
        return self.ox + p[0] * self.sc, self.oy + p[1] * self.sc

    def _i(self, x, y):  # canvas -> image (clamped)
        return (min(max((x - self.ox) / self.sc, 0.0), float(self.w)), min(max((y - self.oy) / self.sc, 0.0), float(self.h)))

    def _paint(self, a):
        cv, sc, kind = self.cv, self.sc, a["t"]
        if kind == "text":
            x, y = self._c(a["p"])
            return [cv.create_text(x, y, anchor="nw", text=a["text"], fill=a["c"],
                                   font=("Segoe UI", -max(6, int(a["size"] * sc)), "bold"))]
        wd = max(1.0, a["w"] * sc)
        if kind == "pen":
            pts = [c for p in a["pts"] for c in self._c(p)]
            if len(pts) < 4:
                pts += pts
            return [cv.create_line(*pts, fill=a["c"], width=wd, capstyle="round", joinstyle="round")]
        if kind == "rect":
            (x1, y1), (x2, y2) = self._c(a["p1"]), self._c(a["p2"])
            return [cv.create_rectangle(x1, y1, x2, y2, outline=a["c"], width=wd)]
        base, head = arrow_parts(self._c(a["p1"]), self._c(a["p2"]), wd)
        x1, y1 = self._c(a["p1"])
        return [cv.create_line(x1, y1, base[0], base[1], fill=a["c"], width=wd, capstyle="round"),
                cv.create_polygon([c for p in head for c in p], fill=a["c"], outline=a["c"])]

    # -- mouse ---------------------------------------------------------------------------
    def _new(self, kind, p):
        w = WIDTHS[self.size.get()] * self.k
        if kind == "pen":
            return {"t": "pen", "c": self.color, "w": w, "pts": [p]}
        return {"t": kind, "c": self.color, "w": w, "p1": p, "p2": p}

    def _press(self, e):
        if self.sc is None:
            return
        self._commit_text()
        kind = self.tool.get()
        p = self._i(e.x, e.y)
        if kind == "text":
            return self._start_text(e.x, e.y, p)
        self.live = self._new(kind, p)
        self.live_ids = []

    def _drag(self, e):
        if not self.live:
            return
        p = self._i(e.x, e.y)
        if self.live["t"] == "pen":
            if math.hypot(p[0] - self.live["pts"][-1][0], p[1] - self.live["pts"][-1][1]) * self.sc < 1.5:
                return
            self.live["pts"].append(p)
        else:
            self.live["p2"] = p
        for i in self.live_ids:
            self.cv.delete(i)
        self.live_ids = self._paint(self.live)

    def _release(self, e):
        if not self.live:
            return
        a, self.live = self.live, None
        if a["t"] in ("rect", "arrow") and math.hypot(a["p2"][0] - a["p1"][0], a["p2"][1] - a["p1"][1]) * self.sc < 4:
            return self._redraw()
        self.anns.append(a)
        self._redraw()

    # -- text tool ---------------------------------------------------------------------------
    def _start_text(self, x, y, p):
        size = TEXT_SIZES[self.size.get()] * self.k
        e = tk.Entry(self.cv, font=("Segoe UI", -max(8, int(size * self.sc)), "bold"), fg=self.color, bd=0,
                     bg=self.th.pal["surface2"], insertbackground=self.color, relief="flat", width=18)
        self._entry = (e, p, size, self.color)
        self.cv.create_window(x, y, window=e, anchor="nw", tags="entry")
        e.focus_set()
        e.bind("<Return>", lambda ev: self._commit_text())
        e.bind("<Escape>", lambda ev: (self._cancel_text(), "break")[1])

    def _commit_text(self):
        if not self._entry:
            return
        e, p, size, color = self._entry
        text = e.get().strip()
        self._entry = None
        e.destroy()
        if text:
            self.anns.append({"t": "text", "c": color, "p": p, "text": text, "size": size})
        self._redraw()

    def _cancel_text(self):
        if self._entry:
            self._entry[0].destroy()
            self._entry = None
            self._redraw()

    def finish_editing(self):
        """Commit a half-typed text note before closing."""
        self._commit_text()
