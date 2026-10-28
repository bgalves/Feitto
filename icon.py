"""Feitto's icon: a clipboard with a camera badge, drawn with GDI+ at any size (no image files needed).

make_hicon(size) -> tray icon;  ensure_ico(path) -> multi-size .ico used for window title bars and the taskbar.
"""
import ctypes
import os
import struct
import zlib
from ctypes import wintypes

import annotate as an

gdip, _H, _PH, _F, _I = an.gdip, an._H, an._PH, an._F, an._I
an._sig("GdipFillEllipse", [_H, _H, _F, _F, _F, _F])
an._sig("GdipCreatePath", [_I, _PH])
an._sig("GdipAddPathArc", [_H, _F, _F, _F, _F, _F, _F])
an._sig("GdipClosePathFigure", [_H])
an._sig("GdipFillPath", [_H, _H, _H])
an._sig("GdipDeletePath", [_H])
an._sig("GdipCreateLineBrushFromRect", [ctypes.POINTER(an.RectF), ctypes.c_uint32, ctypes.c_uint32, _I, _I, _PH])
an._sig("GdipScaleWorldTransform", [_H, _F, _F, _I])

PF_32BPP_ARGB = 0x0026200A
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", ctypes.c_void_p), ("hbmColor", ctypes.c_void_p)]


gdi32.CreateBitmap.restype = ctypes.c_void_p
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
user32.CreateIconIndirect.restype = ctypes.c_void_p
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]


def _argb(hexcolor, alpha=255):
    h = hexcolor.lstrip("#")
    return alpha << 24 | int(h[0:2], 16) << 16 | int(h[2:4], 16) << 8 | int(h[4:6], 16)


def _fill_rr(g, color, x, y, w, h, r):
    brush, path = ctypes.c_void_p(), ctypes.c_void_p()
    gdip.GdipCreateSolidFill(_argb(color) if isinstance(color, str) else color, ctypes.byref(brush))
    gdip.GdipCreatePath(0, ctypes.byref(path))
    d = 2.0 * r
    for (ax, ay, start) in ((x, y, 180), (x + w - d, y, 270), (x + w - d, y + h - d, 0), (x, y + h - d, 90)):
        gdip.GdipAddPathArc(path, _F(ax), _F(ay), _F(d), _F(d), _F(start), _F(90))
    gdip.GdipClosePathFigure(path)
    gdip.GdipFillPath(g, brush, path)
    gdip.GdipDeletePath(path)
    gdip.GdipDeleteBrush(brush)


def _fill_circle(g, color, cx, cy, r):
    brush = ctypes.c_void_p()
    gdip.GdipCreateSolidFill(_argb(color) if isinstance(color, str) else color, ctypes.byref(brush))
    gdip.GdipFillEllipse(g, brush, _F(cx - r), _F(cy - r), _F(2 * r), _F(2 * r))
    gdip.GdipDeleteBrush(brush)


def render_icon(size):
    """Straight-alpha top-down BGRA of the icon at size x size pixels."""
    if not an.available():
        raise RuntimeError("GDI+ is not available")
    buf = ctypes.create_string_buffer(size * size * 4)
    bmp, g = ctypes.c_void_p(), ctypes.c_void_p()
    gdip.GdipCreateBitmapFromScan0(size, size, size * 4, PF_32BPP_ARGB, buf, ctypes.byref(bmp))
    gdip.GdipGetImageGraphicsContext(bmp, ctypes.byref(g))
    gdip.GdipSetSmoothingMode(g, 4)
    gdip.GdipScaleWorldTransform(g, _F(size / 256.0), _F(size / 256.0), 0)
    try:
        # rounded-square background with a soft diagonal gradient
        brush, path = ctypes.c_void_p(), ctypes.c_void_p()
        rect = an.RectF(0, 0, 256, 256)
        gdip.GdipCreateLineBrushFromRect(ctypes.byref(rect), _argb("#6A8BFF"), _argb("#6B3FE0"), 2, 0,
                                         ctypes.byref(brush))
        gdip.GdipCreatePath(0, ctypes.byref(path))
        for (ax, ay, start) in ((10, 10, 180), (130, 10, 270), (130, 130, 0), (10, 130, 90)):
            gdip.GdipAddPathArc(path, _F(ax), _F(ay), _F(116), _F(116), _F(start), _F(90))
        gdip.GdipClosePathFigure(path)
        gdip.GdipFillPath(g, brush, path)
        gdip.GdipDeletePath(path)
        gdip.GdipDeleteBrush(brush)
        # clipboard: board, clip, text lines
        _fill_rr(g, "#FFFFFF", 50, 48, 130, 168, 16)
        _fill_rr(g, "#2A2F6B", 86, 32, 58, 38, 11)
        _fill_rr(g, "#8FA2FF", 103, 43, 24, 13, 6)
        for y, wd in ((92, 86), (118, 66), (144, 76)):
            _fill_rr(g, "#C5CEFA", 70, y, wd, 13, 6.5)
        # camera badge: separation ring (matches the background there), body, hump, lens, flash
        _fill_rr(g, "#6A56E9", 114, 124, 128, 104, 28)
        _fill_rr(g, "#FFC83D", 122, 138, 112, 80, 20)
        _fill_rr(g, "#FFC83D", 146, 126, 42, 22, 9)
        _fill_circle(g, "#2A2F6B", 178, 180, 31)
        _fill_circle(g, "#6F8BFF", 178, 180, 20)
        _fill_circle(g, "#2A2F6B", 178, 180, 9)
        _fill_circle(g, _argb("#FFFFFF", 235), 188, 170, 5.5)
        _fill_rr(g, "#2A2F6B", 211, 148, 14, 10, 4)
    finally:
        gdip.GdipDeleteGraphics(g)
        gdip.GdipDisposeImage(bmp)
    return bytes(buf.raw)


def make_hicon(size):
    """HICON for the tray (size from GetSystemMetrics(SM_CXSMICON))."""
    bgra = render_icon(size)
    color = gdi32.CreateBitmap(size, size, 1, 32, bgra)
    mask = gdi32.CreateBitmap(size, size, 1, 1, bytes(((size + 15) // 16 * 2) * size))
    return user32.CreateIconIndirect(ctypes.byref(ICONINFO(True, 0, 0, mask, color)))


def tray_icon_size():
    return user32.GetSystemMetrics(49) or 16     # SM_CXSMICON


def png_rgba(bgra, w, h):
    """PNG (with transparency) of a straight-alpha BGRA picture."""
    raw = bytearray()
    for y in range(h):
        row = bgra[y * w * 4:(y + 1) * w * 4]
        out = bytearray(w * 4)
        out[0::4], out[1::4], out[2::4], out[3::4] = row[2::4], row[1::4], row[0::4], row[3::4]
        raw += b"\x00" + out

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


ICO_SIZES = (16, 20, 24, 32, 40, 48, 64)


def build_ico():
    """A multi-size .ico using plain bitmap entries (the form Tk's iconbitmap understands)."""
    images = []
    for s in ICO_SIZES:
        bgra = render_icon(s)
        rows = b"".join(bgra[y * s * 4:(y + 1) * s * 4] for y in range(s - 1, -1, -1))   # bottom-up
        mask = bytes(((s + 31) // 32 * 4) * s)
        hdr = struct.pack("<IiiHHIIiiII", 40, s, s * 2, 1, 32, 0, len(rows) + len(mask), 0, 0, 0, 0)
        images.append((s, hdr + rows + mask))
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for s, data in images:
        entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    return head + entries + blobs


def ensure_ico(path):
    """Write the .ico next to the program if it is not there yet; returns the path or None on failure."""
    try:
        if not os.path.exists(path):
            with open(path, "wb") as f:
                f.write(build_ico())
        return path
    except Exception:
        return None
