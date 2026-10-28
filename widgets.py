"""Rounded, animated widgets for Feitto (buttons, segmented control, switch, cards, inputs, menu, clip list).

Everything is drawn on Tk canvases so corners are round and state changes (hover, selection, toggles) are
animated. All colours come from the theme and are re-painted live when the theme changes.
"""
import ctypes
import math
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

import ui_kit as ui
from ui_kit import ICON, animate, lerp_color, rr

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]


# --------------------------------------------------------------------------- tooltip
class Tip:
    def __init__(self, widget, theme, text):
        self.w, self.th, self.text, self.tw, self.job = widget, theme, text, None, None
        widget.bind("<Enter>", self._enter, add="+")
        widget.bind("<Leave>", self._leave, add="+")
        widget.bind("<ButtonPress>", self._leave, add="+")

    def _enter(self, _):
        self.job = self.w.after(450, self._show)

    def _show(self):
        p = self.th.pal
        self.tw = tk.Toplevel(self.w)
        self.tw.wm_overrideredirect(True)
        self.tw.attributes("-topmost", True)
        tk.Label(self.tw, text=self.text, bg=p["surface2"], fg=p["text"], font=self.th.font(9), padx=10, pady=5,
                 highlightthickness=1, highlightbackground=p["border"]).pack()
        self.tw.geometry(f"+{self.w.winfo_rootx()}+{self.w.winfo_rooty() + self.w.winfo_height() + 8}")
        ui.round_window(self.tw, self.th)

    def _leave(self, _=None):
        if self.job:
            self.w.after_cancel(self.job)
            self.job = None
        if self.tw:
            self.tw.destroy()
            self.tw = None


# --------------------------------------------------------------------------- buttons
class Button(tk.Canvas):
    """Rounded button. accent=True -> filled call-to-action; otherwise a quiet outlined button."""

    def __init__(self, master, theme, text, command=None, accent=False, bgkey="bg", min_w=0, h=38, size=10):
        self.th, self._text, self.cmd, self.accent, self.k, self.down = theme, text, command, accent, 0.0, False
        self._font = tkfont.Font(family=theme.ui_family, size=size, weight="bold" if accent else "normal")
        self._min_w, self._h = theme.s(min_w), theme.s(h)
        super().__init__(master, height=self._h, highlightthickness=0, bd=0, cursor="hand2")
        self._fit()
        theme.track(self, bg=bgkey)
        theme.on_change(self, self.draw)
        self.bind("<Enter>", lambda e: self._hover(1.0))
        self.bind("<Leave>", lambda e: (self._hover(0.0), self._press(False)))
        self.bind("<ButtonPress-1>", lambda e: self._press(True))
        self.bind("<ButtonRelease-1>", self._release)
        self.draw()

    def _fit(self):
        self._wpx = max(self._min_w, self._font.measure(self._text) + self.th.s(40))
        tk.Canvas.configure(self, width=self._wpx)

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw, cnf = {**cnf, **kw}, None
        if not kw and cnf is None:
            return super().configure()
        if "text" in kw:
            self._text = kw.pop("text")
            self._fit()
            self.draw()
        if "command" in kw:
            self.cmd = kw.pop("command")
        return super().configure(**kw) if kw else None

    def cget(self, key):
        return self._text if key == "text" else super().cget(key)

    def invoke(self):
        if self.cmd:
            self.cmd()

    def _hover(self, target):
        start = self.k
        animate(self, 140, lambda t: self._set_k(start + (target - start) * t), key="hover")

    def _set_k(self, v):
        self.k = v
        self.draw()

    def _press(self, on):
        if self.down != on:
            self.down = on
            self.draw()

    def _release(self, e):
        was = self.down
        self._press(False)
        if was and 0 <= e.x <= self._wpx and 0 <= e.y <= self._h:
            self.invoke()

    def draw(self):
        p, s = self.th.pal, self.th.s
        self.delete("all")
        if self.accent:
            base, hov, fg, edge = p["accent"], p["accent_hover"], p["accent_fg"], ""
        else:
            base, hov, fg, edge = p["field"], p["hover"], p["text"], p["border"]
        fill = lerp_color(base, hov, self.k)
        off = 1 if self.down else 0
        rr(self, 1, 1, self._wpx - 1, self._h - 1, s(11), fill=fill, outline=edge, width=1)
        self.create_text(self._wpx / 2, self._h / 2 + off, text=self._text, fill=fg, font=self._font)


class IconButton(tk.Canvas):
    def __init__(self, master, theme, glyph, fallback, command, tip=None, bgkey="bg", size=12, accent=False, box=40):
        self.th, self.cmd, self.bgkey, self.accent, self.k = theme, command, bgkey, accent, 0.0
        self._glyph, self._fallback, self._size = glyph, fallback, size
        self._box = theme.s(box)
        super().__init__(master, width=self._box, height=self._box, highlightthickness=0, bd=0, cursor="hand2")
        theme.track(self, bg=bgkey)
        theme.on_change(self, self.draw)
        self.bind("<Enter>", lambda e: self._hover(1.0))
        self.bind("<Leave>", lambda e: self._hover(0.0))
        self.bind("<ButtonRelease-1>", lambda e: self.cmd() if 0 <= e.x <= self._box and 0 <= e.y <= self._box else None)
        self.draw()
        if tip:
            Tip(self, theme, tip)

    def set_glyph(self, glyph, fallback):
        self._glyph, self._fallback = glyph, fallback
        self.draw()

    def _hover(self, target):
        start = self.k
        animate(self, 140, lambda t: self._set_k(start + (target - start) * t), key="hover")

    def _set_k(self, v):
        self.k = v
        self.draw()

    def draw(self):
        p, s, B = self.th.pal, self.th.s, self._box
        self.delete("all")
        if self.accent:
            fill, fg = lerp_color(p["accent"], p["accent_hover"], self.k), p["accent_fg"]
            rr(self, 1, 1, B - 1, B - 1, s(18) if self._box >= s(50) else s(13), fill=fill, outline="")
        else:
            fg = lerp_color(p["muted"], p["text"], self.k)
            if self.k > 0.02:
                rr(self, 1, 1, B - 1, B - 1, s(11), fill=lerp_color(self.cget("bg"), p["hover"], self.k), outline="")
        if self.th.icon_family:
            self.create_text(B / 2, B / 2, text=self._glyph, fill=fg, font=self.th.icon_font(self._size))
        else:
            self.create_text(B / 2, B / 2, text=self._fallback, fill=fg, font=self.th.font(self._size))


class Badge(tk.Canvas):
    """Small rounded label, e.g. 'Disconnected'."""

    def __init__(self, master, theme, text, fillkey="danger", fgkey="accent_fg", bgkey="bg"):
        self.th, self._text, self.fillkey, self.fgkey = theme, text, fillkey, fgkey
        self._font = tkfont.Font(family=theme.ui_family, size=9, weight="bold")
        self._wpx, self._hpx = self._font.measure(text) + theme.s(22), theme.s(24)
        super().__init__(master, width=self._wpx, height=self._hpx, highlightthickness=0, bd=0)
        theme.track(self, bg=bgkey)
        theme.on_change(self, self.draw)
        self.draw()

    def draw(self):
        p = self.th.pal
        self.delete("all")
        rr(self, 0, 0, self._wpx, self._hpx, self._hpx / 2, fill=p[self.fillkey], outline="")
        self.create_text(self._wpx / 2, self._hpx / 2, text=self._text, fill=p[self.fgkey], font=self._font)


# --------------------------------------------------------------------------- switch + segmented
class Switch(tk.Canvas):
    def __init__(self, master, theme, variable, command=None, bgkey="bg"):
        self.th, self.var, self.cmd = theme, variable, command
        self.pos = 1.0 if variable.get() else 0.0
        super().__init__(master, width=theme.s(48), height=theme.s(28), highlightthickness=0, bd=0, cursor="hand2")
        theme.track(self, bg=bgkey)
        theme.on_change(self, self.draw)
        variable.trace_add("write", lambda *_: self._goto())
        self.bind("<Button-1>", self._click)
        self.draw()

    def _click(self, _):
        self.var.set(not self.var.get())
        if self.cmd:
            self.cmd()

    def _goto(self):
        start, target = self.pos, 1.0 if self.var.get() else 0.0
        animate(self, 170, lambda t: self._set(start + (target - start) * t), key="knob")

    def _set(self, v):
        self.pos = v
        self.draw()

    def draw(self):
        p, s = self.th.pal, self.th.s
        self.delete("all")
        w, h = s(48), s(28)
        rr(self, 1, 1, w - 1, h - 1, h / 2, fill=lerp_color(p["switch_off"], p["accent"], self.pos), outline="")
        d = h - s(10)
        x = s(5) + (w - s(10) - d) * self.pos
        self.create_oval(x, s(5), x + d, s(5) + d, fill=p["knob"], outline="")


class Segmented(tk.Canvas):
    """Pill-shaped choice with a thumb that slides to the selected option: Segmented(parent, theme, [(value, text)], StringVar)."""

    def __init__(self, master, theme, options, variable, command=None, compact=False, bgkey="bg"):
        self.th, self.opts, self.var, self.cmd, self.compact = theme, list(options), variable, command, compact
        self._font = tkfont.Font(family=theme.ui_family, size=9 if compact else 10)
        self._bold = tkfont.Font(family=theme.ui_family, size=9 if compact else 10, weight="bold")
        pad, self._inset = theme.s(14 if compact else 20), theme.s(4)
        self._h = theme.s(34 if compact else 40)
        self._ws = [max(self._font.measure(t), self._bold.measure(t)) + 2 * pad for _, t in self.opts]
        self._xs, x = [], self._inset
        for w in self._ws:
            self._xs.append(x)
            x += w
        self._wpx = x + self._inset
        self.hover = -1
        self.tx, self.tw = self._target()
        super().__init__(master, width=self._wpx, height=self._h, highlightthickness=0, bd=0, cursor="hand2")
        variable.trace_add("write", lambda *_: self._slide())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        self.bind("<Button-1>", self._click)
        theme.track(self, bg=bgkey)
        theme.on_change(self, self.draw)
        self.draw()

    def _index(self, value=None):
        value = self.var.get() if value is None else value
        for i, (v, _) in enumerate(self.opts):
            if v == value:
                return i
        return 0

    def _target(self):
        i = self._index()
        return self._xs[i], self._ws[i]

    def _slide(self):
        x0, w0 = self.tx, self.tw
        x1, w1 = self._target()
        animate(self, 200, lambda t: self._set_thumb(x0 + (x1 - x0) * t, w0 + (w1 - w0) * t), key="thumb")

    def _set_thumb(self, x, w):
        self.tx, self.tw = x, w
        self.draw()

    def _at(self, x):
        for i, (sx, w) in enumerate(zip(self._xs, self._ws)):
            if sx <= x < sx + w:
                return i
        return -1

    def _motion(self, e):
        self._set_hover(self._at(e.x))

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.draw()

    def _click(self, e):
        i = self._at(e.x)
        if i >= 0:
            self._pick(self.opts[i][0])

    def _pick(self, value):
        self.var.set(value)
        if self.cmd:
            self.cmd(value)

    def draw(self):
        p, s = self.th.pal, self.th.s
        self.delete("all")
        h, ins = self._h, self._inset
        rr(self, 1, 1, self._wpx - 1, h - 1, s(12 if self.compact else 13), fill=p["field"], outline=p["border"])
        sel = self._index()
        if 0 <= self.hover != sel:
            rr(self, self._xs[self.hover], ins, self._xs[self.hover] + self._ws[self.hover], h - ins, s(9),
               fill=p["hover"], outline="")
        rr(self, self.tx, ins, self.tx + self.tw, h - ins, s(9 if self.compact else 10), fill=p["accent"], outline="")
        for i, (sx, w) in enumerate(zip(self._xs, self._ws)):
            on = i == sel
            self.create_text(sx + w / 2, h / 2, text=self.opts[i][1], font=self._bold if on else self._font,
                             fill=p["accent_fg"] if on else p["text"])


# --------------------------------------------------------------------------- cards + inputs
class Card(tk.Canvas):
    """Rounded panel. Put children in card.inner."""

    def __init__(self, master, theme, fill="surface", border="border", radius=14, outer="bg", width=None, height=None):
        self.th, self.fillk, self.borderk, self.r = theme, fill, border, theme.s(radius)
        kw = {}
        if width:
            kw["width"] = width
        if height:
            kw["height"] = height
        super().__init__(master, highlightthickness=0, bd=0, **kw)
        self.inner = tk.Frame(self)
        theme.track(self.inner, bg=fill)
        theme.track(self, bg=outer)
        self.win = self.create_window(0, 0, anchor="nw", window=self.inner)
        theme.on_change(self, self.draw)
        self.bind("<Configure>", lambda e: self.draw())

    def draw(self):
        p = self.th.pal
        W, H = self.winfo_width(), self.winfo_height()
        if W < 6 or H < 6:
            return
        self.delete("shape")
        rr(self, 1, 1, W - 1, H - 1, self.r, fill=p[self.fillk], outline=p[self.borderk], tags="shape")
        self.tag_lower("shape")
        inset = int(math.ceil(0.3 * self.r)) + 2
        self.coords(self.win, inset, inset)
        self.itemconfigure(self.win, width=max(1, W - 2 * inset), height=max(1, H - 2 * inset))


class Entry(Card):
    """Rounded text input. .entry is the underlying tk.Entry."""

    def __init__(self, master, theme, textvariable=None, width=20, bgkey="bg", height=40):
        f = tkfont.Font(family=theme.ui_family, size=10)
        super().__init__(master, theme, fill="field", border="border", radius=11, outer=bgkey,
                         width=f.measure("0") * width + theme.s(26), height=theme.s(height))
        self.entry = tk.Entry(self.inner, textvariable=textvariable, relief="flat", bd=0, highlightthickness=0,
                              font=theme.font(10))
        theme.track(self.entry, bg="field", fg="text", insertbackground="text", selectbackground="sel",
                    selectforeground="text")
        self.entry.pack(fill="both", expand=True, padx=theme.s(8), pady=theme.s(4))
        self.entry.bind("<FocusIn>", lambda e: self._focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._focus(False))
        self.bind("<Button-1>", lambda e: self.entry.focus_set())

    def _focus(self, on):
        self.borderk = "accent" if on else "border"
        self.draw()

    def get(self):
        return self.entry.get()


class Dropdown(Card):
    """Rounded select box that opens our own menu. values: [(value, label)]."""

    def __init__(self, master, theme, values, variable, bgkey="bg", width=16, height=40):
        f = tkfont.Font(family=theme.ui_family, size=10)
        super().__init__(master, theme, fill="field", border="border", radius=11, outer=bgkey,
                         width=f.measure("0") * width + theme.s(40), height=theme.s(height))
        self.values, self.var, self.th_ = list(values), variable, theme
        self.lab = tk.Label(self.inner, anchor="w", font=theme.font(10), cursor="hand2")
        theme.track(self.lab, bg="field", fg="text")
        self.lab.pack(side="left", fill="both", expand=True, padx=(theme.s(10), 0))
        self.chev = tk.Label(self.inner, font=theme.icon_font(9) if theme.icon_family else theme.font(9),
                             text="" if theme.icon_family else "▾", cursor="hand2")
        theme.track(self.chev, bg="field", fg="muted")
        self.chev.pack(side="right", padx=(0, theme.s(10)))
        for w in (self, self.lab, self.chev):
            w.bind("<ButtonRelease-1>", lambda e: self._open())
        variable.trace_add("write", lambda *_: self._show())
        self._show()

    def set_values(self, values):
        self.values = list(values)
        self._show()

    def _show(self):
        cur = self.var.get()
        self.lab.configure(text=next((t for v, t in self.values if v == cur), cur))

    def _open(self):
        items = [{"label": t, "checked": v == self.var.get(), "cmd": (lambda v=v: self.var.set(v))}
                 for v, t in self.values]
        PopupMenu(self.winfo_toplevel(), self.th_, items, self.winfo_rootx(),
                  self.winfo_rooty() + self.winfo_height() + self.th_.s(4), min_width=self.winfo_width() / self.th_.scale)


# --------------------------------------------------------------------------- popup menu
class PopupMenu:
    """Rounded menu in the app's colours (the native Windows menu cannot follow our theme).

    items: [{"label", "cmd", "checked", "bold"}] or None for a separator. Opens at screen point (x, y),
    flips above the point if there is no room below (and above the taskbar). Esc, a click elsewhere or a
    pick closes it.
    """

    def __init__(self, root, theme, items, x, y, min_width=0, on_close=None):
        S, p = theme.s, theme.pal
        self.root, self.th, self.on_close, self.closed, self.hi, self.armed = root, theme, on_close, False, -1, False
        self.items = [dict(it) if it else {"sep": True} for it in items]
        f, fb = tkfont.Font(family=theme.ui_family, size=10), tkfont.Font(family=theme.ui_family, size=10, weight="bold")
        checks = any("checked" in it for it in self.items if not it.get("sep"))
        self.left = S(44) if checks else S(18)
        labels = [(fb if it.get("bold") else f).measure(it["label"]) for it in self.items if not it.get("sep")]
        W = max(S(min_width), max(labels or [0]) + self.left + S(26), S(190))
        self.rows, y0 = [], S(8)
        for it in self.items:
            h = S(14) if it.get("sep") else S(38)
            self.rows.append((y0, y0 + h))
            y0 += h
        H = y0 + S(8)
        self.W, self.H = W, H
        top = self.top = tk.Toplevel(root)
        top.withdraw()
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.attributes("-alpha", 0.0)
        top.configure(bg=p["surface"])
        cv = self.cv = tk.Canvas(top, width=W, height=H, bg=p["surface"], highlightthickness=0, bd=0)
        cv.pack()
        self.hv = cv.create_polygon(0, 0, 0, 0, 0, 0, fill=p["sel"], outline="", state="hidden", smooth=True)
        for i, (it, (ya, yb)) in enumerate(zip(self.items, self.rows)):
            if it.get("sep"):
                cv.create_line(S(16), (ya + yb) / 2, W - S(16), (ya + yb) / 2, fill=p["border"])
                continue
            if it.get("checked"):
                cv.create_text(S(24), (ya + yb) / 2, text=ICON["check"] if theme.icon_family else "✓",
                               fill=p["accent"], font=theme.icon_font(11) if theme.icon_family else theme.font(11, "bold"))
            cv.create_text(self.left, (ya + yb) / 2, anchor="w", text=it["label"], fill=p["text"],
                           font=fb if it.get("bold") else f)
        cv.bind("<Motion>", self._motion)
        cv.bind("<Leave>", lambda e: self._hover(-1))
        cv.bind("<ButtonRelease-1>", self._release)
        for k, d in (("<Up>", -1), ("<Down>", 1)):
            top.bind(k, lambda e, d=d: self._step(d))
        top.bind("<Return>", lambda e: self._pick(self.hi))
        top.bind("<Escape>", lambda e: self.close())

        l, t, r, b = ui._work_area(root)
        nx = max(l + S(8), min(int(x), r - W - S(8)))
        ny = int(y) if y + H + S(8) <= b else int(y - H)
        if y >= b - S(4):      # opened from the taskbar: sit just above it
            ny = b - H - S(8)
        ny = max(t + S(8), ny)
        top.geometry(f"{W}x{H}+{nx}+{ny + S(8)}")
        ui.round_window(top, theme)
        top.deiconify()
        top.lift()
        try:
            user32.SetForegroundWindow(top.winfo_id())
        except Exception:
            pass
        top.focus_force()
        self._rect = (nx, ny, nx + W, ny + H)
        animate(top, 140, lambda k: (top.attributes("-alpha", k), top.geometry(f"+{nx}+{int(ny + S(8) * (1 - k))}")),
                key="open")
        self._poll()

    # -- interaction
    def _row_at(self, y):
        for i, (ya, yb) in enumerate(self.rows):
            if ya <= y < yb and not self.items[i].get("sep"):
                return i
        return -1

    def _motion(self, e):
        self._hover(self._row_at(e.y))

    def _hover(self, i):
        if i == self.hi:
            return
        self.hi, S = i, self.th.s
        if i < 0:
            self.cv.itemconfigure(self.hv, state="hidden")
            return
        ya, yb = self.rows[i]
        pts = []
        # rounded rectangle polygon, re-used (smooth) so the highlight keeps round corners
        x1, y1, x2, y2, r = S(6), ya + S(2), self.W - S(6), yb - S(2), S(9)
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.cv.coords(self.hv, *pts)
        self.cv.itemconfigure(self.hv, state="normal")
        self.cv.tag_lower(self.hv)

    def _step(self, d):
        n = len(self.items)
        i = self.hi
        for _ in range(n):
            i = (i + d) % n if i >= 0 else (0 if d > 0 else n - 1)
            if not self.items[i].get("sep"):
                return self._hover(i)

    def _release(self, e):
        self._pick(self._row_at(e.y))

    def _pick(self, i):
        if i < 0 or self.items[i].get("sep"):
            return
        cmd = self.items[i].get("cmd")
        self.close()
        if cmd:
            self.root.after(20, cmd)

    def _poll(self):
        """Close when the mouse is pressed outside the menu (a borderless window gets no focus-out reliably)."""
        if self.closed:
            return
        try:
            down = any(user32.GetAsyncKeyState(k) & 0x8000 for k in (1, 2, 4))
            if not down:
                self.armed = True
            elif self.armed:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                l, t, r, b = self._rect
                if not (l <= pt.x < r and t <= pt.y < b):
                    return self.close()
            self.top.after(25, self._poll)
        except tk.TclError:
            pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.top.destroy()
        except tk.TclError:
            pass
        if self.on_close:
            self.on_close()


# --------------------------------------------------------------------------- tabs + search + list
class Tabs(tk.Frame):
    def __init__(self, master, theme, names):
        super().__init__(master)
        self.th = theme
        theme.track(self, bg="bg")
        head = tk.Frame(self)
        head.pack(fill="x", padx=theme.s(20), pady=(theme.s(14), 0))
        theme.track(head, bg="bg")
        self.body = tk.Frame(self)
        self.body.pack(fill="both", expand=True)
        theme.track(self.body, bg="bg")
        self.pages, self.tabs, self.current = {}, {}, None
        for n in names:
            cell = tk.Frame(head)
            cell.pack(side="left", padx=(0, theme.s(22)))
            lab = tk.Label(cell, text=n, font=theme.font(10, "bold"), cursor="hand2", pady=theme.s(6))
            lab.pack()
            bar = tk.Frame(cell, height=theme.s(3))
            bar.pack(fill="x")
            lab.bind("<Button-1>", lambda e, n=n: self.show(n))
            self.tabs[n] = (cell, lab, bar)
            page = tk.Frame(self.body)
            theme.track(page, bg="bg")
            self.pages[n] = page
        theme.on_change(self, self.retheme)
        self.show(names[0])

    def show(self, name):
        if self.current:
            self.pages[self.current].pack_forget()
        self.current = name
        self.pages[name].pack(fill="both", expand=True, padx=self.th.s(20), pady=self.th.s(8))
        self.retheme()

    def retheme(self):
        p = self.th.pal
        for n, (cell, lab, bar) in self.tabs.items():
            on = n == self.current
            cell.configure(bg=p["bg"])
            lab.configure(bg=p["bg"], fg=p["text"] if on else p["muted"])
            bar.configure(bg=p["accent"] if on else p["bg"])


class SearchBox(tk.Canvas):
    def __init__(self, master, theme, variable, placeholder, bgkey="bg"):
        self.th = theme
        h = theme.s(48)
        super().__init__(master, height=h, highlightthickness=0, bd=0)
        self.h, self.focused, self.var = h, False, variable
        theme.track(self, bg=bgkey)
        self.entry = tk.Entry(self, textvariable=variable, relief="flat", bd=0, highlightthickness=0,
                              font=theme.font(11))
        self.ph = tk.Label(self, text=placeholder, anchor="w", font=theme.font(11), cursor="xterm")
        self.ew = self.create_window(0, 0, window=self.entry, anchor="w")
        self.pw = self.create_window(0, 0, window=self.ph, anchor="w")
        self.ph.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda e: self._focus(True))
        self.entry.bind("<FocusOut>", lambda e: self._focus(False))
        variable.trace_add("write", lambda *_: self._placeholder())
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", lambda e: self.entry.focus_set())
        theme.on_change(self, self.draw)

    def _focus(self, on):
        self.focused = on
        self.draw()

    def _placeholder(self):
        self.itemconfigure(self.pw, state="hidden" if self.var.get() else "normal")

    def draw(self):
        p, s = self.th.pal, self.th.s
        W = self.winfo_width()
        if W < 20:
            return
        self.delete("shape")
        rr(self, 1, 1, W - 1, self.h - 1, s(16), fill=p["field"],
           outline=p["accent"] if self.focused else p["border"], width=2 if self.focused else 1, tags="shape")
        self.create_text(s(24), self.h / 2, text=ICON["search"] if self.th.icon_family else "⌕",
                         fill=p["accent"] if self.focused else p["muted"],
                         font=self.th.icon_font(11) if self.th.icon_family else self.th.font(12), tags="shape")
        self.tag_lower("shape")
        x0, ew = s(46), W - s(46) - s(18)
        self.coords(self.ew, x0, self.h / 2)
        self.itemconfigure(self.ew, width=ew, height=self.h - s(18))
        self.coords(self.pw, x0, self.h / 2)
        self.itemconfigure(self.pw, width=ew, height=self.h - s(18))
        self.entry.configure(bg=p["field"], fg=p["text"], insertbackground=p["text"],
                             selectbackground=p["sel"], selectforeground=p["text"])
        self.ph.configure(bg=p["field"], fg=p["muted"])
        self._placeholder()


class ClipList(tk.Canvas):
    """Scrolling list of clip cards. The selection pill glides to the chosen row; hovering tints a row."""

    def __init__(self, master, theme, on_select, on_activate, on_menu, on_focus):
        super().__init__(master, highlightthickness=0, bd=0, yscrollincrement=theme.s(20))
        self.th, self.items, self.sel, self.hover = theme, [], -1, -1
        self.on_select, self.on_activate, self.on_menu, self.on_focus = on_select, on_activate, on_menu, on_focus
        self.rh = theme.s(62)
        f = theme.ui_family
        self.f_title = tkfont.Font(family=f, size=11)
        self.f_sub = tkfont.Font(family=f, size=9)
        self.f_num = tkfont.Font(family=f, size=9, weight="bold")
        self.sel_y = 0.0
        theme.track(self, bg="surface")
        theme.on_change(self, self.redraw)
        self._width = 0
        self.bind("<Configure>", self._on_size)
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-1 * int(e.delta / 120) * 3, "units"))
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        self.bind("<Button-1>", self._click)
        self.bind("<Double-Button-1>", lambda e: self.on_activate())
        self.bind("<Button-3>", self._menu)

    # -- api
    def set_items(self, items):
        self.items = items
        self.sel = 0 if items else -1
        self.hover = -1
        self.sel_y = 0.0
        self.redraw()
        self.yview_moveto(0)
        self.on_select(self.sel)

    def select(self, i, notify=True):
        if not self.items:
            self.sel = -1
        else:
            old_y = self.sel_y
            self.sel = max(0, min(len(self.items) - 1, i))
            target = self.sel * self.rh
            if abs(target - old_y) > 8 * self.rh:      # far jump: do not sweep across the whole list
                self._move_sel(target)
            else:
                animate(self, 150, lambda t: self._move_sel(old_y + (target - old_y) * t), key="sel")
            self.see(self.sel)
        if notify:
            self.on_select(self.sel)

    def see(self, i):
        total = len(self.items) * self.rh
        if not total:
            return
        y1, y2 = i * self.rh, (i + 1) * self.rh
        top, hgt = self.canvasy(0), self.winfo_height()
        if y1 < top:
            self.yview_moveto(y1 / total)
        elif y2 > top + hgt:
            self.yview_moveto((y2 - hgt) / total)

    # -- events
    def _on_size(self, e):
        if e.width != self._width:
            self._width = e.width
            self.redraw()

    def _row_at(self, e):
        i = int(self.canvasy(e.y) // self.rh)
        return i if 0 <= i < len(self.items) else -1

    def _motion(self, e):
        self._set_hover(self._row_at(e))

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self._paint_hover()

    def _click(self, e):
        self.on_focus()
        i = self._row_at(e)
        if i >= 0:
            self.select(i)

    def _menu(self, e):
        i = self._row_at(e)
        if i >= 0:
            self.select(i)
        self.on_menu(e.x_root, e.y_root, i >= 0)

    # -- drawing
    def _geom(self, y):
        s, W = self.th.s, self._width or self.winfo_width() or self.th.s(420)
        return s(8), y + s(3), W - s(8), y + self.rh - s(3), s(11)

    def _move_sel(self, y):
        self.sel_y = y
        if self.sel < 0:
            return
        p, s = self.th.pal, self.th.s
        x1, y1, x2, y2, r = self._geom(y)
        self.delete("selbg", "selbar")
        rr(self, x1, y1, x2, y2, r, fill=p["sel"], outline="", tags="selbg")
        rr(self, x1 + s(2), y1 + s(13), x1 + s(6), y2 - s(13), s(2), fill=p["accent"], outline="", tags="selbar")
        self.tag_lower("selbar")
        self.tag_lower("selbg")

    def _paint_hover(self):
        self.delete("hoverbg")
        if 0 <= self.hover < len(self.items) and self.hover != self.sel:
            x1, y1, x2, y2, r = self._geom(self.hover * self.rh)
            rr(self, x1, y1, x2, y2, r, fill=self.th.pal["hover"], outline="", tags="hoverbg")
            self.tag_lower("hoverbg")

    def _fit(self, text, font, maxpx):
        text = text[:240]
        if font.measure(text) <= maxpx:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if font.measure(text[:mid] + "…") <= maxpx:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo] + "…"

    def redraw(self):
        p, s = self.th.pal, self.th.s
        self.delete("all")
        W = self._width or self.winfo_width() or s(420)
        rh, pad = self.rh, s(8)
        for i, it in enumerate(self.items):
            y = i * rh
            if it.get("num"):
                self.create_text(pad + s(28), y + rh / 2, text=str(it["num"]), fill=p["muted"], font=self.f_num)
            tx = pad + s(54)
            room = W - tx - pad - s(40)
            self.create_text(tx, y + rh / 2 - s(9), anchor="w", text=self._fit(it["title"], self.f_title, room),
                             fill=p["text"], font=self.f_title)
            self.create_text(tx, y + rh / 2 + s(12), anchor="w", text=self._fit(it["sub"], self.f_sub, room),
                             fill=p["muted"], font=self.f_sub)
            if it.get("pinned"):
                self.create_text(W - pad - s(22), y + rh / 2, text=ICON["pinned"] if self.th.icon_family else "★",
                                 fill=p["accent"],
                                 font=self.th.icon_font(11) if self.th.icon_family else self.th.font(11))
        self.configure(scrollregion=(0, 0, W, max(len(self.items) * rh, 1)))
        self._move_sel(self.sel * rh if self.sel >= 0 else 0.0)
        self._paint_hover()
