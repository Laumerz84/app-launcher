"""Image helpers and the generated launcher icon."""
import struct
import unittest
import zlib

import support
from applauncher import gfx


def decode_png(png):
    """Minimal decoder for the PNGs we write (8-bit RGB/RGBA, filter 0). Returns (w, h, channels, rows)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, header = 8, b"", None
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos:pos + 4])
        tag, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF, f"bad CRC in {tag!r}"
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", data)
        elif tag == b"IDAT":
            idat += data
        pos += 12 + length
    w, h, depth, ctype, *_ = header
    channels = {2: 3, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * channels
    rows = []
    for y in range(h):
        line = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        assert line[0] == 0
        rows.append(line[1:])
    return w, h, channels, rows


def pixel(decoded, x, y):
    w, h, ch, rows = decoded
    return tuple(rows[y][x * ch:(x + 1) * ch])


class PngTests(unittest.TestCase):
    def test_round_trip_rgba_and_rgb(self):
        rgba = bytes([255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0, 9, 9, 9, 9])
        d = decode_png(gfx.png_encode(2, 2, rgba, 4))
        self.assertEqual((d[0], d[1], d[2]), (2, 2, 4))
        self.assertEqual(pixel(d, 1, 0), (0, 255, 0, 128))
        d = decode_png(gfx.png_encode(1, 2, bytes([1, 2, 3, 4, 5, 6]), 3))
        self.assertEqual(pixel(d, 0, 1), (4, 5, 6))

    def test_wrong_buffer_size_is_rejected(self):
        with self.assertRaises(ValueError):
            gfx.png_encode(2, 2, b"\x00" * 5, 4)

    def test_size_and_base64(self):
        png = gfx.png_encode(3, 5, b"\x00" * 45, 3)
        self.assertEqual(gfx.png_size(png), (3, 5))
        self.assertTrue(gfx.png_b64(png).startswith("iVBORw0KGgo"))


class RoundedRectTests(unittest.TestCase):
    def test_opaque_version_has_bg_corners_and_fill_centre(self):
        d = decode_png(gfx.rounded_rect_png(60, 30, 10, (255, 255, 255), (10, 20, 30)))
        self.assertEqual((d[0], d[1], d[2]), (60, 30, 3))
        self.assertEqual(pixel(d, 0, 0), (10, 20, 30))          # rounded-off corner shows the background
        self.assertEqual(pixel(d, 59, 29), (10, 20, 30))
        self.assertEqual(pixel(d, 30, 15), (255, 255, 255))     # centre is the fill
        edge = pixel(d, 4, 1)                                    # anti-aliased: between bg and fill
        self.assertTrue(all(10 <= c <= 255 for c in edge))

    def test_border_colours_the_outer_ring_only(self):
        d = decode_png(gfx.rounded_rect_png(60, 30, 8, (255, 255, 255), (0, 0, 0), (255, 0, 0), 2))
        self.assertEqual(pixel(d, 30, 0), (255, 0, 0))
        self.assertEqual(pixel(d, 0, 15), (255, 0, 0))
        self.assertEqual(pixel(d, 30, 15), (255, 255, 255))

    def test_transparent_version_has_clear_corners_and_solid_centre(self):
        d = decode_png(gfx.rounded_rect_png(40, 40, 12, (200, 100, 50), None))
        self.assertEqual(d[2], 4)
        self.assertEqual(pixel(d, 0, 0)[3], 0)
        self.assertEqual(pixel(d, 20, 20), (200, 100, 50, 255))

    def test_circle_and_degenerate_sizes(self):
        d = decode_png(gfx.circle_png(9, (0, 200, 0), None))
        self.assertEqual(pixel(d, 4, 4), (0, 200, 0, 255))
        self.assertEqual(pixel(d, 0, 0)[3], 0)
        gfx.rounded_rect_png(1, 1, 50, (1, 2, 3), (0, 0, 0))   # radius larger than the shape must not break
        gfx.rounded_rect_png(20, 6, 0, (1, 2, 3), (0, 0, 0), (9, 9, 9), 1)

    def test_tall_shapes_reuse_rows_correctly(self):
        d = decode_png(gfx.rounded_rect_png(50, 200, 6, (5, 5, 5), (250, 250, 250)))
        self.assertEqual(pixel(d, 25, 100), (5, 5, 5))
        self.assertEqual(pixel(d, 25, 60), pixel(d, 25, 140))
        self.assertEqual(pixel(d, 1, 3), pixel(d, 1, 196))       # top and bottom edges mirror each other


class ResizeTests(unittest.TestCase):
    def test_same_size_is_untouched_and_scaling_keeps_solid_colour(self):
        px = bytes([10, 20, 30, 255]) * 16
        self.assertEqual(gfx.resize_rgba(4, 4, px, 4, 4), px)
        out = gfx.resize_rgba(4, 4, px, 8, 8)
        self.assertEqual(len(out), 8 * 8 * 4)
        self.assertEqual(out[:4], bytes([10, 20, 30, 255]))

    def test_transparent_neighbours_do_not_bleed_dark_fringes(self):
        # left half opaque white, right half fully transparent black: result stays white where visible
        row = bytes([255, 255, 255, 255]) * 2 + bytes([0, 0, 0, 0]) * 2
        out = gfx.resize_rgba(4, 1, row, 8, 1)
        for i in range(0, len(out), 4):
            if out[i + 3] > 0:
                self.assertEqual(out[i:i + 3], b"\xff\xff\xff")


class BadgeTests(unittest.TestCase):
    def test_letter_and_colour_are_stable(self):
        self.assertEqual(gfx.badge_letter("  fractal flame"), "F")
        self.assertEqual(gfx.badge_letter("!!!"), "?")
        self.assertEqual(gfx.badge_color("Museum"), gfx.badge_color("museum "))
        self.assertNotEqual(gfx.badge_color("Museum"), gfx.badge_color("Vortex Street"))
        self.assertRegex(gfx.badge_color("x"), r"^#[0-9a-f]{6}$")


class IconExtractionTests(unittest.TestCase):
    def test_launcher_icon_yields_a_transparent_png_at_the_requested_size(self):
        for size in (16, 32, 48, 64):
            png = gfx.extract_icon_png(str(support.ROOT / "launcher.ico"), size)
            self.assertIsNotNone(png, size)
            d = decode_png(png)
            self.assertEqual((d[0], d[1]), (size, size))
            self.assertEqual(pixel(d, 0, 0)[3], 0, "outside the rounded square should be transparent")
            self.assertEqual(pixel(d, size // 2, size // 2)[3], 255)

    def test_icon_inside_a_dll_by_index(self):
        png = gfx.extract_icon_png(r"C:\Windows\System32\imageres.dll,144", 32)
        self.assertIsNotNone(png)
        self.assertEqual(gfx.png_size(png), (32, 32))

    def test_program_icon(self):
        self.assertIsNotNone(gfx.extract_icon_png(r"C:\Windows\System32\notepad.exe", 32))

    def test_missing_or_empty_specs_give_none(self):
        self.assertIsNone(gfx.extract_icon_png(r"C:\no\such\file.ico", 32))
        self.assertIsNone(gfx.extract_icon_png("", 32))
        self.assertIsNone(gfx.extract_icon_png(str(support.ROOT / "apps.json"), 32))  # not an icon

    def test_icon_spec_parsing(self):
        self.assertEqual(gfx.parse_icon_spec(r"C:\a\b.dll,144"), (r"C:\a\b.dll", 144))
        self.assertEqual(gfx.parse_icon_spec(r'"C:\a\b.dll", -5'), (r"C:\a\b.dll", -5))
        self.assertEqual(gfx.parse_icon_spec(r"C:\a\b.ico"), (r"C:\a\b.ico", 0))
        self.assertEqual(gfx.parse_icon_spec(r"C:\a,b\c.ico"), (r"C:\a,b\c.ico", 0))


class LauncherIconFileTests(unittest.TestCase):
    def test_ico_holds_every_size_from_16_to_256(self):
        data = (support.ROOT / "launcher.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        sizes = []
        for i in range(count):
            w, h, _, _, planes, bpp, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
            sizes.append(w or 256)
            self.assertEqual(w, h)
            self.assertEqual(bpp, 32)
            self.assertLessEqual(offset + length, len(data))
        self.assertEqual(sorted(sizes), [16, 24, 32, 48, 64, 128, 256])


if __name__ == "__main__":
    unittest.main()
