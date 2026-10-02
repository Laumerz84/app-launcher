"""Pure-Python image helpers (no Pillow): PNG encoding, anti-aliased rounded
rectangles for the buttons, icon extraction from .ico/.exe/.dll, letter badges.

Tk can display PNG data directly, so everything here produces PNG bytes.
"""
from __future__ import annotations

import base64
import colorsys
import ctypes
import math
import os
import re
import struct
import sys
import zlib
from ctypes import wintypes
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

Color = tuple[int, int, int]


# --------------------------------------------------------------------------- colours
def hex_to_rgb(value: str) -> Color:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def rgb_to_hex(color: Color) -> str:
    return "#%02x%02x%02x" % color


def badge_color(name: str) -> str:
    """A stable, pleasant colour for a letter badge, derived from the app's name."""
    hue = (zlib.crc32(name.strip().lower().encode("utf-8")) % 360) / 360.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.42, 0.55)
    return rgb_to_hex((round(r * 255), round(g * 255), round(b * 255)))


def badge_letter(name: str) -> str:
    for ch in name.strip():
        if ch.isalnum():
            return ch.upper()
    return "?"


# --------------------------------------------------------------------------- PNG
def png_encode(width: int, height: int, pixels: bytes, channels: int = 4) -> bytes:
    """Encode 8-bit RGB (channels=3) or RGBA (channels=4) rows as a PNG."""
    if channels not in (3, 4) or len(pixels) != width * height * channels:
        raise ValueError("pixel buffer does not match the size")
    stride = width * channels
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter: none
        raw += pixels[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")


def png_b64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def png_size(png: bytes) -> tuple[int, int]:
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", png[16:24])


# --------------------------------------------------------------------------- rounded rectangles
def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _row_coverage(y: int, width: int, height: int, radius: float, border: float):
    """Per-pixel (outer, inner) coverage for one row of a rounded rectangle."""
    hx, hy = width / 2.0, height / 2.0
    qy = abs(y + 0.5 - hy) - (hy - radius)
    outer: list[float] = []
    inner: list[float] = []
    for x in range(width):
        qx = abs(x + 0.5 - hx) - (hx - radius)
        ox, oy = max(qx, 0.0), max(qy, 0.0)
        d = math.hypot(ox, oy) + min(max(qx, qy), 0.0) - radius  # signed distance, negative inside
        outer.append(_clamp01(0.5 - d))
        inner.append(_clamp01(0.5 - d - border))
    return outer, inner


def rounded_rect_png(width: int, height: int, radius: float, fill: Color, bg: Optional[Color],
                     border: Optional[Color] = None, border_w: float = 0.0) -> bytes:
    """An anti-aliased rounded rectangle.

    With *bg* (the canvas colour) the result is opaque and composited on it - crisp corners with
    no alpha needed. With ``bg=None`` it is RGBA with transparent corners, for shapes that sit on
    backgrounds that change (hover states).

    Rows far from the top/bottom edge are identical, so only a handful are computed.
    """
    radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    if border is None:
        border_w = 0.0
    cap = int(max(radius, border_w)) + 2
    cache: dict[int, bytes] = {}
    rows: list[bytes] = []
    for y in range(height):
        key = min(y, height - 1 - y)  # distance from the nearest horizontal edge
        rep_y = key
        if key >= cap:
            key, rep_y = cap, height // 2  # every row this far in looks the same
        row = cache.get(key)
        if row is None:
            outer, inner = _row_coverage(rep_y, width, height, radius, border_w)
            buf = bytearray()
            for ao, ai in zip(outer, inner):
                ring = ao - ai
                if bg is not None:
                    for c in range(3):
                        value = bg[c] * (1.0 - ao) + fill[c] * ai
                        if border is not None:
                            value += border[c] * ring
                        buf.append(int(value + 0.5))
                else:
                    if ao <= 0.0:
                        buf += b"\x00\x00\x00\x00"
                        continue
                    for c in range(3):
                        value = fill[c] * ai
                        if border is not None:
                            value += border[c] * ring
                        buf.append(int(min(255.0, value / ao) + 0.5))
                    buf.append(int(ao * 255 + 0.5))
            row = bytes(buf)
            cache[key] = row
        rows.append(row)
    return png_encode(width, height, b"".join(rows), channels=3 if bg is not None else 4)


def circle_png(diameter: int, fill: Color, bg: Optional[Color], border: Optional[Color] = None, border_w: float = 0.0) -> bytes:
    return rounded_rect_png(diameter, diameter, diameter / 2.0, fill, bg, border, border_w)


# --------------------------------------------------------------------------- resampling
def resize_rgba(width: int, height: int, pixels: bytes, new_w: int, new_h: int) -> bytes:
    """Bilinear resize of an RGBA buffer (premultiplied while blending, so edges stay clean)."""
    if (width, height) == (new_w, new_h):
        return pixels
    out = bytearray(new_w * new_h * 4)
    xr = width / new_w
    yr = height / new_h
    for oy in range(new_h):
        sy = min(max((oy + 0.5) * yr - 0.5, 0.0), height - 1.0)
        y0 = int(sy)
        y1 = min(y0 + 1, height - 1)
        fy = sy - y0
        for ox in range(new_w):
            sx = min(max((ox + 0.5) * xr - 0.5, 0.0), width - 1.0)
            x0 = int(sx)
            x1 = min(x0 + 1, width - 1)
            fx = sx - x0
            acc = [0.0, 0.0, 0.0, 0.0]
            for (px, py, w) in ((x0, y0, (1 - fx) * (1 - fy)), (x1, y0, fx * (1 - fy)),
                                (x0, y1, (1 - fx) * fy), (x1, y1, fx * fy)):
                i = (py * width + px) * 4
                a = pixels[i + 3] * w
                acc[0] += pixels[i] * a
                acc[1] += pixels[i + 1] * a
                acc[2] += pixels[i + 2] * a
                acc[3] += a
            o = (oy * new_w + ox) * 4
            if acc[3] > 0:
                out[o] = int(acc[0] / acc[3] + 0.5)
                out[o + 1] = int(acc[1] / acc[3] + 0.5)
                out[o + 2] = int(acc[2] / acc[3] + 0.5)
                out[o + 3] = int(acc[3] + 0.5)
    return bytes(out)


# --------------------------------------------------------------------------- icon extraction
def parse_icon_spec(spec: str) -> tuple[str, int]:
    """'path' or 'path,index' (like a Windows shortcut's IconLocation) -> (path, index)."""
    spec = os.path.expandvars(spec.strip().strip('"'))
    match = re.match(r"^(.*?),\s*(-?\d+)$", spec)
    if match and not os.path.exists(spec):
        return match.group(1).strip().strip('"'), int(match.group(2))
    return spec, 0


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wintypes.LONG), ("bmWidth", wintypes.LONG), ("bmHeight", wintypes.LONG),
                ("bmWidthBytes", wintypes.LONG), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


def _dib_bytes(gdi32, hdc, hbitmap, width: int, height: int) -> Optional[bytes]:
    """Top-down 32-bit BGRA pixels of a bitmap."""
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(width * height * 4)
    got = gdi32.GetDIBits(hdc, hbitmap, 0, height, buf, ctypes.byref(info), 0)
    return buf.raw if got else None


def hicon_to_rgba(hicon) -> Optional[tuple[int, int, bytes]]:
    """Read an HICON's pixels as (width, height, RGBA bytes)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(_ICONINFO)]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]

    info = _ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(info)):
        return None
    hdc = user32.GetDC(None)
    try:
        if not info.hbmColor:
            return None  # monochrome icon: not worth supporting
        bm = _BITMAP()
        gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
        width, height = bm.bmWidth, bm.bmHeight
        if width <= 0 or height <= 0:
            return None
        color = _dib_bytes(gdi32, hdc, info.hbmColor, width, height)
        if color is None:
            return None
        has_alpha = any(color[3::4])
        mask = None if has_alpha else (_dib_bytes(gdi32, hdc, info.hbmMask, width, height) if info.hbmMask else None)
        out = bytearray(width * height * 4)
        for i in range(0, width * height * 4, 4):
            out[i] = color[i + 2]
            out[i + 1] = color[i + 1]
            out[i + 2] = color[i]
            if has_alpha:
                out[i + 3] = color[i + 3]
            elif mask is not None:
                out[i + 3] = 0 if (mask[i] or mask[i + 1] or mask[i + 2]) else 255
            else:
                out[i + 3] = 255
        return width, height, bytes(out)
    finally:
        user32.ReleaseDC(None, hdc)
        if info.hbmColor:
            gdi32.DeleteObject(info.hbmColor)
        if info.hbmMask:
            gdi32.DeleteObject(info.hbmMask)


def extract_icon_png(spec: str, size: int) -> Optional[bytes]:
    """PNG bytes (size x size, with transparency) of the icon named by *spec*, or None."""
    if not IS_WINDOWS or not spec:
        return None
    path, index = parse_icon_spec(spec)
    if not path or not os.path.isfile(path):
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PrivateExtractIconsW.argtypes = [
            wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT), wintypes.UINT, wintypes.UINT]
        user32.PrivateExtractIconsW.restype = wintypes.UINT
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        # Ask for the size we want; for a multi-size .ico Windows picks the closest frame.
        big = max(size, 32)
        hicon = wintypes.HICON()
        icon_id = wintypes.UINT()
        if path.lower().endswith(".ico"):
            index = 0
        count = user32.PrivateExtractIconsW(path, index, big, big, ctypes.byref(hicon), ctypes.byref(icon_id), 1, 0)
        if count in (0, 0xFFFFFFFF) or not hicon:
            return None
        try:
            got = hicon_to_rgba(hicon)
        finally:
            user32.DestroyIcon(hicon)
        if got is None:
            return None
        w, h, rgba = got
        if (w, h) != (size, size):
            rgba = resize_rgba(w, h, rgba, size, size)
        return png_encode(size, size, rgba, channels=4)
    except Exception:
        return None
