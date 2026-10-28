"""Look & feel for Feitto: light/dark/system themes, flat modern widgets, DPI awareness.

Standard library only (tkinter). Everything that has a colour is re-painted live when the theme
changes, so the Options dialog can preview a theme while it is open.
"""
import base64
import ctypes
import struct
from ctypes import wintypes
import tkinter as tk
import tkinter.font as tkfont
import zlib
from tkinter import ttk


def enable_dpi():
    """Call before creating the Tk root so text and lines are crisp on high-DPI screens."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


PALETTES = {
    "dark": dict(bg="#16171B", surface="#1E2025", surface2="#262932", field="#262932", border="#33363F", text="#ECEEF3",
                 muted="#9298A6", accent="#7C9CFF", accent_hover="#92AEFF", accent_fg="#0B1020",
                 sel="#2A3250", hover="#252831", danger="#FF7B7B", switch_off="#3B404C", knob="#FFFFFF"),
    "light": dict(bg="#F3F4F8", surface="#FFFFFF", surface2="#F1F2F7", field="#FFFFFF", border="#E1E4EC", text="#1B1D24",
                  muted="#6B7280", accent="#4A63F0", accent_hover="#3B52DB", accent_fg="#FFFFFF",
                  sel="#E4E9FF", hover="#EEF0F7", danger="#D93B3B", switch_off="#C8CCD8", knob="#FFFFFF"),
}


def system_is_dark():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def rr(c, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle on a canvas."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


# Segoe Fluent Icons / MDL2 code points
ICON = dict(search="", pin="", pinned="", delete="", settings="",
            sun="", moon="", copy="")


ICON["camera"] = ""


ICON["check"] = ""


class Theme:
    def __init__(self, root, mode="system"):
        self.root = root
        self.scale = root.winfo_fpixels("1i") / 96.0
        self.mode = mode
        fams = set(tkfont.families(root))
        self.ui_family = next((f for f in ("Segoe UI Variable Text", "Segoe UI") if f in fams), "TkDefaultFont")
        self.icon_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), None)
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self._tracked, self._callbacks = [], []
        self.dark = False
        self.pal = PALETTES["light"]
        self.apply()

    # -- helpers -----------------------------------------------------------
    def s(self, px):
        return int(round(px * self.scale))

    def font(self, size=10, weight="normal"):
        return (self.ui_family, size, weight)

    def icon_font(self, size=12):
        return (self.icon_family or self.ui_family, size)

    def resolve_dark(self):
        return self.mode == "dark" or (self.mode == "system" and system_is_dark())

    def set_mode(self, mode):
        self.mode = mode
        self.apply()

    def refresh_system(self):
        if self.mode == "system" and self.resolve_dark() != self.dark:
            self.apply()

    def track(self, widget, **opts):
        """track(w, bg="surface", fg="text"): widget options follow the palette."""
        self._tracked.append((widget, opts))
        self._paint(widget, opts)
        return widget

    def on_change(self, widget, cb):
        self._callbacks.append((widget, cb))

    def _paint(self, widget, opts):
        try:
            widget.configure(**{o: self.pal[k] for o, k in opts.items()})
        except tk.TclError:
            pass

    # -- apply ---------------------------------------------------------------
    def apply(self):
        self.dark = self.resolve_dark()
        self.pal = PALETTES["dark" if self.dark else "light"]
        self._style_ttk()
        self.root.configure(bg=self.pal["bg"])
        alive = []
        for w, o in self._tracked:
            if self._exists(w):
                self._paint(w, o)
                alive.append((w, o))
        self._tracked = alive
        alive = []
        for w, cb in self._callbacks:
            if self._exists(w):
                cb()
                alive.append((w, cb))
        self._callbacks = alive

    @staticmethod
    def _exists(w):
        try:
            return bool(w.winfo_exists())
        except tk.TclError:
            return False

    def _style_ttk(self):
        p, st, s, f = self.pal, self.style, self.s, self.ui_family
        st.configure(".", background=p["bg"], foreground=p["text"], fieldbackground=p["field"],
                     bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                     troughcolor=p["bg"], focuscolor=p["bg"], font=(f, 10))
        st.configure("TFrame", background=p["bg"])
        st.configure("Card.TFrame", background=p["surface"])
        st.configure("TLabel", background=p["bg"], foreground=p["text"])
        st.configure("Muted.TLabel", foreground=p["muted"])
        st.configure("TButton", padding=(s(16), s(7)), background=p["field"], foreground=p["text"],
                     bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"],
                     relief="flat", focusthickness=0)
        st.map("TButton", background=[("active", p["hover"]), ("pressed", p["hover"])],
               lightcolor=[("active", p["hover"])], darkcolor=[("active", p["hover"])])
        st.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                     bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"])
        st.map("Accent.TButton", background=[("active", p["accent_hover"]), ("pressed", p["accent_hover"])],
               foreground=[("active", p["accent_fg"])], lightcolor=[("active", p["accent_hover"])],
               darkcolor=[("active", p["accent_hover"])], bordercolor=[("active", p["accent_hover"])])
        st.configure("TEntry", fieldbackground=p["field"], foreground=p["text"], insertcolor=p["text"],
                     padding=s(6), bordercolor=p["border"], lightcolor=p["field"], darkcolor=p["field"])
        st.map("TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])],
               darkcolor=[("focus", p["accent"])])
        st.configure("TCombobox", fieldbackground=p["field"], background=p["field"], foreground=p["text"],
                     arrowcolor=p["muted"], bordercolor=p["border"], lightcolor=p["field"],
                     darkcolor=p["field"], selectbackground=p["field"], selectforeground=p["text"],
                     padding=s(5))
        st.map("TCombobox", fieldbackground=[("readonly", p["field"])],
               selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["text"])],
               bordercolor=[("focus", p["accent"])])
        self.root.option_add("*TCombobox*Listbox.background", p["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["sel"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", p["text"])
        st.configure("Treeview", background=p["surface"], fieldbackground=p["surface"], foreground=p["text"],
                     rowheight=s(30), borderwidth=0, relief="flat")
        st.map("Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["text"])])
        st.configure("Treeview.Heading", background=p["surface2"], foreground=p["muted"], relief="flat",
                     padding=(s(8), s(6)), borderwidth=0, font=(f, 9, "bold"))
        st.configure("Treeview.Heading", anchor="w")
        st.map("Treeview.Heading", background=[("active", p["hover"])])
        st.layout("Slim.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        st.configure("Slim.Vertical.TScrollbar", width=s(9), troughcolor=p["surface"], background=p["border"],
                     bordercolor=p["surface"], lightcolor=p["border"], darkcolor=p["border"], relief="flat")
        st.map("Slim.Vertical.TScrollbar", background=[("active", p["muted"]), ("pressed", p["muted"])])

    def decorate(self, win):
        """Background + a title bar that matches the theme (Windows 10/11)."""
        win.configure(bg=self.pal["bg"])
        try:
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            dwm = ctypes.windll.dwmapi

            def put(attr, value):
                v = ctypes.c_int(value)
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)

            def colorref(hexcolor):
                h = hexcolor.lstrip("#")
                return int(h[0:2], 16) | int(h[2:4], 16) << 8 | int(h[4:6], 16) << 16

            put(20, 1 if self.dark else 0)           # immersive dark mode (title bar buttons)
            put(35, colorref(self.pal["bg"]))        # caption colour   (Windows 11)
            put(36, colorref(self.pal["text"]))      # caption text
            put(34, colorref(self.pal["border"]))    # window border
        except Exception:
            pass


# --------------------------------------------------------------------------- images
def dib_to_png(dib, max_w=520, max_h=420):
    """Convert a clipboard DIB (24/32-bit) into a small PNG for preview.

    Returns (png_bytes, orig_w, orig_h) or None for formats we do not decode (palette/RLE/JPEG).
    """
    try:
        hdr, w, h, _planes, bpp, comp = struct.unpack_from("<IiiHHI", dib, 0)
        if hdr < 40 or bpp not in (24, 32) or comp not in (0, 3) or w <= 0 or h == 0 or w * abs(h) > 80_000_000:
            return None
        top_down, h = h < 0, abs(h)
        clr_used = struct.unpack_from("<I", dib, 32)[0]
        off = hdr + (12 if (comp == 3 and hdr == 40) else 0) + clr_used * 4
        stride = ((w * bpp + 31) // 32) * 4
        k = max(1, -(-w // max_w), -(-h // max_h))  # integer decimation factor
        ow, oh, step = len(range(0, w, k)), len(range(0, h, k)), bpp // 8
        raw = bytearray()
        for y in range(0, h, k):
            src_y = y if top_down else h - 1 - y
            row = dib[off + src_y * stride: off + src_y * stride + w * step]
            if len(row) < w * step:
                return None
            out = bytearray(3 * ow)
            out[0::3] = row[2::step][::k]
            out[1::3] = row[1::step][::k]
            out[2::3] = row[0::step][::k]
            raw += b"\x00" + out

        def chunk(tag, data):
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", ow, oh, 8, 2, 0, 0, 0)) +
               chunk(b"IDAT", zlib.compress(bytes(raw), 1)) + chunk(b"IEND", b""))
        return png, w, h
    except Exception:
        return None


def dib_to_bgra(dib):
    """Decode a 24/32-bit clipboard DIB into (top-down BGRA bytes, width, height). Raises ValueError otherwise."""
    hdr, w, h, _planes, bpp, comp = struct.unpack_from("<IiiHHI", dib, 0)
    if hdr < 40 or bpp not in (24, 32) or comp not in (0, 3) or w <= 0 or h == 0 or w * abs(h) > 80_000_000:
        raise ValueError("unsupported bitmap format")
    top_down, h = h < 0, abs(h)
    clr_used = struct.unpack_from("<I", dib, 32)[0]
    off = hdr + (12 if (comp == 3 and hdr == 40) else 0) + clr_used * 4
    stride, step = ((w * bpp + 31) // 32) * 4, bpp // 8
    out = bytearray(w * h * 4)
    for y in range(h):
        src = y if top_down else h - 1 - y
        row = dib[off + src * stride: off + src * stride + w * step]
        if len(row) < w * step:
            raise ValueError("truncated bitmap")
        seg = bytearray(w * 4)
        seg[0::4], seg[1::4], seg[2::4], seg[3::4] = row[0::step], row[1::step], row[2::step], b"\xff" * w
        out[y * w * 4:(y + 1) * w * 4] = seg
    return bytes(out), w, h


def png_photo(png):
    return tk.PhotoImage(data=base64.b64encode(png))


# --------------------------------------------------------------------------- effects
import time


def lerp_color(a, b, t):
    a, b = a.lstrip("#"), b.lstrip("#")
    ca, cb = [int(a[i:i + 2], 16) for i in (0, 2, 4)], [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def ease_out(t):
    return 1 - (1 - t) ** 3


def animate(widget, ms, step, done=None, key="a"):
    """Run step(progress 0..1) for ms milliseconds (time based, so slow frames do not stretch it).
    Starting a new animation with the same key replaces the previous one."""
    jobs = widget.__dict__.setdefault("_fx", {})
    old = jobs.pop(key, None)
    if old is not None:
        try:
            widget.after_cancel(old)
        except tk.TclError:
            pass
    t0 = time.perf_counter()

    def tick():
        try:
            t = min(1.0, (time.perf_counter() - t0) * 1000.0 / ms)
            step(ease_out(t))
            if t < 1.0:
                jobs[key] = widget.after(16, tick)
            else:
                jobs.pop(key, None)
                if done:
                    done()
        except tk.TclError:  # the widget was destroyed mid-animation
            pass

    tick()


class _Margins(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def round_window(win, theme=None):
    """Rounded corners + a soft shadow + a themed 1px border on a borderless window (Windows 11; harmless elsewhere)."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetAncestor(win.winfo_id(), 2) or win.winfo_id()
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)       # rounded corners
        if theme is not None:
            h = theme.pal["border"].lstrip("#")
            ref = int(h[0:2], 16) | int(h[2:4], 16) << 8 | int(h[4:6], 16) << 16
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(ctypes.c_int(ref)), 4)  # border colour
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(1, 1, 1, 1)))   # shadow on borderless windows
    except Exception:
        pass


def fade_in(win, ms=150):
    try:
        win.attributes("-alpha", 0.0)
        animate(win, ms, lambda t: win.attributes("-alpha", t), key="fade")
    except tk.TclError:
        pass


# --------------------------------------------------------------------------- toast
def _work_area(root):
    """Screen area not covered by the taskbar: (left, top, right, bottom)."""
    r = wintypes.RECT()
    if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
        return r.left, r.top, r.right, r.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def _no_activate(win):
    """Show a window without stealing keyboard focus (WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)."""
    try:
        u = ctypes.windll.user32
        u.GetWindowLongPtrW.restype, u.GetWindowLongPtrW.argtypes = ctypes.c_ssize_t, [ctypes.c_void_p, ctypes.c_int]
        u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        hwnd = win.winfo_id()
        u.SetWindowLongPtrW(hwnd, -20, u.GetWindowLongPtrW(hwnd, -20) | 0x08000000 | 0x80)
    except Exception:
        pass


class _Toast:
    def __init__(self, root, theme, title, text, image, ms):
        p, S = theme.pal, theme.s
        self.root, self.jobs, self.top = root, [], tk.Toplevel(root)
        top = self.top
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", 0.0)
        card = tk.Frame(top, bg=p["surface"], highlightthickness=1, highlightbackground=p["border"],
                        highlightcolor=p["border"])
        card.pack()
        row = tk.Frame(card, bg=p["surface"])
        row.pack(padx=S(16), pady=S(14))
        if image is not None:
            tk.Label(row, image=image, bg=p["surface"]).pack(side="left", padx=(0, S(14)))
        col = tk.Frame(row, bg=p["surface"])
        col.pack(side="left")
        tk.Label(col, text=title, bg=p["surface"], fg=p["text"], font=theme.font(10, "bold"), anchor="w").pack(anchor="w")
        tk.Label(col, text=text, bg=p["surface"], fg=p["muted"], font=theme.font(10), anchor="w", justify="left",
                 wraplength=S(300)).pack(anchor="w")
        for w in (top, card, row, col, *col.winfo_children(), *row.winfo_children()):
            w.bind("<Button-1>", lambda e: self.close())
        top.update_idletasks()
        W, H = top.winfo_reqwidth(), top.winfo_reqheight()
        l, t, r, b = _work_area(root)
        top.geometry(f"+{r - W - S(16)}+{b - H - S(16)}")
        _no_activate(top)
        round_window(top, theme)
        x0, y0 = r - W - S(16), b - H - S(16)
        top.geometry(f"+{x0}+{y0 + S(14)}")
        top.deiconify()
        animate(top, 220, lambda t: top.geometry(f"+{x0}+{int(y0 + S(14) * (1 - t))}"), key="slide")
        self._fade(0.0, 1.0, 0.18, lambda: self._later(ms, lambda: self._fade(1.0, 0.0, 0.25, self.close)))

    def _later(self, ms, fn):
        self.jobs.append(self.root.after(ms, fn))

    def _fade(self, a0, a1, seconds, then, steps=8):
        def step(i):
            if not self.top.winfo_exists():
                return
            self.top.attributes("-alpha", a0 + (a1 - a0) * i / steps)
            if i < steps:
                self._later(int(seconds * 1000 / steps), lambda: step(i + 1))
            else:
                then()
        step(0)

    def close(self):
        for j in self.jobs:
            try:
                self.root.after_cancel(j)
            except tk.TclError:
                pass
        self.jobs = []
        try:
            self.top.destroy()
        except tk.TclError:
            pass


def show_toast(root, theme, title, text, image=None, ms=3200):
    """Feitto's own notification card. Windows balloons would be labelled with the host program ("Python")."""
    old = getattr(root, "_feitto_toast", None)
    if old is not None:
        old.close()
    root._feitto_toast = _Toast(root, theme, title, text, image, ms)
