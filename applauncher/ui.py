"""The launcher window (tkinter).

Everything is drawn on two canvases - the scrolling list of app rows and the
bottom bar with the two buttons and the status line - using small
anti-aliased PNGs for the rounded shapes, so it looks modern without any
third-party packages.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import commands, config, gfx, procs, winutil
from . import stop as stopmod
from .commands import LaunchError
from .config import App
from .theme import Palette, palette_for

HERE = Path(__file__).resolve().parent.parent
DEFAULT_APPS = HERE / "apps.json"
ICON_FILE = HERE / "launcher.ico"
LOG_FILE = HERE / "launcher.log"

log = logging.getLogger("applauncher")

# Layout in logical pixels at 100% display scaling (multiplied by the DPI scale).
W = 360
PAD = 16
ROW_H = 56
GAP = 8
RADIUS = 10
ICON = 32
BTN_H = 40
BAR_PAD = 12
STATUS_H = 38
DOT = 9
KEYCAP = 22
STOP_W, STOP_H = 52, 26            # the small "Stop" pill on a running card
ARMED_W, ARMED_H = 72, 36          # after the first click: "Click again to stop"
STOPPING_W = 68

ARM_MS = 3000                      # how long the first click stays armed
CLOSE_GRACE_S = 6.0                # closing the window waits this long for a stop in progress
RUNNING_EVERY_MS = 3000
FILE_EVERY_MS = 1500
HINT_DELAY_OK = 4500
HINT_DELAY_ERR = 12000


def blend(a: str, b: str, t: float) -> str:
    ra, rb = gfx.hex_to_rgb(a), gfx.hex_to_rgb(b)
    return gfx.rgb_to_hex(tuple(round(x * (1 - t) + y * t) for x, y in zip(ra, rb)))  # type: ignore[arg-type]


@dataclass
class _Row:
    y: int
    bg: int = 0
    sub: int = 0
    dot: int = 0
    stop_img: int = 0
    stop_txt: int = 0
    stop_right: int = 0     # right edge of the Stop pill (it grows leftwards when armed)
    dot_x: int = 0
    sub_w: int = 999
    detectable: bool = False


class LauncherApp:
    """One launcher window. Dependencies (process scan, launch, editor) can be injected for tests."""

    def __init__(self, root: tk.Tk, apps_path: Path | str = DEFAULT_APPS, *,
                 scan: Callable[[], Optional[list]] = procs.list_processes,
                 launch_fn: Callable[[App], object] = commands.launch_app,
                 edit_fn: Callable[[str], object] = commands.open_in_editor,
                 stop_fn: Optional[Callable[[App, list], "stopmod.StopResult"]] = None,
                 dark: Optional[bool] = None, ico_path: Optional[Path | str] = ICON_FILE,
                 threaded: bool = True, timers: bool = True, title: str = winutil.WINDOW_TITLE) -> None:
        self.root = root
        self.apps_path = Path(apps_path)
        self.scan = scan
        self.launch_fn = launch_fn
        self.edit_fn = edit_fn
        self.stop_fn = stop_fn or (lambda app, apps: stopmod.stop_app(app, apps, scan=self.scan))
        self.threaded = threaded
        self._forced_dark = dark

        self.apps: list[App] = []
        self.states: list[Optional[bool]] = []
        self.rows: list[_Row] = []
        self.cursor: Optional[int] = None          # keyboard selection
        self.hover: Optional[tuple] = None          # ("row", i) | ("stop", i) | ("all",) | ("edit",)
        self.pressed: Optional[tuple] = None
        self.armed: Optional[int] = None            # row whose Stop was clicked once ("Click again to stop")
        self._armed_app: Optional[App] = None
        self._armed_after: Optional[str] = None
        self._stopping: list[App] = []              # stops in progress
        self._stop_results: list = []               # (app, StopResult) handed over by worker threads
        self._stop_threads: list[threading.Thread] = []
        self.callback_errors = 0
        self.file_message = ("", "hint")             # (text, kind) shown while nothing else is
        self._file_stamp: Optional[tuple] = None
        self._status_after: Optional[str] = None
        self._timers: set[str] = set()
        self._scanning = False
        self._scan_result = None
        self._closed = False
        self._drawn = False
        self._icon_cache: dict[tuple, Optional[tk.PhotoImage]] = {}
        self._images: dict[str, tk.PhotoImage] = {}
        self._keep: list[tk.PhotoImage] = []
        self._btn_rects: dict[str, tuple[int, int, int, int]] = {}

        root.title(title)
        root.resizable(False, False)
        if ico_path and os.path.isfile(str(ico_path)):
            try:
                root.iconbitmap(default=str(ico_path))
            except tk.TclError:
                pass
        self.ico_path = str(ico_path) if ico_path else ""
        root.report_callback_exception = self._on_callback_error

        self.s = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self._make_fonts()
        self.pal: Palette = palette_for(self._is_dark())
        root.configure(bg=self.pal.window)

        self.list = tk.Canvas(root, highlightthickness=0, bd=0, bg=self.pal.window, takefocus=0)
        self.bar = tk.Canvas(root, highlightthickness=0, bd=0, bg=self.pal.window, takefocus=0)
        self.list.pack(side="top", fill="x")
        self.bar.pack(side="top", fill="x")

        self._bind()
        self._build_images()
        self.reload(force=True)
        if not self._drawn:      # apps.json was unreadable: still draw the (empty) list
            self._draw_rows()
        self._draw_bar()
        self._resize(first=True)
        self.show_hint()
        if timers:
            self._later(FILE_EVERY_MS, self._file_tick)
            self._later(RUNNING_EVERY_MS, self._running_tick)
        self.refresh_running()

    # ------------------------------------------------------------------ small helpers
    def _later(self, ms: int, fn: Callable[[], None]) -> str:
        """root.after that is remembered, so closing the window cancels everything still pending."""
        holder: list[str] = []

        def run() -> None:
            self._timers.discard(holder[0])
            if not self._closed:
                fn()

        holder.append(self.root.after(ms, run))
        self._timers.add(holder[0])
        return holder[0]

    def _cancel(self, after_id: Optional[str]) -> None:
        if after_id:
            self._timers.discard(after_id)
            try:
                self.root.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass

    def px(self, n: float) -> int:
        return int(round(n * self.s))

    def _is_dark(self) -> bool:
        if self._forced_dark is not None:
            return self._forced_dark
        return winutil.system_uses_dark_theme()

    def _make_fonts(self) -> None:
        families = set(tkfont.families(self.root))
        semi = "Segoe UI Semibold" if "Segoe UI Semibold" in families else "Segoe UI"
        base = "Segoe UI" if "Segoe UI" in families else tkfont.nametofont("TkDefaultFont").actual("family")
        weight = "normal" if semi != base else "bold"
        self.f_name = tkfont.Font(root=self.root, family=semi, size=10, weight=weight)
        self.f_sub = tkfont.Font(root=self.root, family=base, size=8)
        self.f_btn = tkfont.Font(root=self.root, family=semi, size=10, weight=weight)
        self.f_key = tkfont.Font(root=self.root, family=base, size=8)
        self.f_status = tkfont.Font(root=self.root, family=base, size=9)
        self.f_badge = tkfont.Font(root=self.root, family=semi, size=13, weight=weight)
        self.f_stop = tkfont.Font(root=self.root, family=semi, size=8, weight=weight)

    def _photo(self, png: bytes) -> tk.PhotoImage:
        return tk.PhotoImage(master=self.root, data=gfx.png_b64(png))

    def _fit(self, text: str, font: tkfont.Font, max_w: int) -> str:
        if font.measure(text) <= max_w:
            return text
        while text and font.measure(text + "…") > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    # ------------------------------------------------------------------ images
    def _build_images(self) -> None:
        p, px = self.pal, self.px
        bg = gfx.hex_to_rgb(p.window)
        rgb = gfx.hex_to_rgb
        border = max(1, round(self.s))
        ring = border * 2
        r = px(RADIUS)
        rw, rh = px(W - 2 * PAD), px(ROW_H)
        images: dict[str, tk.PhotoImage] = {}

        def rr(w, h, fill, edge=None, edge_w=0, radius=r, opaque=True):
            png = gfx.rounded_rect_png(w, h, radius, rgb(fill), bg if opaque else None,
                                       rgb(edge) if edge else None, edge_w)
            return self._photo(png)

        images["row"] = rr(rw, rh, p.card, p.card_border, border)
        images["row_hover"] = rr(rw, rh, p.card_hover, p.card_hover_border, border)
        images["row_pressed"] = rr(rw, rh, p.card_pressed, p.card_hover_border, border)
        images["row_focus"] = rr(rw, rh, p.card, p.accent, ring)
        images["row_focus_hover"] = rr(rw, rh, p.card_hover, p.accent, ring)

        all_w = px(200)
        edit_w = px(W - 2 * PAD - 8) - all_w
        bh = px(BTN_H)
        br = px(8)
        for name, w in (("all", all_w), ("edit", edit_w)):
            if name == "all":
                fills = (p.accent, p.accent_hover, p.accent_pressed)
                edge = None
            else:
                fills = (p.secondary, p.secondary_hover, p.secondary_pressed)
                edge = p.card_border
            images[f"{name}"] = rr(w, bh, fills[0], edge, border, br)
            images[f"{name}_hover"] = rr(w, bh, fills[1], edge, border, br)
            images[f"{name}_pressed"] = rr(w, bh, fills[2], edge, border, br)
            images[f"{name}_focus"] = rr(w, bh, fills[0], p.text, ring, br)
        self._btn_w = {"all": all_w, "edit": edit_w}

        images["keycap"] = rr(px(KEYCAP), px(KEYCAP), p.keycap, p.keycap_border, border, px(6), opaque=False)
        images["dot"] = self._photo(gfx.circle_png(px(DOT), rgb(p.running), None))

        # The Stop pill: quiet until hovered, solid red once armed. Transparent corners so it sits
        # on whatever colour the card is (normal / hover / pressed).
        sw, sh, aw, ah, tw = px(STOP_W), px(STOP_H), px(ARMED_W), px(ARMED_H), px(STOPPING_W)
        tint = lambda amount: blend(p.card, p.danger, amount)   # noqa: E731
        images["stop"] = rr(sw, sh, p.card, p.card_border, border, sh // 2, opaque=False)
        images["stop_hover"] = rr(sw, sh, tint(0.10), p.danger, border, sh // 2, opaque=False)
        images["stop_pressed"] = rr(sw, sh, tint(0.22), p.danger, border, sh // 2, opaque=False)
        images["armed"] = rr(aw, ah, p.danger, None, 0, px(10), opaque=False)
        images["armed_hover"] = rr(aw, ah, p.danger_hover, None, 0, px(10), opaque=False)
        images["armed_pressed"] = rr(aw, ah, p.danger_pressed, None, 0, px(10), opaque=False)
        images["stopping"] = rr(tw, sh, p.card_pressed, p.card_border, border, sh // 2, opaque=False)
        self._stop_size = {"idle": (sw, sh), "armed": (aw, ah), "stopping": (tw, sh)}
        self._images = images
        self._icon_cache.clear()

    def _icon_image(self, app: App) -> Optional[tk.PhotoImage]:
        """The app's own icon as an image, or None (a letter badge is drawn instead)."""
        spec = app.icon
        if not spec:
            try:
                plan = commands.build_plan(app, check=False)
            except LaunchError:
                plan = None
            if plan and plan.mode == "exe" and os.path.basename(plan.exe).lower() not in procs.SCRIPT_HOSTS:
                spec = plan.exe
        if not spec:
            return None
        size = self.px(ICON)
        key = (spec, size)
        if key not in self._icon_cache:
            png = gfx.extract_icon_png(spec, size)
            self._icon_cache[key] = self._photo(png) if png else None
        return self._icon_cache[key]

    def _badge_image(self, name: str) -> tk.PhotoImage:
        key = ("badge", name, self.px(ICON))
        if key not in self._icon_cache:
            size = self.px(ICON)
            png = gfx.rounded_rect_png(size, size, size * 0.28, gfx.hex_to_rgb(gfx.badge_color(name)), None)
            self._icon_cache[key] = self._photo(png)
        return self._icon_cache[key]  # type: ignore[return-value]

    # ------------------------------------------------------------------ drawing the list
    def _content_height(self) -> int:
        n = max(len(self.apps), 1)
        return self.px(PAD) + n * self.px(ROW_H) + (n - 1) * self.px(GAP)

    def _max_list_height(self) -> int:
        screen = self.root.winfo_screenheight()
        return max(self.px(ROW_H) * 3, int(screen * 0.75) - self.px(BAR_PAD + BTN_H + STATUS_H + 40))

    def _draw_rows(self) -> None:
        c, p, px = self.list, self.pal, self.px
        c.delete("all")
        self.rows = []
        self._drawn = True
        c.configure(bg=p.window)
        x0 = px(PAD)
        rw, rh = px(W - 2 * PAD), px(ROW_H)

        if not self.apps:
            c.create_text(px(W) // 2, px(PAD) + rh // 2, text="No apps yet - click Edit list to add some.",
                          fill=p.muted, font=self.f_status)
        for i, app in enumerate(self.apps):
            y = px(PAD) + i * (rh + px(GAP))
            row = _Row(y=y)
            row.bg = c.create_image(x0, y, anchor="nw", image=self._images["row"])
            icon_x, icon_y = x0 + px(12), y + (rh - px(ICON)) // 2
            image = self._icon_image(app)
            if image is not None:
                c.create_image(icon_x, icon_y, anchor="nw", image=image)
            else:
                c.create_image(icon_x, icon_y, anchor="nw", image=self._badge_image(app.name))
                c.create_text(icon_x + px(ICON) // 2, icon_y + px(ICON) // 2, text=gfx.badge_letter(app.name),
                              fill="#ffffff", font=self.f_badge)

            has_key = i < 9
            right = x0 + rw - px(12)
            if has_key:
                kx = right - px(KEYCAP)
                c.create_image(kx, y + (rh - px(KEYCAP)) // 2, anchor="nw", image=self._images["keycap"])
                c.create_text(kx + px(KEYCAP) // 2, y + rh // 2, text=str(i + 1), fill=p.muted, font=self.f_key)
                right = kx - px(10)
            # Running entries show [dot][Stop] to the left of the number; entries that cannot be
            # detected (web pages, bare shells) never do, so their name may use that room.
            row.detectable = procs.matcher_for(app) is not None
            row.stop_right = right
            row.dot_x = right - px(STOP_W) - px(10) - px(DOT)
            row.dot = c.create_image(row.dot_x, y + (rh - px(DOT)) // 2, anchor="nw",
                                     image=self._images["dot"], state="hidden")
            row.stop_img = c.create_image(right - px(STOP_W), y + (rh - px(STOP_H)) // 2, anchor="nw",
                                          image=self._images["stop"], state="hidden")
            row.stop_txt = c.create_text(right - px(STOP_W) // 2, y + rh // 2, text="Stop", fill=p.muted,
                                         font=self.f_stop, justify="center", state="hidden")
            text_x = icon_x + px(ICON) + px(12)
            max_w = (row.dot_x if row.detectable else right) - px(10) - text_x
            c.create_text(text_x, y + int(rh * 0.36), anchor="w", text=self._fit(app.name, self.f_name, max_w),
                          fill=p.text, font=self.f_name)
            row.sub = c.create_text(text_x, y + int(rh * 0.69), anchor="w", text="", fill=p.muted, font=self.f_sub)
            row.sub_w = max_w
            self.rows.append(row)

        total = self._content_height()
        view = min(total, self._max_list_height())
        c.configure(width=px(W), height=view, scrollregion=(0, 0, px(W), total), yscrollincrement=px(24))
        self._apply_states()

    def _subtitle(self, i: int) -> tuple[str, str]:
        """(text, colour) for the second line of row *i*."""
        app, state = self.apps[i], self.states[i] if i < len(self.states) else None
        if state is True:
            return "Running", self.pal.running
        if state is False:
            return "Not running", self.pal.muted
        cmd = app.command.lower()
        if cmd.startswith(("http://", "https://")) or cmd.endswith((".html", ".htm")):
            return "Opens in your browser", self.pal.muted
        try:
            if commands.build_plan(app, check=False).is_open:
                return "Opens with its default app", self.pal.muted
        except LaunchError:
            pass
        return "Click to start", self.pal.muted

    def _apply_states(self) -> None:
        for i, row in enumerate(self.rows):
            text, colour = self._subtitle(i)
            self.list.itemconfigure(row.sub, text=self._fit(text, self.f_sub, row.sub_w), fill=colour)
            self._paint_row(i)
            self._paint_stop(i)
        if self.armed is not None and not self._can_stop(self.armed):
            self.disarm()          # the app went away by itself: nothing left to confirm

    def _paint_row(self, i: int) -> None:
        if i >= len(self.rows):
            return
        pressed, hover = self.pressed == ("row", i), self.hover == ("row", i)
        focus = self.cursor == i
        if pressed:
            key = "row_pressed"
        elif focus and hover:
            key = "row_focus_hover"
        elif focus:
            key = "row_focus"
        elif hover:
            key = "row_hover"
        else:
            key = "row"
        self.list.itemconfigure(self.rows[i].bg, image=self._images[key])

    # ------------------------------------------------------------------ the Stop pill
    def _can_stop(self, i: int) -> bool:
        return 0 <= i < len(self.apps) and i < len(self.states) and self.states[i] is True

    def _stop_mode(self, i: int) -> str:
        """"idle" (Stop), "armed" (Click again to stop) or "stopping" (working on it)."""
        if self.apps[i] in self._stopping:
            return "stopping"
        if self.armed == i and self._armed_app == self.apps[i]:
            return "armed"
        return "idle"

    def _stop_box(self, i: int) -> tuple[int, int, int, int]:
        """Where row i's Stop pill is right now, in list-canvas coordinates."""
        row = self.rows[i]
        w, h = self._stop_size[self._stop_mode(i)]
        y0 = row.y + (self.px(ROW_H) - h) // 2
        return row.stop_right - w, y0, row.stop_right, y0 + h

    def _paint_stop(self, i: int) -> None:
        if i >= len(self.rows):
            return
        row, c, p = self.rows[i], self.list, self.pal
        if not self._can_stop(i):
            for item in (row.stop_img, row.stop_txt, row.dot):
                c.itemconfigure(item, state="hidden")
            return
        mode = self._stop_mode(i)
        tag = "_pressed" if self.pressed == ("stop", i) else "_hover" if self.hover == ("stop", i) else ""
        if mode == "idle":
            key, label, colour = "stop" + tag, "Stop", (p.danger if tag else p.muted)
        elif mode == "armed":
            key, label, colour = "armed" + tag, "Click again\nto stop", p.on_danger
        else:
            key, label, colour = "stopping", "Stopping…", p.muted
        x0, y0, x1, y1 = self._stop_box(i)
        c.coords(row.stop_img, x0, y0)
        c.itemconfigure(row.stop_img, image=self._images[key], state="normal")
        c.coords(row.stop_txt, (x0 + x1) // 2, (y0 + y1) // 2)
        c.itemconfigure(row.stop_txt, text=label, fill=colour, state="normal")
        c.itemconfigure(row.dot, state="normal" if mode == "idle" else "hidden")

    # ------------------------------------------------------------------ drawing the bar
    def _draw_bar(self) -> None:
        c, p, px = self.bar, self.pal, self.px
        c.delete("all")
        c.configure(bg=p.window)
        x0, y0 = px(PAD), px(BAR_PAD)
        bh = px(BTN_H)
        all_w, edit_w = self._btn_w["all"], self._btn_w["edit"]
        self._btn_rects = {
            "all": (x0, y0, x0 + all_w, y0 + bh),
            "edit": (x0 + all_w + px(8), y0, x0 + all_w + px(8) + edit_w, y0 + bh),
        }
        self._btn_items: dict[str, int] = {}
        for name, label, colour in (("all", "Launch all", p.on_accent), ("edit", "Edit list", p.text)):
            bx0, by0, bx1, by1 = self._btn_rects[name]
            self._btn_items[name] = c.create_image(bx0, by0, anchor="nw", image=self._images[name])
            c.create_text((bx0 + bx1) // 2, (by0 + by1) // 2, text=label, fill=colour, font=self.f_btn)
        bx0, by0, bx1, by1 = self._btn_rects["all"]
        c.create_text(bx1 - px(14), (by0 + by1) // 2, anchor="e", text="A", font=self.f_key,
                      fill=blend(p.on_accent, p.accent, 0.35))
        self.status_id = c.create_text(x0, y0 + bh + px(10), anchor="nw", width=px(W - 2 * PAD),
                                       text="", font=self.f_status, fill=p.muted)
        c.configure(width=px(W), height=y0 + bh + px(10) + px(STATUS_H) - px(6))

    def _paint_button(self, name: str) -> None:
        if self.pressed == (name,):
            key = f"{name}_pressed"
        elif self.hover == (name,):
            key = f"{name}_hover"
        else:
            key = name
        self.bar.itemconfigure(self._btn_items[name], image=self._images[key])

    # ------------------------------------------------------------------ window size / chrome
    def _resize(self, first: bool = False) -> None:
        self.root.update_idletasks()
        width, height = self.px(W), self.list.winfo_reqheight() + self.bar.winfo_reqheight()
        if first:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{width}x{height}+{max(0, (sw - width) // 2)}+{max(0, (sh - height) // 3)}")
        else:
            self.root.geometry(f"{width}x{height}")
        self._apply_chrome()

    def _apply_chrome(self) -> None:
        try:
            hwnd = winutil.toplevel_hwnd(self.root)
            winutil.set_dark_title_bar(hwnd, self.pal.dark)
            if self.ico_path:
                winutil.set_window_icons(hwnd, self.ico_path)
        except Exception:
            log.exception("window chrome")

    # ------------------------------------------------------------------ status line
    def show_status(self, text: str, kind: str = "ok", ms: Optional[int] = None) -> None:
        colour = {"error": self.pal.error, "ok": self.pal.text}.get(kind, self.pal.muted)
        self.bar.itemconfigure(self.status_id, text=self._clip(text), fill=colour)
        self._cancel(self._status_after)
        self._status_after = None
        if ms is None:
            ms = HINT_DELAY_ERR if kind == "error" else HINT_DELAY_OK
        if ms > 0:
            self._status_after = self._later(ms, self.show_hint)

    def show_hint(self) -> None:
        """Back to the resting line: a problem with apps.json if there is one, else the key hints."""
        self._status_after = None
        text, kind = self.file_message
        if not text:
            n = min(len(self.apps), 9)
            keys = ("1" if n == 1 else f"1–{n}") if n else ""
            text = ((f"{keys} launch   ·   Shift+{keys} stop   ·   " if keys else "")
                    + "A launch all   ·   Esc close")
            kind = "hint"
        self.bar.itemconfigure(self.status_id, text=self._clip(text),
                               fill=self.pal.error if kind == "error" else self.pal.muted)

    def _clip(self, text: str) -> str:
        """Keep the status to two lines."""
        limit = 2 * int(self.px(W - 2 * PAD) / max(1, self.f_status.measure("n")) * 1.15)
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    # ------------------------------------------------------------------ loading apps.json
    def _stamp(self) -> Optional[tuple]:
        try:
            st = self.apps_path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def reload(self, force: bool = False) -> bool:
        """Re-read apps.json if it changed. Returns True if the visible list changed."""
        stamp = self._stamp()
        if not force and stamp == self._file_stamp:
            return False
        self._file_stamp = stamp
        result = config.load_apps(self.apps_path)
        if not result.ok:
            self.file_message = (result.message + (" - showing the previous list." if self.apps else ""), "error")
            log.warning("apps.json not loaded: %s", result.message)
            self._refresh_hint_if_idle()
            return False
        problems = result.errors + result.warnings
        self.file_message = ((problems[0] + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")),
                             "error" if result.errors else "hint") if problems else ("", "hint")
        for problem in problems:
            log.warning("apps.json: %s", problem)
        changed = result.apps != self.apps
        self.apps = result.apps
        if changed or force:
            self.disarm(repaint=False)
            self.states = [None] * len(self.apps)
            self.cursor = None if self.cursor is None or self.cursor >= len(self.apps) else self.cursor
            self.hover = self.pressed = None
            self._draw_rows()
            if not force:
                self._draw_bar()  # hint text lives on the bar
                self._resize()
                self.refresh_running()
        self._refresh_hint_if_idle()
        return changed

    def _refresh_hint_if_idle(self) -> None:
        if self._status_after is None and hasattr(self, "status_id"):
            self.show_hint()

    def _file_tick(self) -> None:
        try:
            self._check_theme()
            self.reload()
        except Exception:
            log.exception("file tick")
        self._later(FILE_EVERY_MS, self._file_tick)

    def _check_theme(self) -> None:
        dark = self._is_dark()
        if dark != self.pal.dark:
            self.set_theme(dark)

    def set_theme(self, dark: bool) -> None:
        self.pal = palette_for(dark)
        self.root.configure(bg=self.pal.window)
        self._build_images()
        self._draw_rows()
        self._draw_bar()
        self._resize()
        self.show_hint()

    # ------------------------------------------------------------------ running detection
    def _running_tick(self) -> None:
        try:
            if self.root.state() != "iconic":
                self.refresh_running()
        except Exception:
            log.exception("running tick")
        self._later(RUNNING_EVERY_MS, self._running_tick)

    def refresh_running(self) -> None:
        if self._scanning or self._closed:
            return
        if not self.threaded:
            self._apply_scan(self._safe_scan())
            return
        self._scanning = True
        self._scan_result = None

        def work() -> None:
            self._scan_result = (self._safe_scan(),)

        threading.Thread(target=work, name="scan", daemon=True).start()
        self._later(40, self._poll_scan)

    def _safe_scan(self):
        try:
            return self.scan()
        except Exception:
            log.exception("process scan")
            return None

    def _poll_scan(self) -> None:
        if self._scan_result is None:
            self._later(40, self._poll_scan)
            return
        (found,) = self._scan_result
        self._scanning = False
        self._apply_scan(found)

    def _apply_scan(self, found) -> None:
        states = procs.running_states(self.apps, found, ignore_pid=os.getpid())
        if states != self.states:
            self.states = states
            self._apply_states()

    def _after_launch(self) -> None:
        for ms in (1500, 4500):
            self._later(ms, self.refresh_running)

    # ------------------------------------------------------------------ actions
    def launch_index(self, i: int) -> None:
        if not 0 <= i < len(self.apps):
            return
        self.disarm()
        app = self.apps[i]
        try:
            self.launch_fn(app)
        except LaunchError as exc:
            log.warning("launch %s failed: %s", app.name, exc)
            self.show_status(f"Could not start {app.name}: {exc}", "error")
        except Exception as exc:  # never crash the window over one app
            log.exception("launch %s", app.name)
            self.show_status(f"Could not start {app.name}: {type(exc).__name__}: {exc}", "error")
        else:
            log.info("launched %s", app.name)
            self.show_status(f"Started {app.name}", "ok")
            self._after_launch()

    def launch_everything(self) -> None:
        self.disarm()
        found = self._safe_scan()
        states = procs.running_states(self.apps, found, ignore_pid=os.getpid())
        result = commands.launch_all(self.apps, states, self.launch_fn)
        for name, why in result.failed:
            log.warning("launch all: %s failed: %s", name, why)
        log.info("launch all: launched=%s skipped=%s failed=%s", result.launched, result.skipped, result.failed)
        message = result.summary()
        if result.excluded:
            message = message.rstrip(".") + f", {len(result.excluded)} not in Launch all."
        if result.failed:
            first_name, first_why = result.failed[0]
            message += f" {first_name}: {first_why}"
        self.show_status(message, "error" if result.failed else "ok")
        if found is not None:
            self.states = states
            self._apply_states()
        self._after_launch()

    def edit_list(self) -> None:
        config.ensure_file(self.apps_path)
        try:
            self.edit_fn(str(self.apps_path))
        except LaunchError as exc:
            self.show_status(f"Could not open {self.apps_path.name}: {exc}", "error")
        else:
            self.show_status(f"Opened {self.apps_path.name} - save it and the list updates.", "ok")

    # ------------------------------------------------------------------ stopping
    def arm(self, i: int, via_key: bool = False) -> None:
        """First click: the pill turns into "Click again to stop" for ARM_MS."""
        self.disarm()
        self.armed, self._armed_app = i, self.apps[i]
        self._armed_after = self._later(ARM_MS, self.disarm)
        self._paint_stop(i)
        if via_key:
            self.show_status(f"Press Shift+{i + 1} again to stop {self.apps[i].name}", "hint", ARM_MS)

    def disarm(self, repaint: bool = True) -> None:
        self._cancel(self._armed_after)
        self._armed_after = None
        old, self.armed, self._armed_app = self.armed, None, None
        if repaint and old is not None and old < len(self.rows):
            self._paint_stop(old)

    def stop_clicked(self, i: int, via_key: bool = False) -> None:
        """A click (or Shift+digit) on row i's Stop: the first arms it, the second stops the app."""
        if not 0 <= i < len(self.apps):
            return
        app = self.apps[i]
        if app in self._stopping:
            return
        if not self._can_stop(i):
            if via_key:
                if self.rows and not self.rows[i].detectable:
                    self.show_status(f"{app.name} cannot be detected as running, so it cannot be stopped here",
                                     "hint")
                else:
                    self.show_status(f"{app.name} is not running", "hint")
            return
        if self.armed == i and self._armed_app == app:
            self.disarm()
            self._begin_stop(i)
        else:
            self.arm(i, via_key)

    def _begin_stop(self, i: int) -> None:
        app = self.apps[i]
        self._stopping.append(app)
        snapshot = list(self.apps)
        self._paint_stop(i)
        self.show_status(f"Stopping {app.name}…", "ok", 0)
        log.info("stopping %s", app.name)
        if not self.threaded:
            self._finish_stop(app, self._run_stop(app, snapshot))
            return

        def work() -> None:
            self._stop_results.append((app, self._run_stop(app, snapshot)))

        worker = threading.Thread(target=work, name=f"stop-{app.name}", daemon=True)
        self._stop_threads.append(worker)
        worker.start()
        self._later(100, self._poll_stops)

    def _run_stop(self, app: App, snapshot: list):
        try:
            return self.stop_fn(app, snapshot)
        except Exception as exc:  # the window must survive whatever the stop does
            log.exception("stop %s", app.name)
            return stopmod.StopResult(False, f"Could not stop {app.name}: {type(exc).__name__}: {exc}", "error")

    def _poll_stops(self) -> None:
        while self._stop_results:
            app, result = self._stop_results.pop(0)
            self._finish_stop(app, result)
        if self._stopping:
            self._later(100, self._poll_stops)

    def _finish_stop(self, app: App, result) -> None:
        if app in self._stopping:
            self._stopping.remove(app)
        log.info("stop %s: %s", app.name, result.message)
        self.show_status(result.message, "error" if result.level == "error" else "ok",
                         8000 if result.level == "warn" else None)
        try:
            index = self.apps.index(app)
        except ValueError:
            index = None
        if index is not None and result.ok and index < len(self.states):
            self.states[index] = False      # the dot goes out at once; the rescan below confirms it
        self._apply_states()
        self.refresh_running()
        self._after_launch()

    def close(self) -> None:
        self._closed = True
        for after_id in list(self._timers):
            self._cancel(after_id)
        if any(t.is_alive() for t in self._stop_threads):
            # Closing the window must not leave a half-finished stop (a parent ended, its children not):
            # hide the window, then give the stop a few seconds to finish before the process exits.
            try:
                self.root.withdraw()
            except tk.TclError:
                pass
            deadline = time.monotonic() + CLOSE_GRACE_S
            for worker in self._stop_threads:
                worker.join(max(0.0, deadline - time.monotonic()))
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ input
    def _bind(self) -> None:
        r = self.root
        r.bind("<Key>", self._on_key)
        r.bind("<FocusIn>", self._on_focus_in)
        r.protocol("WM_DELETE_WINDOW", self.close)
        for canvas in (self.list, self.bar):
            canvas.bind("<Motion>", self._on_motion)
            canvas.bind("<Leave>", self._on_leave)
            canvas.bind("<ButtonPress-1>", self._on_press)
            canvas.bind("<ButtonRelease-1>", self._on_release)
        self.list.bind("<MouseWheel>", self._on_wheel)

    def _on_focus_in(self, event) -> None:
        if event.widget is self.root:
            self.reload()
            self.refresh_running()

    def _on_wheel(self, event) -> None:
        if self._content_height() > int(self.list.cget("height")):
            self.list.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def press_keys(self, keys: str) -> None:
        """Feed characters as if typed (used by the smoke test)."""
        for ch in keys:
            self.handle_char(ch)

    def handle_char(self, ch: str) -> bool:
        if ch in "123456789" and ch != "":
            index = int(ch) - 1
            if index < len(self.apps):
                self.launch_index(index)
                return True
            return False
        if ch.lower() == "a":
            self.launch_everything()
            return True
        return False

    def _on_key(self, event) -> None:
        if event.state & 0x4 or event.state & 0x20000:  # Ctrl or Alt held: not ours
            return
        sym = event.keysym
        code = getattr(event, "keycode", 0)
        if event.state & 0x1 and 49 <= code <= 57 and not (event.char or "").isdigit():
            self.stop_clicked(code - 49, via_key=True)   # Shift+1..9: first press arms, second stops
            return
        if sym == "Escape":
            self.close()
        elif sym in ("Up", "Down"):
            if not self.apps:
                return
            step = -1 if sym == "Up" else 1
            self._move_cursor(0 if self.cursor is None and step > 0 else
                              len(self.apps) - 1 if self.cursor is None else
                              (self.cursor + step) % len(self.apps))
        elif sym in ("Home", "End") and self.apps:
            self._move_cursor(0 if sym == "Home" else len(self.apps) - 1)
        elif sym in ("Return", "KP_Enter", "space") and self.cursor is not None:
            self.launch_index(self.cursor)
        elif event.char:
            self.handle_char(event.char)

    def _move_cursor(self, index: int) -> None:
        old, self.cursor = self.cursor, index
        if old is not None:
            self._paint_row(old)
        self._paint_row(index)
        self._scroll_to(index)

    def _scroll_to(self, index: int) -> None:
        total, view = self._content_height(), int(self.list.cget("height"))
        if total <= view:
            return
        top = self.rows[index].y - self.px(PAD)
        self.list.yview_moveto(max(0.0, min(1.0, top / total)))

    def _hit(self, event) -> Optional[tuple]:
        if event.widget is self.bar:
            for name, (x0, y0, x1, y1) in self._btn_rects.items():
                if x0 <= event.x < x1 and y0 <= event.y < y1:
                    return (name,)
            return None
        x, y = event.x, self.list.canvasy(event.y)
        rh, gap = self.px(ROW_H), self.px(GAP)
        if not (self.px(PAD) <= x < self.px(W - PAD)):
            return None
        rel = y - self.px(PAD)
        if rel < 0:
            return None
        index, offset = divmod(int(rel), rh + gap)
        if offset < rh and index < len(self.apps):
            if self._can_stop(index) and index < len(self.rows):
                x0, y0, x1, y1 = self._stop_box(index)
                if x0 <= x < x1 and y0 <= y < y1:
                    return ("stop", index)       # the pill is its own button, not part of the launch row
            return ("row", index)
        return None

    def _set_hover(self, hit: Optional[tuple]) -> None:
        if hit == self.hover:
            return
        old, self.hover = self.hover, hit
        self._repaint(old)
        self._repaint(hit)

    def _repaint(self, target: Optional[tuple]) -> None:
        if not target:
            return
        if target[0] == "row":
            self._paint_row(target[1])
        elif target[0] == "stop":
            self._paint_stop(target[1])
        else:
            self._paint_button(target[0])

    def _on_motion(self, event) -> None:
        hit = self._hit(event)
        self._set_hover(hit)
        event.widget.configure(cursor="hand2" if hit else "")

    def _on_leave(self, event) -> None:
        self._set_hover(None)
        event.widget.configure(cursor="")

    def _on_press(self, event) -> None:
        hit = self._hit(event)
        self.pressed = hit
        self.cursor = None if hit and hit[0] == "row" else self.cursor
        self._repaint(hit)

    def _on_release(self, event) -> None:
        pressed, self.pressed = self.pressed, None
        self._repaint(pressed)
        hit = self._hit(event)
        if not pressed or hit != pressed:
            return
        if pressed[0] == "row":
            self.launch_index(pressed[1])
        elif pressed[0] == "stop":
            self.stop_clicked(pressed[1])
        elif pressed[0] == "all":
            self.launch_everything()
        elif pressed[0] == "edit":
            self.edit_list()

    # ------------------------------------------------------------------ misc
    def _on_callback_error(self, exc, val, tb) -> None:
        self.callback_errors += 1
        log.error("UI callback failed", exc_info=(exc, val, tb))
        try:
            self.show_status(f"Something went wrong: {val}", "error")
        except Exception:
            pass

    def snapshot(self, path: Path | str) -> bool:
        """Save a PNG of the window (smoke-test aid for checking the layout)."""
        self.root.update()
        shot = winutil.capture_window_bgra(winutil.toplevel_hwnd(self.root))
        if not shot:
            return False
        w, h, bgra = shot
        rgb = bytearray(w * h * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
        Path(path).write_bytes(gfx.png_encode(w, h, bytes(rgb), channels=3))
        return True

    def report(self) -> str:
        running = [a.name for a, s in zip(self.apps, self.states) if s]
        return (f"rows={len(self.rows)} theme={self.pal.name} scale={self.s:.2f} "
                f"running={running} callback_errors={self.callback_errors} note={self.file_message[0]!r}")


# ============================================================================= entry point
def _setup_logging() -> None:
    # LAUNCHER_LOG lets the tests keep their runs out of the real launcher.log.
    path = os.environ.get("LAUNCHER_LOG") or LOG_FILE
    try:
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=100_000, backupCount=1, encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _parse(argv: Optional[list[str]]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="launcher", description="One-click launcher for my tools.")
    ap.add_argument("--apps", default=str(DEFAULT_APPS), help="path to apps.json (default: next to the launcher)")
    ap.add_argument("--smoke-test", nargs="?", const=2.0, type=float, metavar="SECONDS",
                    help="build the window, close it after SECONDS (default 2) and exit 0 if nothing went wrong")
    ap.add_argument("--press", default="", help="with --smoke-test: keys to press once the window is up, e.g. 1")
    ap.add_argument("--snapshot", default="", help="with --smoke-test: save a PNG of the window here")
    ap.add_argument("--invisible", action="store_true", help="with --smoke-test: keep the window fully transparent")
    ap.add_argument("--single-instance", action="store_true",
                    help="with --smoke-test: behave like a normal start (one window at a time)")
    ap.add_argument("--instance-id", default="",
                    help="testing aid: use a separate single-instance lock and window title, so a test run "
                         "never meets (or focuses) the real launcher")
    ap.add_argument("--theme", choices=("light", "dark"), help="force a theme instead of following Windows")
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse(argv)
    _setup_logging()
    smoke = args.smoke_test is not None
    try:
        winutil.enable_dpi_awareness()
        winutil.set_app_user_model_id()
        tag = args.instance_id.strip()
        title = winutil.WINDOW_TITLE + (f" [{tag}]" if tag else "")
        lock = "Local\\ClodCode.AppLauncher.Instance" + (f".{tag}" if tag else "")
        if (not smoke or args.single_instance) and not winutil.acquire_single_instance(lock):
            winutil.focus_existing_window(title)
            log.info("already open - brought the existing window forward")
            if smoke and sys.stdout:
                print("already open - brought the existing window forward")
            return 0
        apps_path = Path(args.apps)
        if apps_path == DEFAULT_APPS:
            config.ensure_file(apps_path)
        root = tk.Tk()
        root.withdraw()  # build the whole window first so it never flashes up half-drawn
        if smoke and args.invisible:
            root.attributes("-alpha", 0.0)
        app = LauncherApp(root, apps_path, dark=None if not args.theme else args.theme == "dark", title=title)
        root.deiconify()
        root.lift()
        root.focus_force()
        log.info("started (%s)", app.report())
        if smoke:
            def script() -> None:
                if args.press:
                    app.press_keys(args.press)
                if args.snapshot:
                    ok = app.snapshot(args.snapshot)
                    log.info("snapshot %s -> %s", args.snapshot, "saved" if ok else "FAILED")

            root.after(int(min(args.smoke_test * 1000, 1000)), script)
            root.after(int(args.smoke_test * 1000), app.close)
        root.mainloop()
        summary = app.report()
        log.info("closed (%s)", summary)
        if smoke:
            log.info("smoke test %s", "FAILED" if app.callback_errors else "passed")
            if sys.stdout:
                print(("smoke test FAILED: " if app.callback_errors else "smoke test passed: ") + summary)
        return 1 if (smoke and app.callback_errors) else 0
    except Exception as exc:
        log.exception("fatal")
        if smoke:
            if sys.stdout:
                print(f"smoke test FAILED: {type(exc).__name__}: {exc}")
            return 1
        winutil.show_error_box(f"The launcher hit an error and has to close:\n\n{type(exc).__name__}: {exc}\n\n"
                               f"Details are in {LOG_FILE}")
        return 1
