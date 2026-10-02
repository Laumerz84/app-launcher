"""Generate launcher.ico (multi-size, 16 to 256 px) - pure Python, no Pillow.

The picture: a blue rounded square holding a 2x2 grid of tiles, one of them
lit up. Every size is drawn from scratch (not shrunk from the big one) so the
small ones stay crisp.

    python make_icon.py            # writes launcher.ico next to this file
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

from applauncher import gfx

SIZES = (16, 24, 32, 48, 64, 128, 256)
BLUE_TOP = (52, 137, 255)
BLUE_BOTTOM = (8, 62, 158)
TILE = (255, 255, 255)
LIT = (110, 231, 240)


def _sdf_round_rect(x: float, y: float, cx: float, cy: float, hx: float, hy: float, r: float) -> float:
    qx, qy = abs(x - cx) - (hx - r), abs(y - cy) - (hy - r)
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def render(size: int) -> bytes:
    """RGBA bytes (straight alpha) for one square icon frame."""
    ss = 6 if size <= 32 else 4 if size <= 64 else 3
    margin, radius = 0.03, 0.22
    tile, gap, tile_r = 0.27, 0.075, 0.075
    span = 2 * tile + gap
    origin = (1 - span) / 2
    tiles = []
    for row in range(2):
        for col in range(2):
            cx = origin + col * (tile + gap) + tile / 2
            cy = origin + row * (tile + gap) + tile / 2
            tiles.append((cx, cy, LIT if (row, col) == (1, 1) else TILE))

    out = bytearray(size * size * 4)
    inv = 1.0 / (size * ss)
    for py in range(size):
        for px in range(size):
            acc_r = acc_g = acc_b = acc_a = 0.0
            for sy in range(ss):
                y = (py * ss + sy + 0.5) * inv
                for sx in range(ss):
                    x = (px * ss + sx + 0.5) * inv
                    d = _sdf_round_rect(x, y, 0.5, 0.5, 0.5 - margin, 0.5 - margin, radius)
                    if d > 0:
                        continue
                    t = (x + y) / 2  # diagonal gradient
                    color = tuple(BLUE_TOP[i] * (1 - t) + BLUE_BOTTOM[i] * t for i in range(3))
                    for cx, cy, tc in tiles:
                        if _sdf_round_rect(x, y, cx, cy, tile / 2, tile / 2, tile_r) <= 0:
                            color = tc
                            break
                    acc_r += color[0]
                    acc_g += color[1]
                    acc_b += color[2]
                    acc_a += 1.0
            o = (py * size + px) * 4
            if acc_a:
                out[o] = int(acc_r / acc_a + 0.5)
                out[o + 1] = int(acc_g / acc_a + 0.5)
                out[o + 2] = int(acc_b / acc_a + 0.5)
                out[o + 3] = int(255 * acc_a / (ss * ss) + 0.5)
    return bytes(out)


def _dib(size: int, rgba: bytes) -> bytes:
    """ICO-style bitmap: BITMAPINFOHEADER (height doubled), bottom-up BGRA, then a 1-bit AND mask."""
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = rgba[(y * size + x) * 4:(y * size + x) * 4 + 4]
            rows += bytes((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4
    return header + bytes(rows) + bytes(mask_row * size)


def build_ico(sizes=SIZES) -> bytes:
    frames = []
    for size in sizes:
        rgba = render(size)
        data = gfx.png_encode(size, size, rgba, channels=4) if size >= 256 else _dib(size, rgba)
        frames.append((size, data))
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = bytearray()
    for size, data in frames:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + bytes(entries) + b"".join(data for _, data in frames)


if __name__ == "__main__":
    target = Path(__file__).with_name("launcher.ico")
    target.write_bytes(build_ico())
    print(f"wrote {target} ({target.stat().st_size} bytes, sizes {', '.join(map(str, SIZES))})")
    sys.exit(0)
