"""Screen capture for Feitto, in the spirit of Windows' PrtSc / Snipping Tool.

  * full screen (all monitors), rectangle (drag an area), window (click a window)
  * optional delay (3 / 5 / 10 s) so menus and tooltips can be opened first
Windows only, standard library only. All coordinates are physical screen pixels.
"""
import ctypes
import os
import struct
import tkinter as tk
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwm = ctypes.WinDLL("dwmapi", use_last_error=True)

user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]

SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
_PER_MONITOR_V2 = ctypes.c_void_p(-4)   # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2


def _enter_physical():
    """Let this thread see real screen pixels on every monitor.

    The app itself is system-DPI aware, so with monitors of different scale factors Windows reports scaled
    coordinates for the secondary screens. Capturing needs the real pixels. Returns the previous context."""
    try:
        f = user32.SetThreadDpiAwarenessContext
        f.restype, f.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
        return f(_PER_MONITOR_V2)
    except (AttributeError, OSError):   # Windows older than 10 (1607): nothing to switch
        return None


def _leave_physical(prev):
    if prev:
        try:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(prev))
        except (AttributeError, OSError):
            pass


class physical_pixels:
    def __enter__(self):
        self.prev = _enter_physical()
        return self

    def __exit__(self, *exc):
        _leave_physical(self.prev)


def virtual_screen():
    """(x, y, width, height) of the rectangle that spans every monitor, in real pixels (x/y can be negative)."""
    with physical_pixels():
        gm = user32.GetSystemMetrics
        return gm(76), gm(77), gm(78), gm(79)


def primary_size():
    with physical_pixels():
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def grab(x, y, w, h):
    """Top-down BGRA bytes of a screen region (includes layered / translucent windows)."""
    with physical_pixels():
        return _grab(x, y, w, h)


def _grab(x, y, w, h):
    sdc = user32.GetDC(None)
    mdc = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        gdi32.BitBlt(mdc, 0, 0, w, h, sdc, x, y, SRCCOPY | CAPTUREBLT)
        hdr = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(hdr + bytes(w * h * 4))
        gdi32.GetDIBits(mdc, bmp, 0, h, ctypes.addressof(buf) + 40, buf, 0)
        return bytes(buf.raw[40:40 + w * h * 4])  # create_string_buffer adds one trailing NUL
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, sdc)


def crop_bgra(bgra, width, x, y, w, h):
    return b"".join(bgra[((y + r) * width + x) * 4:((y + r) * width + x + w) * 4] for r in range(h))


def bgra_to_dib24(bgra, w, h):
    """A clipboard-ready 24-bit bottom-up DIB (what every Windows app can paste)."""
    stride = (w * 3 + 3) // 4 * 4
    pad = bytes(stride - w * 3)
    rows = []
    for y in range(h - 1, -1, -1):
        row = bgra[y * w * 4:(y + 1) * w * 4]
        out = bytearray(w * 3)
        out[0::3], out[1::3], out[2::3] = row[0::4], row[1::4], row[2::4]
        rows.append(bytes(out) + pad)
    return struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, stride * h, 0, 0, 0, 0) + b"".join(rows)


def bgra_to_ppm(bgra, w, h):
    """Raw PPM that tk.PhotoImage can load directly (fast, no compression)."""
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    return b"P6\n%d %d\n255\n" % (w, h) + bytes(rgb)


WNDENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def list_windows(skip_pid=None):
    """Visible top-level windows as (left, top, right, bottom), front-most first."""
    with physical_pixels():
        return _list_windows(skip_pid)


def _list_windows(skip_pid=None):
    out = []

    def cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if skip_pid is not None and pid.value == skip_pid:
                return True
            cloaked = ctypes.c_int(0)
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
            if cloaked.value:
                return True
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            if cls.value in ("Progman", "WorkerW"):  # the desktop itself
                return True
            r = wintypes.RECT()
            if dwm.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:  # visible frame
                user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 1 and r.bottom - r.top > 1:
                out.append((r.left, r.top, r.right, r.bottom))
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUM(cb), 0)
    return out


class Selector:
    """Frozen full-screen overlay. mode 'rect': drag an area. mode 'window': click a window.

    run() returns (x, y, w, h) in screen pixels, or None if cancelled (Esc / right-click).
    """

    def __init__(self, root, theme, bgra, vx, vy, vw, vh, mode):
        self._ctx = _enter_physical()   # real pixels for this window's whole life (see physical_pixels)
        try:
            self._build(root, theme, bgra, vx, vy, vw, vh, mode)
        except Exception:
            _leave_physical(self._ctx)
            raise

    def _place(self, x, y, w, h):
        """Make the overlay cover exactly the virtual screen, whatever Tk did with the requested geometry."""
        top = self.top
        top.update()
        if (top.winfo_x(), top.winfo_y(), top.winfo_width(), top.winfo_height()) == (x, y, w, h):
            return
        try:
            hwnd = user32.GetAncestor(top.winfo_id(), 2) or top.winfo_id()
            user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), x, y, w, h, 0x0010 | 0x0040)  # topmost, no activate, show
            top.update_idletasks()
        except Exception:
            pass

    def _build(self, root, theme, bgra, vx, vy, vw, vh, mode):
        self.th, self.vx, self.vy, self.vw, self.vh, self.mode = theme, vx, vy, vw, vh, mode
        self.windows = list_windows(os.getpid()) if mode == "window" else []
        top = self.top = tk.Toplevel(root)
        top.overrideredirect(True)
        # "+-1920" is how Tk spells a negative position; "-1920" would mean 1920 px from the RIGHT edge, which put
        # the overlay in the wrong place whenever a monitor sits left of / above the main one.
        top.geometry(f"{vw}x{vh}+{vx}+{vy}")
        top.attributes("-topmost", True)
        self._place(vx, vy, vw, vh)
        cv = self.cv = tk.Canvas(top, width=vw, height=vh, highlightthickness=0, bd=0, cursor="crosshair")
        cv.pack()
        self.photo = tk.PhotoImage(data=bgra_to_ppm(bgra, vw, vh))
        cv.create_image(0, 0, anchor="nw", image=self.photo)
        self.dim = [cv.create_rectangle(0, 0, 0, 0, fill="#000000", stipple="gray50", outline="") for _ in range(4)]
        self._dim(None)
        self.box = cv.create_rectangle(0, 0, 0, 0, outline=theme.pal["accent"], width=2, state="hidden")
        self.tag_bg = cv.create_rectangle(0, 0, 0, 0, fill=theme.pal["accent"], outline="", state="hidden")
        self.tag = cv.create_text(0, 0, text="", fill=theme.pal["accent_fg"], font=theme.font(10, "bold"),
                                  anchor="nw", state="hidden")
        hint = ("Drag to select an area" if mode == "rect" else "Click a window") + "   ·   Esc to cancel"
        pw, _ph = primary_size()
        hx = -vx + pw // 2   # centre of the PRIMARY monitor, not of the whole desktop (that is the seam between screens)
        if not 0 < hx < vw:
            hx = vw // 2
        t = cv.create_text(hx, -vy + theme.s(28) if 0 <= -vy < vh else theme.s(28), text=hint, fill="#FFFFFF", font=theme.font(11, "bold"))
        x1, y1, x2, y2 = cv.bbox(t)
        bg = cv.create_rectangle(x1 - 14, y1 - 8, x2 + 14, y2 + 8, fill="#16171B", outline="")
        cv.tag_lower(bg, t)
        self.start, self.result = None, None
        self.done = tk.BooleanVar(value=False)
        cv.bind("<Motion>", self._motion)
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        cv.bind("<Button-3>", lambda e: self._finish(None))
        top.bind("<Escape>", lambda e: self._finish(None))
        cv.bind("<Escape>", lambda e: self._finish(None))

    def run(self):
        try:
            self.top.update()
            self.top.focus_force()
            self.cv.focus_set()
            self.top.wait_variable(self.done)
            res = self.result
            self.top.destroy()
            return res
        finally:
            _leave_physical(self._ctx)

    def _finish(self, res):
        self.result = res
        self.done.set(True)

    def _dim(self, sel):
        W, H = self.vw, self.vh
        if sel is None:
            rects = [(0, 0, W, H), (0, 0, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)]
        else:
            x1, y1, x2, y2 = sel
            rects = [(0, 0, W, y1), (0, y2, W, H), (0, y1, x1, y2), (x2, y1, W, y2)]
        for item, r in zip(self.dim, rects):
            self.cv.coords(item, *r)

    def _show(self, x1, y1, x2, y2):
        cv = self.cv
        self._dim((x1, y1, x2, y2))
        cv.coords(self.box, x1, y1, x2, y2)
        cv.itemconfigure(self.box, state="normal")
        cv.itemconfigure(self.tag, text=f" {x2 - x1} × {y2 - y1} ", state="normal")
        ty = y2 + 6 if y2 + 30 < self.vh else max(0, y1 - 28)
        cv.coords(self.tag, x1, ty)
        b = cv.bbox(self.tag)
        cv.coords(self.tag_bg, *b)
        cv.itemconfigure(self.tag_bg, state="normal")
        cv.tag_raise(self.tag_bg)
        cv.tag_raise(self.tag)

    def _hide_sel(self):
        self._dim(None)
        for i in (self.box, self.tag_bg, self.tag):
            self.cv.itemconfigure(i, state="hidden")

    def _window_at(self, x, y):
        sx, sy = x + self.vx, y + self.vy
        for l, t, r, b in self.windows:
            if l <= sx < r and t <= sy < b:
                return (max(l, self.vx), max(t, self.vy), min(r, self.vx + self.vw), min(b, self.vy + self.vh))
        return None

    def _motion(self, e):
        if self.mode != "window":
            return
        w = self._window_at(e.x, e.y)
        if w:
            self._show(w[0] - self.vx, w[1] - self.vy, w[2] - self.vx, w[3] - self.vy)
        else:
            self._hide_sel()

    def _press(self, e):
        if self.mode == "rect":
            self.start = (e.x, e.y)

    def _drag(self, e):
        if self.mode == "rect" and self.start:
            x1, y1 = self.start
            self._show(min(x1, e.x), min(y1, e.y), max(x1, e.x), max(y1, e.y))

    def _release(self, e):
        if self.mode == "window":
            w = self._window_at(e.x, e.y)
            if w:
                self._finish((w[0], w[1], w[2] - w[0], w[3] - w[1]))
            return
        if not self.start:
            return
        x1, y1 = self.start
        self.start = None
        x1, x2, y1, y2 = min(x1, e.x), max(x1, e.x), min(y1, e.y), max(y1, e.y)
        if x2 - x1 >= 3 and y2 - y1 >= 3:
            self._finish((self.vx + x1, self.vy + y1, x2 - x1, y2 - y1))
        else:
            self._hide_sel()


class Countdown:
    """Small 'Screenshot in 3...' pill at the top of the screen; calls done() right after it closes."""

    def __init__(self, root, theme, secs, done):
        self.root, self.n, self.done = root, secs, done
        p = theme.pal
        self.top = tk.Toplevel(root)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(bg=p["accent"])
        self.lab = tk.Label(self.top, bg=p["accent"], fg=p["accent_fg"], font=theme.font(20, "bold"),
                            padx=theme.s(26), pady=theme.s(10))
        self.lab.pack()
        self._tick()

    def _place(self):
        self.top.update_idletasks()
        w, h = self.top.winfo_reqwidth(), self.top.winfo_reqheight()
        self.top.geometry(f"+{(self.root.winfo_screenwidth() - w) // 2}+{int(self.root.winfo_screenheight() * 0.06)}")

    def _tick(self):
        if self.n <= 0:
            self.top.destroy()
            self.root.update()
            self.root.after(250, self.done)  # give the compositor time to drop the pill
            return
        self.lab.configure(text=f"Screenshot in {self.n}")
        self._place()
        self.n -= 1
        self.root.after(1000, self._tick)
