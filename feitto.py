"""Feitto - a portable clipboard history manager and screenshot tool for Windows.

  * capture : clipboard change detection, "disconnect" switch, max size, exclusions
  * storage : SQLite, Main (clip metadata) / Data (blob per format), CRC dedupe,
              expiry, "delete non used clips", "delete clip data", .fdb backups
  * hotkeys : activate, screenshot, save-current-clipboard, last-ten global paste hotkeys
  * paste   : restore previous foreground window, then inject Ctrl+V
  * tray    : icon with its own themed right-click menu, single instance, startup notice
  * screenshots : rectangle / window / full screen with delay, then mark up and copy

Everything lives next to this file in ./data, so the folder can be moved freely.
Standard library only.
"""
import ctypes
import datetime
import json
import os
import queue
import sqlite3
import struct
import sys
import threading
import time
import zlib
from ctypes import wintypes
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

APP = "Feitto"
BASE = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
import ui_kit as ui  # noqa: E402
import snip  # noqa: E402
import annotate  # noqa: E402
import icon  # noqa: E402
import widgets as wd  # noqa: E402
DATA_DIR = os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA_DIR, "clips.db")
CFG_PATH = os.path.join(DATA_DIR, "settings.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULTS = {
    "hotkey": "ctrl+alt+v",          # activate the popup
    "save_hotkey": "",               # save current clipboard (blank = none)
    "lastten_mods": "",              # e.g. "ctrl+shift" -> Ctrl+Shift+1..0 paste last ten copies
    "max_items": 500,
    "expire_days": 0,                # 0 = never
    "allow_duplicates": False,
    "move_to_top_on_paste": True,
    "hide_on_hotkey_if_visible": True,
    "paste_after_select": True,
    "max_clip_bytes": 0,             # 0 = no limit
    "popup_position": "cursor",      # cursor | center | previous
    "show_startup_message": True,
    "disconnected": False,
    "screenshot_hotkey": "ctrl+alt+s",  # opens the screenshot tool; "prtsc" works if Windows does not claim it
    "shot_mode": "rect",             # rect | window | full
    "shot_delay": 0,                 # seconds: 0, 3, 5 or 10
    "theme": "system",               # system | light | dark
    "db_path": "",                   # blank = data/clips.db (change needs a restart)
    "poll_ms": 250,
    "list_size": 300,
}

# --------------------------------------------------------------------------- win32
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

CF_UNICODETEXT, CF_DIB = 13, 8
GMEM_MOVEABLE = 2
WM_HOTKEY = 0x0312
MOD = {"alt": 1, "ctrl": 2, "shift": 4, "win": 8}
MOD_NOREPEAT = 0x4000
KEYEVENTF_KEYUP = 2

user32.GetClipboardData.restype = ctypes.c_void_p
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wintypes.DWORD, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]

# Password managers set this format to ask clipboard tools not to record the copy.
FMT_EXCLUDE = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")


def open_clipboard(retries=10):
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def read_clipboard():
    """Return ('text', str) / ('image', bytes) / None. Skips excluded content."""
    if not open_clipboard():
        return None
    try:
        if user32.IsClipboardFormatAvailable(FMT_EXCLUDE):
            return None
        for fmt, kind in ((CF_UNICODETEXT, "text"), (CF_DIB, "image")):
            if not user32.IsClipboardFormatAvailable(fmt):
                continue
            h = user32.GetClipboardData(fmt)
            if not h:
                continue
            p = kernel32.GlobalLock(h)
            if not p:
                continue
            try:
                if kind == "text":
                    return kind, ctypes.wstring_at(p)
                return kind, ctypes.string_at(p, kernel32.GlobalSize(h))
            finally:
                kernel32.GlobalUnlock(h)
        return None
    finally:
        user32.CloseClipboard()


def _put(fmt, payload):
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, payload, len(payload))
    kernel32.GlobalUnlock(h)
    if not user32.SetClipboardData(fmt, h):
        kernel32.GlobalFree(h)
        return False
    return True


def write_clipboard(kind, value):
    if not open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        if kind == "text":
            return _put(CF_UNICODETEXT, (value + "\0").encode("utf-16-le"))
        return _put(CF_DIB, value)
    finally:
        user32.CloseClipboard()


def send_ctrl_v():
    VK_CONTROL, VK_V = 0x11, 0x56
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


KEYNAMES = {"prtsc": 0x2C, "printscreen": 0x2C, "snapshot": 0x2C, "space": 0x20, "tab": 0x09,
            "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22}


def parse_hotkey(spec):
    mods, key = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in MOD:
            mods |= MOD[part]
        elif part in KEYNAMES:
            key = KEYNAMES[part]
        elif len(part) == 1:
            key = {"`": 0xC0}.get(part, ord(part.upper()))
        elif part.startswith("f") and part[1:].isdigit():
            key = 0x6F + int(part[1:])
    if key is None or (not mods and key != 0x2C):  # only PrtSc may be used on its own
        raise ValueError("hotkey needs at least one modifier and one key: " + spec)
    return mods | MOD_NOREPEAT, key


def hotkey_table(cfg):
    """[(id, hotkey spec, description, event)] for everything we register globally."""
    rows = [(1, cfg["hotkey"], "Activate (show Quick Paste)", "toggle")]
    if cfg["save_hotkey"].strip():
        rows.append((2, cfg["save_hotkey"], "Save current clipboard", "save_clip"))
    if cfg["screenshot_hotkey"].strip():
        rows.append((3, cfg["screenshot_hotkey"], "Screenshot tool", "screenshot"))
    mods = cfg["lastten_mods"].strip()
    if mods:
        for n in range(10):
            rows.append((10 + n, f"{mods}+{(n + 1) % 10}", f"Paste copy #{n + 1} (last ten)", ("last", n)))
    return rows


# --------------------------------------------------------------------------- start with Windows
def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw if os.path.exists(pyw) else sys.executable}" "{os.path.abspath(__file__)}"'


STARTUP_NAME = "Feitto"


def get_startup(name=STARTUP_NAME):
    if not winreg:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        return False


def set_startup(enabled, name=STARTUP_NAME):
    if not winreg:
        return
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass


# --------------------------------------------------------------------------- storage
MAIN_COLS = {"lID", "lDate", "mText", "lShortCut", "lDontAutoDelete", "CRC", "bIsGroup",
             "lParentID", "lastPasteDate"}
DATA_COLS = {"lID", "lParentID", "strClipBoardFormat", "ooData"}


def ms_to_text(v):
    if not v:
        return ""
    v = float(v)
    if v < 1e11:  # seconds (rows imported from an older database)
        v *= 1000
    return datetime.datetime.fromtimestamp(v / 1000).strftime("%Y-%m-%d %H:%M:%S")


def date_to_ms(text):
    text = text.strip()
    if not text:
        return None
    return int(time.mktime(time.strptime(text, "%Y-%m-%d")) * 1000)


class Store:
    def __init__(self, path, max_items):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.max_items = max_items
        self._init_schema()

    def _init_schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS Main(
                lID INTEGER PRIMARY KEY AUTOINCREMENT, lDate INTEGER, mText TEXT,
                lShortCut INTEGER DEFAULT 0, lDontAutoDelete INTEGER DEFAULT 0, CRC INTEGER,
                bIsGroup INTEGER DEFAULT 0, lParentID INTEGER DEFAULT 0, lastPasteDate INTEGER);
            CREATE TABLE IF NOT EXISTS Data(
                lID INTEGER PRIMARY KEY AUTOINCREMENT, lParentID INTEGER,
                strClipBoardFormat TEXT, ooData BLOB);
            CREATE INDEX IF NOT EXISTS Main_Date ON Main(lDate DESC);
            CREATE INDEX IF NOT EXISTS Main_CRC ON Main(CRC);
            CREATE INDEX IF NOT EXISTS Data_Parent ON Data(lParentID);
            CREATE TRIGGER IF NOT EXISTS del_data AFTER DELETE ON Main FOR EACH ROW
                BEGIN DELETE FROM Data WHERE lParentID = old.lID; END;
        """)
        self.db.commit()

    # -- capture ------------------------------------------------------------
    def add(self, kind, value, allow_duplicates=False):
        raw = value.encode("utf-16-le") if kind == "text" else value
        crc = zlib.crc32(raw)
        now = int(time.time() * 1000)
        row = None
        if not allow_duplicates:
            row = self.db.execute("SELECT lID FROM Main WHERE CRC=? AND bIsGroup=0 AND "
                                  "(mText IS ? OR ?='image') LIMIT 1",
                                  (crc, value if kind == "text" else None, kind)).fetchone()
        if row:  # duplicate: just move it back to the top
            self.db.execute("UPDATE Main SET lDate=? WHERE lID=?", (now, row[0]))
        else:
            label = value if kind == "text" else None
            cur = self.db.execute("INSERT INTO Main(lDate,mText,CRC) VALUES(?,?,?)", (now, label, crc))
            fmt = "CF_UNICODETEXT" if kind == "text" else "CF_DIB"
            self.db.execute("INSERT INTO Data(lParentID,strClipBoardFormat,ooData) VALUES(?,?,?)",
                            (cur.lastrowid, fmt, raw))
            self._trim()
        self.db.commit()

    def _trim(self):
        self.db.execute("""DELETE FROM Main WHERE lDontAutoDelete=0 AND bIsGroup=0 AND lID NOT IN
            (SELECT lID FROM Main WHERE lDontAutoDelete=0 AND bIsGroup=0 ORDER BY lDate DESC, lID DESC LIMIT ?)""",
                        (self.max_items,))

    def expire(self, days):
        """Expire unpinned clips after N days, judged on the last paste date."""
        if days <= 0:
            return 0
        cutoff = int((time.time() - days * 86400) * 1000)
        cur = self.db.execute(
            "DELETE FROM Main WHERE bIsGroup=0 AND COALESCE(lDontAutoDelete,0)=0 AND COALESCE(lShortCut,0)=0 "
            "AND COALESCE(lastPasteDate,lDate)<?", (cutoff,))
        self.db.commit()
        return cur.rowcount

    # -- reading ------------------------------------------------------------
    def list(self, term="", limit=300):
        sql = "SELECT lID,mText,lDontAutoDelete,lDate FROM Main WHERE bIsGroup=0"
        args = []
        for w in term.split():
            sql += " AND (mText LIKE ? ESCAPE '\\')"
            args.append("%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        sql += " ORDER BY COALESCE(lDontAutoDelete,0) DESC, lDate DESC, lID DESC LIMIT ?"
        return self.db.execute(sql, args + [limit]).fetchall()

    def recent(self, n):
        return [r[0] for r in self.db.execute(
            "SELECT lID FROM Main WHERE bIsGroup=0 ORDER BY lDate DESC, lID DESC LIMIT ?", (n,))]

    def payload(self, clip_id):
        rows = {r[0]: bytes(r[1]) for r in self.db.execute(
            "SELECT strClipBoardFormat,ooData FROM Data WHERE lParentID=?", (clip_id,))}
        if "CF_UNICODETEXT" in rows:
            return "text", rows["CF_UNICODETEXT"].decode("utf-16-le", "replace").rstrip("\0")
        if "CF_DIB" in rows:
            return "image", rows["CF_DIB"]
        return None

    def image_meta(self, clip_id):
        """(width, height, bytes) of a stored image clip."""
        r = self.db.execute("SELECT substr(ooData,1,12), length(ooData) FROM Data "
                            "WHERE lParentID=? AND strClipBoardFormat='CF_DIB'", (clip_id,)).fetchone()
        try:
            w, h = struct.unpack_from("<ii", bytes(r[0]), 4)
            return w, abs(h), r[1]
        except Exception:
            return 0, 0, 0

    # -- edits --------------------------------------------------------------
    def toggle_sticky(self, clip_id):
        self.db.execute("UPDATE Main SET lDontAutoDelete=1-COALESCE(lDontAutoDelete,0) WHERE lID=?", (clip_id,))
        self.db.commit()

    def delete(self, clip_id):
        self.db.execute("DELETE FROM Main WHERE lID=?", (clip_id,))
        self.db.commit()

    def touch_paste(self, clip_id, move_to_top=True):
        now = int(time.time() * 1000)
        self.db.execute("UPDATE Main SET lastPasteDate=?, lDate=CASE WHEN ? THEN ? ELSE lDate END WHERE lID=?",
                        (now, 1 if move_to_top else 0, now, clip_id))
        self.db.commit()

    def delete_unused(self):
        """Delete All Non Used Clips: everything with no shortcut, group or pin."""
        cur = self.db.execute(
            "DELETE FROM Main WHERE bIsGroup=0 AND COALESCE(lShortCut,0)=0 AND COALESCE(lParentID,0)<=0 "
            "AND COALESCE(lDontAutoDelete,0)=0")
        self.db.commit()
        return cur.rowcount

    # -- Delete Clip Data dialog ----------------------------------------------
    def formats(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT strClipBoardFormat FROM Data ORDER BY 1")]

    def data_rows(self, title="", fmt="", created=(None, None), used=(None, None), limit=2000):
        sql = ("SELECT m.lID, m.mText, m.lDate, m.lastPasteDate, d.lID, d.strClipBoardFormat, length(d.ooData) "
               "FROM Data d JOIN Main m ON m.lID=d.lParentID WHERE 1=1")
        args = []
        if title.strip():
            sql += " AND m.mText LIKE ? ESCAPE '\\'"
            args.append("%" + title.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        if fmt:
            sql += " AND d.strClipBoardFormat=?"
            args.append(fmt)
        for col, (lo, hi) in (("m.lDate", created), ("COALESCE(m.lastPasteDate,m.lDate)", used)):
            if lo is not None:
                sql += f" AND {col}>=?"
                args.append(lo)
            if hi is not None:
                sql += f" AND {col}<?"
                args.append(hi + 86400000)  # include the whole 'to' day
        sql += " ORDER BY length(d.ooData) DESC LIMIT ?"
        return self.db.execute(sql, args + [limit]).fetchall()

    def delete_data(self, data_ids):
        ids = list(data_ids)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            self.db.execute(f"DELETE FROM Data WHERE lID IN ({','.join('?' * len(chunk))})", chunk)
        # a clip left with no data is useless: remove those too
        self.db.execute("DELETE FROM Main WHERE bIsGroup=0 AND lID NOT IN (SELECT lParentID FROM Data)")
        self.db.commit()

    # -- Backup / Restore ------------------------------------------------------
    def backup(self, path):
        self.db.commit()
        dst = sqlite3.connect(path)
        try:
            self.db.backup(dst)
        finally:
            dst.close()

    def restore(self, path):
        src = sqlite3.connect(path)
        try:
            have_main = {r[1] for r in src.execute("PRAGMA table_info(Main)")}
            have_data = {r[1] for r in src.execute("PRAGMA table_info(Data)")}
            if not (MAIN_COLS <= have_main and DATA_COLS <= have_data):
                raise ValueError("This file is not a Feitto clips database (missing Main/Data columns).")
            safety = os.path.join(os.path.dirname(os.path.abspath(self.path)),
                                  time.strftime("pre-restore-%Y%m%d-%H%M%S.fdb"))
            self.backup(safety)  # never lose the current history to a restore
            src.backup(self.db)
        finally:
            src.close()
        self._init_schema()
        return safety

    def clear_all(self):
        self.db.execute("DELETE FROM Main")
        self.db.commit()


# --------------------------------------------------------------------------- tray
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_SHOW_EXISTING = WM_APP + 2
WM_RELOAD_HOTKEYS = WM_APP + 10
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP, WM_NULL = 0x202, 0x203, 0x205, 0
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 0x10
MF_CHECKED, MF_SEPARATOR = 0x8, 0x800
TRAY_CLASS = "FeittoTrayWnd"
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
                ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
                ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateIconIndirect.restype = wintypes.HICON
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  wintypes.HWND, wintypes.LPVOID]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, wintypes.LPVOID]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


# command id -> (label, event), in the order shown in the tray menu.
MENU = [
    (1, "Show Quick Paste", "open"), (2, "Options", "options"), None,
    (3, "Show Startup Message", "toggle_startmsg"), (4, "Global Hot Keys", "hotkeys"),
    (5, "Delete Clip Data", "delete_data"), (6, "Delete All Non Used Clips", "delete_unused"), None,
    (7, "Backup Database", "backup"), (8, "Restore Database", "restore"), None,
    (9, "Import Clip(s)", "import"), (10, "New Clip", "new_clip"), (15, "Take Screenshot\u2026", "screenshot"), None,
    (11, "Help", "help"), None,
    (12, "Disconnect from Clipboard.", "toggle_connect"), (13, "Save Current Clipboard", "save_clip"), None,
    (14, "Exit", "quit"),
]


class Tray:
    """Notification-area icon on its own thread. Left click opens, right click shows the menu."""

    def __init__(self, events, state):
        self.events = events
        self.state = state  # callable -> {"disconnected": bool, "startmsg": bool}
        self.hwnd = None
        self.ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self.ready.wait(5)

    def _add(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(nid)
        nid.hWnd, nid.uID, nid.uCallbackMessage = self.hwnd, 1, WM_TRAY
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.hIcon, nid.szTip = self.icon, APP
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self.nid = nid

    def set_tip(self, text):
        if self.hwnd:
            self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            self.nid.szTip = text[:127]
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def _run(self):
        taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")

        def proc(hwnd, msg, wp, lp):
            if msg == WM_TRAY:
                if lp in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.events.put("open")
                elif lp == WM_RBUTTONUP:
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    self.events.put(("tray_menu", pt.x, pt.y))
                return 0
            if msg == WM_SHOW_EXISTING:
                self.events.put("open")
                return 0
            if msg == taskbar_created:  # explorer.exe restarted: put the icon back
                self._add()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wp, lp)

        self._proc = WNDPROC(proc)  # keep a reference or it gets garbage collected
        wc = WNDCLASSW()
        wc.lpfnWndProc, wc.lpszClassName = self._proc, TRAY_CLASS
        wc.hInstance = kernel32.GetModuleHandleW(None)
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, TRAY_CLASS, APP, 0, 0, 0, 0, 0, None, None, wc.hInstance, None)
        self.icon = icon.make_hicon(icon.tray_icon_size())
        self._add()
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def remove(self):
        if self.hwnd:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))


def notify_running_instance():
    hwnd = user32.FindWindowW(TRAY_CLASS, None)
    if hwnd:
        user32.PostMessageW(hwnd, WM_SHOW_EXISTING, 0, 0)


# --------------------------------------------------------------------------- UI
HELP_TEXT = f"""{APP} - quick reference

Open the history      hotkey (default Ctrl+Alt+V) or left-click the tray icon
Paste                 Enter / double-click   (or Ctrl+1..9 for the first nine)
Search                just start typing in the box at the top
Pin a clip            Ctrl+S  (pinned clips are never auto-deleted and stay at the top)
Delete a clip         Del (when the search box is empty) or Ctrl+D
Theme                 sun/moon button, or Options > Appearance (System, Light, Dark)
Screenshot            camera button at the bottom (Mode and Delay are next to it)
                      Ctrl+Alt+S or the tray menu capture at once with those settings
Mark up a shot        Pen / Arrow / Rectangle / Text in red, yellow, green or blue.
                      Closing the editor copies the picture, with your marks, to the clipboard
Close the window      Esc  -  the app keeps running in the tray

Tray menu (right-click the icon)
  Show Quick Paste            open the history window
  Options                     history size, expiry, hotkeys, theme, start with Windows...
  Show Startup Message        notice at start saying the app runs in the tray
  Global Hot Keys             every hotkey the app registered, and whether it worked
  Delete Clip Data            find clips by title/date/format/size and delete their data
  Delete All Non Used Clips   delete every clip that is not pinned
  Backup / Restore Database   save or load a .fdb copy of your history
  Import Clip(s)              turn text files into clips
  New Clip                    type a clip by hand
  Take Screenshot...          capture now with the Mode and Delay set in the main window
  Disconnect from Clipboard.  pause recording; click again to reconnect
  Save Current Clipboard      store what is on the clipboard now, even when disconnected
  Exit                        really quit

Data lives in the 'data' folder next to the program (settings.json, clips.db).
"""


def ago(ms):
    if not ms:
        return ""
    v = float(ms)
    v = v * 1000 if v < 1e11 else v
    secs = max(0, time.time() - v / 1000)
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    if secs < 172800:
        return "yesterday"
    return datetime.datetime.fromtimestamp(v / 1000).strftime("%b %d")


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


class App:
    def __init__(self):
        self.cfg = dict(DEFAULTS)
        try:
            with open(CFG_PATH, encoding="utf-8") as f:
                self.cfg.update(json.load(f))
        except FileNotFoundError:
            self.save_cfg()
        except (OSError, ValueError):
            pass
        self.store = Store(self.cfg["db_path"] or DB_PATH, self.cfg["max_items"])
        self.root = tk.Tk()
        self.root.withdraw()
        self.theme = ui.Theme(self.root, self.cfg["theme"])
        self.S = self.theme.s
        self._toast_img = None
        ico = icon.ensure_ico(os.path.join(BASE, "feitto.ico"))
        if ico:  # title bars, taskbar and Alt+Tab show Feitto's icon instead of Python's
            try:
                self.root.iconbitmap(default=ico)
            except tk.TclError:
                pass
        self.mono = "Cascadia Mono" if "Cascadia Mono" in tkfont.families(self.root) else "Consolas"
        self.rows = []
        self.target = None
        self.last_pos = None
        self.dialogs = {}
        self._menu_open = False
        self._tray_menu = None
        self._img = None
        self.last_seq = user32.GetClipboardSequenceNumber()
        self.ignore_seq = None
        self.events = queue.Queue()
        self.hk_status = {}
        self.hk_tid = None
        self._build_popup()
        self._start_hotkeys()
        self.tray = Tray(self.events, lambda: {"disconnected": self.cfg["disconnected"],
                                               "startmsg": self.cfg["show_startup_message"]})
        self._update_disconnected_ui()
        self._after_theme()
        self.theme.on_change(self.pop, self._after_theme)
        self.store.expire(self.cfg["expire_days"])
        self.root.after(self.cfg["poll_ms"], self._poll)
        self.root.after(100, self._drain)
        self.root.after(3600 * 1000, self._hourly)
        if self.cfg["show_startup_message"]:
            self.root.after(800, lambda: self.toast(
                "Running minimized. Open it with the hotkey or by clicking the tray icon."))

    def toast(self, text, title=APP):
        if self._toast_img is None:
            n = self.S(40)
            self._toast_img = ui.png_photo(icon.png_rgba(icon.render_icon(n), n, n))
        ui.show_toast(self.root, self.theme, title, text, self._toast_img)

    def save_cfg(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2)

    # ---- small ui helpers -------------------------------------------------
    def _frame(self, parent, bg="bg"):
        f = tk.Frame(parent)
        self.theme.track(f, bg=bg)
        return f

    def _label(self, parent, text, muted=False, size=10, bold=False, bg="bg"):
        l = tk.Label(parent, text=text, anchor="w", justify="left", font=self.theme.font(size, "bold" if bold else "normal"))
        self.theme.track(l, bg=bg, fg="muted" if muted else "text")
        return l

    def _card(self, parent, bg="surface", radius=16, outer="bg", **kw):
        """Rounded panel; put children in card.inner."""
        return wd.Card(parent, self.theme, fill=bg, radius=radius, outer=outer, **kw)
    # ---- popup -----------------------------------------------------------
    def _build_popup(self):
        th, S = self.theme, self.S
        w = self.pop = tk.Toplevel(self.root)
        w.title(APP)
        w.geometry(f"{S(960)}x{S(660)}")
        w.minsize(S(820), S(540))
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", self.hide)
        w.withdraw()
        th.track(w, bg="bg")
        outer = self._frame(w)
        outer.pack(fill="both", expand=True, padx=S(22), pady=S(18))
        I = ui.ICON

        # header: search + quick actions
        head = self._frame(outer)
        head.pack(fill="x")
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *_: self.refresh())
        self.search = wd.SearchBox(head, th, self.q, "Search clipboard history")
        self.search.pack(side="left", fill="x", expand=True)
        self.entry = self.search.entry
        icons = self._frame(head)
        icons.pack(side="left", padx=(S(12), 0))
        for glyph, alt, cmd, tip, attr in ((I["pin"], "★", self.pin, "Pin / unpin  (Ctrl+S)", None),
                                            (I["delete"], "✕", self.delete, "Delete  (Del)", None),
                                            (I["sun"], "☼", self.toggle_theme, "Switch light / dark", "b_theme"),
                                            (I["settings"], "⚙", self.options, "Options", None)):
            b = wd.IconButton(icons, th, glyph, alt, cmd, tip=tip)
            b.pack(side="left", padx=(S(2), 0), pady=S(4))
            if attr:
                setattr(self, attr, b)

        # footer (packed first so it always keeps its space), then the capture dock above it
        foot = self._frame(outer)
        foot.pack(side="bottom", fill="x", pady=(S(2), 0))
        self.hint = self._label(foot, "Enter paste   ·   Ctrl+1–9 quick paste   ·   Ctrl+S pin   ·   "
                                      "Del delete   ·   Esc close", muted=True, size=9)
        self.hint.pack(side="left")
        self.count = self._label(foot, "", muted=True, size=9)
        self.count.pack(side="right")
        self.pill = wd.Badge(foot, th, "Disconnected")

        dock = self._card(outer, radius=20, height=S(96))
        dock.pack(side="bottom", fill="x", pady=(S(12), S(10)))
        row = dock.inner
        self.shot_mode = tk.StringVar(value=self.cfg["shot_mode"])
        self.shot_delay = tk.StringVar(value=str(self.cfg["shot_delay"]))
        self.shot_mode.trace_add("write", lambda *_: self._save_shot_prefs())
        self.shot_delay.trace_add("write", lambda *_: self._save_shot_prefs())
        cam = wd.IconButton(row, th, I["camera"], "▣", self.quick_screenshot, size=17, accent=True,
                            bgkey="surface", box=56, tip="Capture screenshot  (Ctrl+Alt+S)")
        cam.pack(side="left", padx=(S(12), S(20)), pady=S(8))

        def group(title, options, var):
            g = self._frame(row, bg="surface")
            g.pack(side="left", padx=(0, S(26)))
            self._label(g, title, muted=True, size=8, bold=True, bg="surface").pack(anchor="w", pady=(S(14), S(4)))
            wd.Segmented(g, th, options, var, compact=True, bgkey="surface").pack(anchor="w")

        group("MODE", self.SHOT_MODES, self.shot_mode)
        group("DELAY", self.SHOT_DELAYS, self.shot_delay)
        self._label(row, "Ctrl+Alt+S", muted=True, size=9, bg="surface").pack(side="right", padx=(0, S(20)))

        # body: clip list + preview, as two rounded panels
        body = self._frame(outer)
        body.pack(fill="both", expand=True, pady=(S(16), 0))
        body.columnconfigure(0, weight=11, uniform="c")
        body.columnconfigure(1, weight=9, uniform="c")
        body.rowconfigure(0, weight=1)
        left = self._card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, S(14)))
        sb = ttk.Scrollbar(left.inner, orient="vertical", style="Slim.Vertical.TScrollbar")
        self.clips = wd.ClipList(left.inner, th, self._on_select, self.paste, self._popup_menu,
                                 lambda: self.entry.focus_set())
        self.clips.configure(yscrollcommand=sb.set)
        sb.configure(command=self.clips.yview)
        sb.pack(side="right", fill="y", padx=(0, S(2)), pady=S(4))
        self.clips.pack(side="left", fill="both", expand=True)

        card = self._card(body)
        card.grid(row=0, column=1, sticky="nsew")
        pv = card.inner
        self.pv_head = self._label(pv, "", muted=True, size=9, bg="surface")
        self.pv_head.pack(fill="x", padx=S(16), pady=(S(12), S(4)))
        self.pv_meta = self._label(pv, "", muted=True, size=9, bg="surface")
        self.pv_meta.pack(side="bottom", fill="x", padx=S(16), pady=(S(4), S(10)))
        self.pv_text = tk.Text(pv, wrap="word", relief="flat", bd=0, highlightthickness=0, padx=S(16), pady=S(4),
                               font=(self.mono, 10), state="disabled", cursor="arrow", spacing3=S(2))
        th.track(self.pv_text, bg="surface", fg="text", insertbackground="text", selectbackground="sel",
                 selectforeground="text")
        self.pv_img = tk.Label(pv)
        th.track(self.pv_img, bg="surface", fg="muted")
        self.pv_text.pack(fill="both", expand=True)
        self._pane_w = 0
        pv.bind("<Configure>", self._on_pane_resize)

        w.bind("<Return>", lambda e: self.paste())
        w.bind("<Escape>", lambda e: self.hide())
        w.bind("<Control-s>", lambda e: self.pin())
        w.bind("<Control-d>", lambda e: self.delete())
        w.bind("<Delete>", self._on_delete_key)
        w.bind("<Down>", lambda e: self._move(1))
        w.bind("<Up>", lambda e: self._move(-1))
        w.bind("<Next>", lambda e: self._move(6))
        w.bind("<Prior>", lambda e: self._move(-6))
        for n in range(1, 10):
            w.bind(f"<Control-Key-{n}>", lambda e, n=n: self.paste(n - 1))
        w.bind("<FocusOut>", lambda e: self.root.after(150, self._maybe_hide))

    def _after_theme(self):
        dark = self.theme.dark
        self.b_theme.set_glyph(ui.ICON["sun"] if dark else ui.ICON["moon"], "☼" if dark else "☾")
        for win in [self.pop] + [d for d in self.dialogs.values() if d.winfo_exists()]:
            if win.winfo_viewable():
                self.theme.decorate(win)

    def toggle_theme(self):
        self.cfg["theme"] = "light" if self.theme.dark else "dark"
        self.save_cfg()
        self.theme.set_mode(self.cfg["theme"])
        self.entry.focus_set()

    def _popup_menu(self, x, y, on_row):
        items = []
        if on_row:
            items += [{"label": "Paste", "cmd": self.paste, "bold": True},
                      {"label": "Pin / unpin", "cmd": self.pin},
                      {"label": "Delete", "cmd": self.delete}, None]
        items += [{"label": "Delete all non used clips…", "cmd": self.delete_unused},
                  {"label": "Options…", "cmd": self.options}]
        self._menu_open = True
        wd.PopupMenu(self.root, self.theme, items, x, y, on_close=self._menu_closed)

    def _menu_closed(self):
        self._menu_open = False
        if self.visible():
            self.pop.focus_force()
            self.entry.focus_set()

    def show_tray_menu(self, x, y):
        """Right-click on the tray icon: our own themed menu instead of the (unthemeable) native one."""
        if self._tray_menu is not None and not self._tray_menu.closed:
            self._tray_menu.close()
        items = []
        for m in MENU:
            if m is None:
                items.append(None)
                continue
            cid, label, ev = m
            it = {"label": label, "cmd": (lambda e=ev: self._handle(e))}
            if cid == 1:
                it["bold"] = True
            if cid == 3:
                it["checked"] = self.cfg["show_startup_message"]
            if cid == 12 and self.cfg["disconnected"]:
                it["label"] = "Connect to Clipboard"
            items.append(it)
        self._tray_menu = wd.PopupMenu(self.root, self.theme, items, x, y)

    def _on_delete_key(self, _e):
        if not self.q.get() and not self.entry.selection_present():
            self.delete()

    def _move(self, d):
        self.clips.select((self.clips.sel + d) if self.clips.sel >= 0 else 0)
        return "break"

    def refresh(self):
        self.rows = self.store.list(self.q.get(), self.cfg["list_size"])
        items = []
        for n, (cid, text, sticky, date) in enumerate(self.rows):
            if text is None:
                w, h, size = self.store.image_meta(cid)
                title, sub = "Image", f"{ago(date)}  ·  {w}×{h}  ·  {human(size)}"
            else:
                first = next((ln.strip() for ln in text[:2000].splitlines() if ln.strip()), "")
                title = first or "(whitespace)"
                lines = text.count("\n") + 1
                sub = f"{ago(date)}  ·  {len(text):,} chars" + (f"  ·  {lines} lines" if lines > 1 else "")
            items.append(dict(id=cid, title=title, sub=sub, pinned=bool(sticky), num=n + 1 if n < 9 else None))
        self.clips.set_items(items)
        self.count.configure(text=f"{len(items)} clip{'s' if len(items) != 1 else ''}")

    def _select_id(self, cid):
        for i, r in enumerate(self.rows):
            if r[0] == cid:
                self.clips.select(i)
                return

    def _on_select(self, i):
        self.preview()

    def preview(self):
        i, S = self.clips.sel, self.S
        self._img = None
        self.pv_img.pack_forget()
        self.pv_text.pack_forget()
        self.pv_text.pack(fill="both", expand=True)
        self.pv_text.config(state="normal")
        self.pv_text.delete("1.0", "end")
        if i < 0 or i >= len(self.rows):
            self.pv_head.configure(text="")
            self.pv_meta.configure(text="")
            self.pv_text.insert("1.0", "Nothing here yet.\n\nCopy something and it will show up in this list.")
        else:
            cid, text, sticky, date = self.rows[i]
            self.pv_head.configure(text=("Pinned  ·  " if sticky else "") + ms_to_text(date))
            if text is not None:
                self.pv_text.insert("1.0", text[:20000] + ("\n…" if len(text) > 20000 else ""))
                n_lines = text.count("\n") + 1
                self.pv_meta.configure(text=f"{len(text):,} characters  ·  {n_lines:,} line{'s' if n_lines != 1 else ''}")
            else:
                w, h, size = self.store.image_meta(cid)
                self.pv_meta.configure(text=f"{w} × {h} px  ·  {human(size)}")
                self._img = self._fit_photo(self.store.payload(cid)[1])
                if self._img:
                    self.pv_text.pack_forget()
                    self.pv_img.configure(image=self._img)
                    self.pv_img.pack(fill="both", expand=True, padx=S(16), pady=S(8))
                else:
                    self.pv_text.insert("1.0", "Preview is not available for this image format.")
        self.pv_text.config(state="disabled")

    def _fit_photo(self, dib):
        """The picture scaled (smoothly) to fit the preview pane, or None for formats we cannot decode."""
        S = self.S
        pane = self.pv_text.master
        max_w = max(S(160), pane.winfo_width() - 2 * S(20))
        max_h = max(S(120), pane.winfo_height() - S(96))
        try:
            bgra, w, h = ui.dib_to_bgra(dib)
            sc = min(1.0, max_w / w, max_h / h)
            if sc < 1.0:
                dw, dh = max(1, int(w * sc)), max(1, int(h * sc))
                bgra, w, h = annotate.scale_bgra(bgra, w, h, dw, dh), dw, dh
            return tk.PhotoImage(data=snip.bgra_to_ppm(bgra, w, h))
        except (ValueError, TypeError, tk.TclError):
            return None

    def _on_pane_resize(self, e):
        if self._img is not None and abs(e.width - self._pane_w) > self.S(12):
            self._pane_w = e.width
            self.root.after_idle(self.preview)

    def visible(self):
        return self.pop.state() == "normal"

    def show(self):
        self.theme.refresh_system()
        self.target = user32.GetForegroundWindow()
        self.q.set("")
        self.refresh()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        W = self.pop.winfo_width() if self.pop.winfo_width() > 100 else self.S(960)
        H = self.pop.winfo_height() if self.pop.winfo_height() > 100 else self.S(660)
        mode = self.cfg["popup_position"]
        if mode == "center":
            x, y = (sw - W) // 2, (sh - H) // 2
        elif mode == "previous" and self.last_pos:
            x, y = self.last_pos
        else:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        self.pop.geometry(f"+{max(0, min(x, sw - W))}+{max(0, min(y, sh - H - 40))}")
        self.pop.attributes("-alpha", 0.0)
        self.pop.deiconify()
        self.theme.decorate(self.pop)
        self.pop.update_idletasks()
        self.preview()
        self.pop.lift()
        self.pop.focus_force()
        self.entry.focus_set()
        ui.fade_in(self.pop, 150)

    def hide(self):
        if self.visible():
            self.last_pos = (self.pop.winfo_x(), self.pop.winfo_y())
        self.pop.withdraw()

    def _maybe_hide(self):
        if not self.visible() or self._menu_open:
            return
        f = self.pop.focus_get()  # widget that has focus anywhere in this app, or None
        if f is None or f.winfo_toplevel() is not self.pop:
            self.hide()

    # ---- actions ---------------------------------------------------------
    def paste_clip(self, cid, target, refocus):
        data = self.store.payload(cid)
        if not data:
            return
        write_clipboard(*data)
        self.ignore_seq = user32.GetClipboardSequenceNumber()
        self.store.touch_paste(cid, self.cfg["move_to_top_on_paste"])
        if self.cfg["paste_after_select"] and target and user32.IsWindow(target):
            if refocus:
                user32.SetForegroundWindow(target)
                time.sleep(0.12)
            send_ctrl_v()

    def paste(self, index=None):
        if index is None:
            index = self.clips.sel
        if index < 0 or index >= len(self.rows):
            return "break"
        cid = self.rows[index][0]
        self.hide()
        self.root.update()
        self.paste_clip(cid, self.target, True)
        return "break"

    def pin(self):
        i = self.clips.sel
        if 0 <= i < len(self.rows):
            cid = self.rows[i][0]
            self.store.toggle_sticky(cid)
            self.refresh()
            self._select_id(cid)
        return "break"

    def delete(self):
        i = self.clips.sel
        if 0 <= i < len(self.rows):
            self.store.delete(self.rows[i][0])
            self.refresh()
            self.clips.select(i)
        return "break"

    def delete_unused(self):
        if messagebox.askyesno(APP, "Delete all clips that are not pinned?\n\n"
                                    "(Pinned clips and clips with shortcuts are kept.)", parent=self._parent()):
            n = self.store.delete_unused()
            if self.visible():
                self.refresh()
            self.toast(f"Deleted {n} clip{'s' if n != 1 else ''}.")

    def _parent(self):
        return self.pop if self.visible() else self.root

    def quit(self):
        self.root.quit()

    # ---- tray-menu commands ------------------------------------------------
    def toggle_connect(self):
        self.cfg["disconnected"] = not self.cfg["disconnected"]
        self.last_seq = user32.GetClipboardSequenceNumber()  # do not record what was copied meanwhile
        self.save_cfg()
        self._update_disconnected_ui()

    def _update_disconnected_ui(self):
        off = self.cfg["disconnected"]
        self.pop.title(APP + (" [Disconnected]" if off else ""))
        if off:
            self.pill.pack(side="right", padx=(0, self.S(10)))
        else:
            self.pill.pack_forget()
        if getattr(self, "tray", None):
            self.tray.set_tip(APP + (" [Disconnected]" if off else ""))

    def toggle_startmsg(self):
        self.cfg["show_startup_message"] = not self.cfg["show_startup_message"]
        self.save_cfg()

    def save_current_clipboard(self):
        item = read_clipboard()
        if not item or (item[0] == "text" and not item[1].strip()):
            self.toast("Nothing to save: the clipboard has no text or image.")
            return
        self.store.add(item[0], item[1], self.cfg["allow_duplicates"])
        self.last_seq = user32.GetClipboardSequenceNumber()
        if self.visible():
            self.refresh()
        self.toast("Current clipboard saved.")

    def backup_db(self):
        path = filedialog.asksaveasfilename(
            title="Backup database", defaultextension=".fdb", initialfile=time.strftime("clips-%Y%m%d.fdb"),
            filetypes=[("Feitto backups (.fdb)", "*.fdb"), ("All files", "*.*")])
        if path:
            try:
                self.store.backup(path)
                messagebox.showinfo(APP, "Backup saved:\n" + path)
            except Exception as e:
                messagebox.showerror(APP, f"Backup failed: {e}")

    def restore_db(self):
        path = filedialog.askopenfilename(
            title="Restore database", filetypes=[("Feitto backups (.fdb)", "*.fdb"), ("All files", "*.*")])
        if not path:
            return
        if not messagebox.askyesno(APP, "Replace the current history with this backup?\n\n"
                                        "The current history is saved first as pre-restore-*.fdb in the data folder."):
            return
        try:
            safety = self.store.restore(path)
            if self.visible():
                self.refresh()
            messagebox.showinfo(APP, "Database restored.\nPrevious history kept in:\n" + safety)
        except Exception as e:
            messagebox.showerror(APP, f"Restore failed: {e}")

    def import_clips(self):
        paths = filedialog.askopenfilenames(
            title="Import clip(s) - text files become clips",
            filetypes=[("Text files", "*.txt *.md *.log *.csv *.json"), ("All files", "*.*")])
        n = 0
        for p in paths:
            try:
                with open(p, "rb") as f:
                    raw = f.read(5 * 1024 * 1024)
                text = raw.decode("utf-8-sig") if self._is_utf8(raw) else raw.decode("cp1252", "replace")
                if text.strip():
                    self.store.add("text", text, self.cfg["allow_duplicates"])
                    n += 1
            except OSError:
                pass
        if paths:
            if self.visible():
                self.refresh()
            messagebox.showinfo(APP, f"Successfully imported {n} clip{'s' if n != 1 else ''}.")

    @staticmethod
    def _is_utf8(raw):
        try:
            raw.decode("utf-8-sig")
            return True
        except UnicodeDecodeError:
            return False

    # ---- dialogs -----------------------------------------------------------
    def _dialog(self, key, title, size):
        """One themed window per dialog kind: raise it if it is already open. size is in 96-dpi pixels."""
        self.hide()  # the popup would otherwise sit on top of the dialog
        w = self.dialogs.get(key)
        if w is not None and w.winfo_exists():
            w.deiconify()
            w.lift()
            w.focus_force()
            return None
        S = self.S
        w = self.dialogs[key] = tk.Toplevel(self.root)
        w.attributes("-alpha", 0.0)
        w.title(title)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        W, H = min(S(size[0]), int(sw * 0.94)), min(S(size[1]), int(sh * 0.9))
        w.geometry(f"{W}x{H}+{(sw - W) // 2}+{max(0, (sh - H) // 3)}")
        self.theme.track(w, bg="bg")
        w.update_idletasks()
        self.theme.decorate(w)
        w.attributes("-topmost", True)  # stays above everything while open; never hides behind the popup
        w.focus_force()
        ui.fade_in(w, 170)
        return w

    def _buttons(self, parent, ok_text, ok_cmd, cancel_cmd):
        bar = self._frame(parent)
        bar.pack(side="bottom", fill="x", padx=self.S(24), pady=self.S(18))
        wd.Button(bar, self.theme, "Cancel", cancel_cmd).pack(side="right")
        if ok_text:
            wd.Button(bar, self.theme, ok_text, ok_cmd, accent=True).pack(side="right", padx=(0, self.S(10)))
        return bar
    def new_clip(self):
        w = self._dialog("new", "New Clip", (680, 460))
        if not w:
            return
        S = self.S
        card = self._card(w, bg="field", radius=14)
        txt = tk.Text(card.inner, wrap="word", relief="flat", bd=0, highlightthickness=0, padx=S(10), pady=S(8),
                      font=(self.mono, 10), undo=True)
        self.theme.track(txt, bg="field", fg="text", insertbackground="text", selectbackground="sel",
                         selectforeground="text")
        txt.pack(fill="both", expand=True)

        def save(place):
            text = txt.get("1.0", "end-1c")
            if not text.strip():
                return
            self.store.add("text", text, self.cfg["allow_duplicates"])
            if place:
                write_clipboard("text", text)
                self.ignore_seq = user32.GetClipboardSequenceNumber()
            if self.visible():
                self.refresh()
            w.destroy()

        bar = self._buttons(w, "Save, close and place on clipboard", lambda: save(True), w.destroy)
        wd.Button(bar, self.theme, "Save", lambda: save(False)).pack(side="right", padx=(0, S(10)))
        card.pack(fill="both", expand=True, padx=S(24), pady=(S(24), 0))
        txt.focus_set()

    def show_help(self):
        w = self._dialog("help", "Help", (720, 680))
        if not w:
            return
        S = self.S
        self._buttons(w, None, None, w.destroy).winfo_children()[0].configure(text="Close")
        card = self._card(w)
        card.pack(fill="both", expand=True, padx=S(24), pady=(S(24), 0))
        t = tk.Text(card.inner, wrap="word", relief="flat", bd=0, highlightthickness=0, padx=S(16), pady=S(12),
                    font=(self.mono, 10), spacing3=S(2))
        self.theme.track(t, bg="surface", fg="text", selectbackground="sel", selectforeground="text")
        t.insert("1.0", HELP_TEXT)
        t.config(state="disabled")
        t.pack(fill="both", expand=True)

    def _tree(self, parent, columns, widths, height=None, multi=False):
        S = self.S
        wrap = self._card(parent)
        sb = ttk.Scrollbar(wrap.inner, orient="vertical", style="Slim.Vertical.TScrollbar")
        tree = ttk.Treeview(wrap.inner, columns=[c for c, _ in columns], show="headings",
                            selectmode="extended" if multi else "browse", yscrollcommand=sb.set)
        sb.configure(command=tree.yview)
        for (c, label), width in zip(columns, widths):
            tree.heading(c, text=label)
            tree.column(c, width=S(width), anchor="w")
        sb.pack(side="right", fill="y", padx=(0, S(2)), pady=S(2))
        tree.pack(side="left", fill="both", expand=True)
        return wrap, tree

    def show_hotkeys(self):
        w = self._dialog("hotkeys", "Global Hot Keys", (680, 440))
        if not w:
            return
        S = self.S
        self._buttons(w, None, None, w.destroy).winfo_children()[0].configure(text="Close")
        self._label(w, "Hotkeys this app registered with Windows", size=12, bold=True).pack(
            fill="x", padx=S(24), pady=(S(22), S(10)))
        wrap, tree = self._tree(w, (("desc", "Description"), ("key", "HotKey"), ("reg", "Registered")),
                                (320, 200, 90))
        wrap.pack(fill="both", expand=True, padx=S(24))
        for hid, spec, desc, _ev in hotkey_table(self.cfg):
            ok = self.hk_status.get(hid)
            tree.insert("", "end", values=(desc, spec, "Yes" if ok else ("No" if ok is False else "...")))

    def delete_clip_data(self):
        w = self._dialog("deldata", "Delete Clip Data", (1040, 680))
        if not w:
            return
        S, th = self.S, self.theme
        self._label(w, "Each clip is made of clipboard items. Find the ones you want to remove.",
                    muted=True).pack(fill="x", padx=S(24), pady=(S(20), S(8)))
        f = self._frame(w)
        f.pack(fill="x", padx=S(24))
        v = {k: tk.StringVar() for k in ("title", "fmt", "c_from", "c_to", "u_from", "u_to")}

        def field(r, c, text, widget):
            self._label(f, text, muted=True, size=9).grid(row=r * 2, column=c, sticky="w", padx=(0, S(12)))
            widget.grid(row=r * 2 + 1, column=c, sticky="we", padx=(0, S(12)), pady=(S(2), S(10)))

        field(0, 0, "Title contains", wd.Entry(f, th, textvariable=v["title"], width=28))
        fmts = [("", "Any format")] + [(x, x) for x in self.store.formats()]
        field(0, 1, "Format", wd.Dropdown(f, th, fmts, v["fmt"], width=16))
        field(0, 2, "Created from (YYYY-MM-DD)", wd.Entry(f, th, textvariable=v["c_from"], width=13))
        field(0, 3, "to", wd.Entry(f, th, textvariable=v["c_to"], width=13))
        field(1, 2, "Last used from", wd.Entry(f, th, textvariable=v["u_from"], width=13))
        field(1, 3, "to", wd.Entry(f, th, textvariable=v["u_to"], width=13))
        f.columnconfigure(0, weight=1)
        cols = (("Title", "Title"), ("Created", "Created"), ("Last Used", "Last Used"), ("Format", "Format"),
                ("Data Size", "Data Size"))
        wrap, tree = self._tree(w, cols, (420, 150, 150, 130, 100), multi=True)
        info = tk.StringVar()
        il = self._label(w, "", muted=True, size=9)
        il.configure(textvariable=info)
        sizes = {}

        def update_info(*_):
            sel = tree.selection()
            info.set(f"Database size: {human(os.path.getsize(self.store.path))}      "
                     f"Selected: {len(sel)}      Selected size: {human(sum(sizes.get(i, 0) for i in sel))}")

        def search():
            try:
                c = (date_to_ms(v["c_from"].get()), date_to_ms(v["c_to"].get()))
                u = (date_to_ms(v["u_from"].get()), date_to_ms(v["u_to"].get()))
            except ValueError:
                messagebox.showerror(APP, "Dates must look like 2026-10-28.", parent=w)
                return
            tree.delete(*tree.get_children())
            sizes.clear()
            for _mid, text, d, used, did, fmt, size in self.store.data_rows(v["title"].get(), v["fmt"].get(), c, u):
                title = " ".join((text or "[Image]").split())[:90]
                tree.insert("", "end", iid=str(did), values=(title, ms_to_text(d), ms_to_text(used or d), fmt,
                                                             human(size)))
                sizes[str(did)] = size
            update_info()

        def delete():
            sel = tree.selection()
            if sel and messagebox.askyesno(APP, f"Delete the data of {len(sel)} selected item(s)?\n"
                                                "Clips left with no data are removed.", parent=w):
                self.store.delete_data(int(i) for i in sel)
                if self.visible():
                    self.refresh()
                search()

        tree.bind("<<TreeviewSelect>>", update_info)
        bar = self._buttons(w, "Delete selected", delete, w.destroy)
        bar.winfo_children()[0].configure(text="Close")
        wd.Button(bar, th, "Search", search).pack(side="left")
        il.pack(side="bottom", fill="x", padx=S(24), pady=(S(8), 0))
        wrap.pack(fill="both", expand=True, padx=S(24), pady=(S(4), 0))
        search()

    def options(self):
        w = self._dialog("options", "Options", (700, 800))
        if not w:
            return
        S, th, c = self.S, self.theme, self.cfg
        var = {
            "startup": tk.BooleanVar(value=get_startup()),
            "max_items": tk.StringVar(value=str(c["max_items"])),
            "expire_days": tk.StringVar(value=str(c["expire_days"])),
            "allow_duplicates": tk.BooleanVar(value=c["allow_duplicates"]),
            "move_to_top_on_paste": tk.BooleanVar(value=c["move_to_top_on_paste"]),
            "hide_on_hotkey_if_visible": tk.BooleanVar(value=c["hide_on_hotkey_if_visible"]),
            "paste_after_select": tk.BooleanVar(value=c["paste_after_select"]),
            "max_clip_bytes": tk.StringVar(value=str(c["max_clip_bytes"])),
            "popup_position": tk.StringVar(value=c["popup_position"]),
            "show_startup_message": tk.BooleanVar(value=c["show_startup_message"]),
            "db_path": tk.StringVar(value=c["db_path"]),
            "hotkey": tk.StringVar(value=c["hotkey"]),
            "save_hotkey": tk.StringVar(value=c["save_hotkey"]),
            "screenshot_hotkey": tk.StringVar(value=c["screenshot_hotkey"]),
            "lastten_mods": tk.StringVar(value=c["lastten_mods"]),
            "theme": tk.StringVar(value=c["theme"]),
        }

        def cancel():
            th.set_mode(c["theme"])  # undo a live theme preview
            w.destroy()

        w.protocol("WM_DELETE_WINDOW", cancel)

        def setting(parent, title, desc=None):
            row = self._frame(parent)
            row.pack(fill="x", pady=S(8))
            left = self._frame(row)
            left.pack(side="left", fill="x", expand=True)
            self._label(left, title).pack(anchor="w")
            if desc:
                self._label(left, desc, muted=True, size=9).pack(anchor="w")
            right = self._frame(row)
            right.pack(side="right")
            return right

        def switch(parent, key, title, desc=None):
            wd.Switch(setting(parent, title, desc), th, var[key]).pack()

        def entry(parent, key, title, desc=None, width=12):
            wd.Entry(setting(parent, title, desc), th, textvariable=var[key], width=width).pack()

        self._buttons(w, "Save", lambda: ok(), cancel)
        tabs = wd.Tabs(w, th, ["General", "Shortcuts", "Appearance"])
        tabs.pack(fill="both", expand=True)
        g, s, a = tabs.pages["General"], tabs.pages["Shortcuts"], tabs.pages["Appearance"]
        switch(g, "startup", "Start on system startup", "Adds a per-user entry, no admin needed")
        switch(g, "allow_duplicates", "Allow duplicates", "Keep every copy even if the text is identical")
        switch(g, "move_to_top_on_paste", "Move clip to top on paste")
        switch(g, "hide_on_hotkey_if_visible", "Hide on hotkey if already visible")
        switch(g, "paste_after_select", "Paste into the active window after selecting")
        switch(g, "show_startup_message", "Show startup message")
        entry(g, "max_items", "Maximum saved copies", "Oldest unpinned clips are removed first")
        entry(g, "expire_days", "Entries expire after (days)", "0 = never. Measured from the last paste.")
        entry(g, "max_clip_bytes", "Maximum clip size (bytes)", "0 = no limit")
        entry(g, "db_path", "Database path", "Blank = data\\clips.db. Takes effect after a restart.", width=30)

        entry(s, "hotkey", "Activate Quick Paste", "Example: ctrl+alt+v, ctrl+shift+f9, win+`", width=22)
        entry(s, "screenshot_hotkey", "Screenshot tool", "Blank = none. “prtsc” works only if Windows has not "
                                                          "claimed PrtSc (Settings > Accessibility > Keyboard)", width=22)
        entry(s, "save_hotkey", "Save current clipboard", "Blank = no hotkey", width=22)
        entry(s, "lastten_mods", "Last ten copies: modifiers", "ctrl+shift → Ctrl+Shift+1 pastes the newest copy, "
                                                                 "Ctrl+Shift+0 the 10th. Blank = off", width=22)
        self._label(s, "Use “Global Hot Keys” in the tray menu to see which ones registered.", muted=True,
                    size=9).pack(anchor="w", pady=(S(14), 0))

        seg = setting(a, "Theme", "System follows your Windows light / dark setting")
        wd.Segmented(seg, th, [("system", "System"), ("light", "Light"), ("dark", "Dark")], var["theme"],
                     command=lambda v: th.set_mode(v)).pack()
        seg2 = setting(a, "Popup position", "Where the history window opens")
        wd.Segmented(seg2, th, [("cursor", "Cursor"), ("center", "Center"), ("previous", "Last place")],
                     var["popup_position"]).pack()

        def ok():
            try:
                ints = {k: int(var[k].get()) for k in ("max_items", "expire_days", "max_clip_bytes")}
                if ints["max_items"] < 1 or min(ints.values()) < 0:
                    raise ValueError("Numbers must be positive (maximum saved copies >= 1).")
                parse_hotkey(var["hotkey"].get())
                for hk in ("save_hotkey", "screenshot_hotkey"):
                    if var[hk].get().strip():
                        parse_hotkey(var[hk].get())
                if var["lastten_mods"].get().strip():
                    parse_hotkey(var["lastten_mods"].get() + "+1")
            except ValueError as e:
                messagebox.showerror(APP, str(e), parent=w)
                return
            c.update(ints)
            for k in ("allow_duplicates", "move_to_top_on_paste", "hide_on_hotkey_if_visible",
                      "paste_after_select", "show_startup_message"):
                c[k] = var[k].get()
            for k in ("popup_position", "db_path", "hotkey", "save_hotkey", "screenshot_hotkey", "lastten_mods", "theme"):
                c[k] = var[k].get().strip()
            self.store.max_items = c["max_items"]
            self.store.expire(c["expire_days"])
            try:
                set_startup(var["startup"].get())
            except OSError as e:
                messagebox.showwarning(APP, f"Could not change start-with-Windows: {e}", parent=w)
            self.save_cfg()
            th.set_mode(c["theme"])
            self.reload_hotkeys()
            w.after(400, self._report_failed_hotkeys)
            w.destroy()

    # ---- screenshots -------------------------------------------------------
    SHOT_MODES = [("rect", "Rectangle"), ("window", "Window"), ("full", "Full screen")]
    SHOT_DELAYS = [("0", "None"), ("3", "3 s"), ("5", "5 s"), ("10", "10 s")]

    def _save_shot_prefs(self):
        self.cfg["shot_mode"], self.cfg["shot_delay"] = self.shot_mode.get(), int(self.shot_delay.get())
        self.save_cfg()

    def quick_screenshot(self):
        """Capture now, using the mode and delay picked at the bottom of the main window."""
        self.start_capture(self.shot_mode.get(), int(self.shot_delay.get()))

    def start_capture(self, mode, delay):
        for win in [self.pop] + list(self.dialogs.values()):  # our own windows must not be in the picture
            try:
                if win.winfo_exists() and win.winfo_viewable():
                    win.withdraw()
            except tk.TclError:
                pass
        self.root.update()

        def capture():
            vx, vy, vw, vh = snip.virtual_screen()
            bgra = snip.grab(vx, vy, vw, vh)
            if mode == "full":
                return self._finish_shot(bgra, vw, vh)
            sel = snip.Selector(self.root, self.theme, bgra, vx, vy, vw, vh, mode).run()
            if sel:
                x, y, w, h = sel
                self._finish_shot(snip.crop_bgra(bgra, vw, x - vx, y - vy, w, h), w, h)

        if delay > 0:
            snip.Countdown(self.root, self.theme, delay, capture)
        else:
            self.root.after(300, capture)  # let the hidden windows disappear first

    def _finish_shot(self, bgra, w, h):
        dib = snip.bgra_to_dib24(bgra, w, h)
        self.store.add("image", dib)  # an explicit screenshot is always kept, even when "disconnected"
        write_clipboard("image", dib)
        self.ignore_seq = user32.GetClipboardSequenceNumber()
        self._shot_editor(bgra, w, h)

    def _shot_editor(self, bgra, w_px, h_px):
        """Preview + mark-up window. Closing it (Done, X or Esc) always puts the final picture on the clipboard."""
        old = self.dialogs.get("shot")
        if old is not None and old.winfo_exists():
            old.destroy()
        sc = self.theme.scale
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = self._dialog("shot", "Screenshot", (min(1180, int(sw * 0.92 / sc)), min(840, int(sh * 0.9 / sc))))
        S = self.S
        ann = annotate.Annotator(w, self.theme, bgra, w_px, h_px)

        def final_dib():
            ann.finish_editing()
            return snip.bgra_to_dib24(ann.render(), w_px, h_px)

        def copy_now():
            dib = final_dib()
            if ann.count():
                self.store.add("image", dib)  # keep the marked-up version in the history too
            write_clipboard("image", dib)
            self.ignore_seq = user32.GetClipboardSequenceNumber()
            return dib

        def finalize():
            if w.winfo_exists():
                copy_now()
                w.destroy()

        def save():
            dib = final_dib()
            pics = os.path.join(os.path.expanduser("~"), "Pictures")
            path = filedialog.asksaveasfilename(
                parent=w, title="Save screenshot", defaultextension=".png",
                initialdir=pics if os.path.isdir(pics) else os.path.expanduser("~"),
                initialfile=time.strftime("Feitto %Y-%m-%d %H%M%S.png"), filetypes=[("PNG image", "*.png")])
            if path:
                png = ui.dib_to_png(dib, 10 ** 6, 10 ** 6)
                if png:
                    with open(path, "wb") as f:
                        f.write(png[0])
                    self.toast("Screenshot saved.")

        bar = self._frame(w)
        bar.pack(side="bottom", fill="x", padx=S(18), pady=(S(4), S(14)))
        wd.Button(bar, self.theme, "Done", finalize, accent=True).pack(side="right")
        wd.Button(bar, self.theme, "Save as\u2026", save).pack(side="right", padx=(0, S(10)))
        wd.Button(bar, self.theme, "Copy", copy_now).pack(side="right", padx=(0, S(10)))
        wd.Button(bar, self.theme, "New screenshot",
                  lambda: (finalize(), self.root.after(200, self.quick_screenshot)), accent=True
                  ).pack(side="left", padx=(0, S(18)))
        # the same Mode / Delay choices as the main window (shared settings), right where you start the next shot
        for title, options, var in (("Mode", self.SHOT_MODES, self.shot_mode), ("Delay", self.SHOT_DELAYS, self.shot_delay)):
            self._label(bar, title, muted=True, size=9).pack(side="left", padx=(0, S(6)))
            wd.Segmented(bar, self.theme, options, var, compact=True).pack(side="left", padx=(0, S(16)))
        self._label(w, f"{w_px} × {h_px} px   ·   Closing this window copies the picture, with your marks, "
                       "to the clipboard so you can paste it anywhere.", muted=True, size=9
                    ).pack(side="bottom", fill="x", padx=S(20))
        ann.pack(fill="both", expand=True)
        w.protocol("WM_DELETE_WINDOW", finalize)
        w.bind("<Escape>", lambda e: finalize())
        w.bind("<Control-z>", lambda e: ann.undo())

    # ---- capture + hotkeys -----------------------------------------------
    def _poll(self):
        seq = user32.GetClipboardSequenceNumber()
        if seq != self.last_seq:
            self.last_seq = seq
            if seq != self.ignore_seq and not self.cfg["disconnected"]:
                item = read_clipboard()
                if item and (item[0] == "image" or item[1].strip()):
                    size = len(item[1]) * (2 if item[0] == "text" else 1)
                    limit = self.cfg["max_clip_bytes"]
                    if not limit or size <= limit:
                        self.store.add(*item, self.cfg["allow_duplicates"])
                        if self.visible():
                            self.refresh()
        self.root.after(self.cfg["poll_ms"], self._poll)

    def _hourly(self):
        self.store.expire(self.cfg["expire_days"])
        self.root.after(3600 * 1000, self._hourly)

    def _start_hotkeys(self):
        ready = threading.Event()
        threading.Thread(target=self._hotkey_loop, args=(ready,), daemon=True).start()
        ready.wait(5)
        time.sleep(0.15)
        self._report_failed_hotkeys()

    def _hotkey_loop(self, ready):
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # force this thread's message queue to exist
        self.hk_tid = kernel32.GetCurrentThreadId()
        ready.set()
        active = {}

        def apply():
            for hid in list(active):
                user32.UnregisterHotKey(None, hid)
            active.clear()
            self.hk_status.clear()
            for hid, spec, _desc, event in hotkey_table(self.cfg):
                try:
                    mods, vk = parse_hotkey(spec)
                    ok = bool(user32.RegisterHotKey(None, hid, mods, vk))
                except ValueError:
                    ok = False
                self.hk_status[hid] = ok
                if ok:
                    active[hid] = event

        apply()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in active:
                self.events.put(active[msg.wParam])
            elif msg.message == WM_RELOAD_HOTKEYS:
                apply()

    def reload_hotkeys(self):
        if self.hk_tid:
            user32.PostThreadMessageW(self.hk_tid, WM_RELOAD_HOTKEYS, 0, 0)

    def _report_failed_hotkeys(self):
        failed = [f"{spec}  ({desc})" for hid, spec, desc, _e in hotkey_table(self.cfg)
                  if self.hk_status.get(hid) is False]
        if failed:
            messagebox.showwarning(APP, "These hotkeys could not be registered (already used by another "
                                        "program, or invalid):\n\n" + "\n".join(failed) +
                                        "\n\nChange them in Options > Shortcuts.")

    COMMANDS = {
        "options": "options", "hotkeys": "show_hotkeys", "delete_data": "delete_clip_data",
        "delete_unused": "delete_unused", "backup": "backup_db", "restore": "restore_db",
        "import": "import_clips", "new_clip": "new_clip", "help": "show_help",
        "toggle_connect": "toggle_connect", "toggle_startmsg": "toggle_startmsg",
        "save_clip": "save_current_clipboard", "screenshot": "quick_screenshot", "quit": "quit",
    }

    def _handle(self, ev):
        if ev == "open":
            self.show()
        elif ev == "toggle":
            if self.visible() and self.cfg["hide_on_hotkey_if_visible"]:
                self.hide()
            else:
                self.show()
        elif isinstance(ev, tuple) and ev[0] == "tray_menu":
            self.show_tray_menu(ev[1], ev[2])
        elif isinstance(ev, tuple) and ev[0] == "last":
            ids = self.store.recent(ev[1] + 1)
            if len(ids) > ev[1]:
                self.paste_clip(ids[ev[1]], user32.GetForegroundWindow(), False)
        elif ev in self.COMMANDS:
            getattr(self, self.COMMANDS[ev])()

    def _drain(self):
        try:
            while True:
                try:
                    self._handle(self.events.get_nowait())
                except queue.Empty:
                    raise
                except Exception as e:  # a failing menu command must not kill the app
                    messagebox.showerror(APP, f"{type(e).__name__}: {e}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def run(self):
        if not self.tray.hwnd:  # no tray available: do not start invisible and unreachable
            self.show()
        self.root.mainloop()
        self.tray.remove()


def main():
    ui.enable_dpi()
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p("Feitto.Clipboard"))
    except Exception:
        pass
    mutex = kernel32.CreateMutexW(None, False, "Local\\FeittoSingleInstance")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS: wake the running one instead
        notify_running_instance()
        sys.exit(0)
    App().run()
    del mutex


if __name__ == "__main__":
    main()
