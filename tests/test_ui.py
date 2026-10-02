"""The window itself, driven without showing it (the Tk root stays withdrawn)."""
import json
import re
import subprocess
import sys
import time
import tkinter as tk
import unittest
from types import SimpleNamespace

import support
from applauncher import commands, procs
from applauncher.commands import CREATE_NO_WINDOW, LaunchError
from applauncher.config import App
from applauncher.procs import Proc
from applauncher.ui import BAR_PAD, GAP, PAD, ROW_H, LauncherApp


def entry(name, **extra):
    return {"name": name, "command": r"C:\Windows\System32\notepad.exe", "match": [f"mark-{name}"], **extra}


class Recorder:
    def __init__(self, fail=None):
        self.apps = []
        self.fail = fail or {}

    def __call__(self, app):
        if app.name in self.fail:
            raise self.fail[app.name]
        self.apps.append(app.name)


def key(keysym="", char="", state=0):
    return SimpleNamespace(keysym=keysym, char=char, state=state)


class UiCase(support.TmpDirTestCase):
    names = ("Alpha", "Beta", "Gamma")

    def setUp(self):
        super().setUp()
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"no display: {exc}")
        self.root.withdraw()
        self.addCleanup(self.destroy_root)
        self.path = self.write_json("apps.json", {"apps": [entry(n) for n in self.names]})
        self.launched = Recorder()
        self.edited = []
        self.running = []          # Proc list returned by the fake scan

    @staticmethod
    def never_stop(app, apps):
        """Tests with pretend processes must never reach the real stop code (it would end real pids)."""
        raise AssertionError(f"unexpected stop of {app.name}")

    def destroy_root(self):
        try:
            if getattr(self, "app", None):
                self.app.close()
            self.root.destroy()
        except tk.TclError:
            pass

    def make(self, **kw):
        options = dict(scan=lambda: list(self.running), launch_fn=self.launched, edit_fn=self.edited.append,
                       stop_fn=self.never_stop, dark=False, threaded=False, timers=False,
                       ico_path=support.ROOT / "launcher.ico")
        options.update(kw)
        self.app = LauncherApp(self.root, self.path, **options)
        return self.app

    def status(self):
        return self.app.bar.itemcget(self.app.status_id, "text")

    def click(self, widget, x, y):
        ev = SimpleNamespace(widget=widget, x=x, y=y, delta=0)
        self.app._on_press(ev)
        self.app._on_release(ev)

    def row_center(self, index):
        a = self.app
        return a.px(PAD) + 30, a.px(PAD) + index * (a.px(ROW_H) + a.px(GAP)) + a.px(ROW_H) // 2


class BuildTests(UiCase):
    def test_one_row_per_app_and_window_fits_them(self):
        app = self.make()
        self.assertEqual([a.name for a in app.apps], list(self.names))
        self.assertEqual(len(app.rows), 3)
        self.assertEqual(self.root.title(), "App Launcher")
        self.assertGreater(self.root.winfo_reqheight(), 3 * app.px(ROW_H))

    def test_icons_are_real_images_or_letter_badges(self):
        # notepad.exe has its own icon -> an image; a page has none -> a badge with a letter
        self.path = self.write_json("apps.json", {"apps": [
            entry("Prog"), {"name": "Zed", "command": str(self.write_text("p.html", "<html>"))}]})
        app = self.make()
        texts = [app.list.itemcget(i, "text") for i in app.list.find_all() if app.list.type(i) == "text"]
        self.assertIn("Z", texts)          # the page got a letter badge
        self.assertNotIn("P", texts)       # the program got its real icon, so no badge letter

    def test_long_names_are_ellipsised_not_overflowing(self):
        long_name = "An extraordinarily long application name that cannot possibly fit on one row"
        self.path = self.write_json("apps.json", {"apps": [entry(long_name)]})
        app = self.make()
        texts = [app.list.itemcget(i, "text") for i in app.list.find_all() if app.list.type(i) == "text"]
        shown = [t for t in texts if t.startswith("An extraordinarily")]
        self.assertEqual(len(shown), 1)
        self.assertTrue(shown[0].endswith("\u2026"))

    def test_empty_list_shows_a_hint_not_a_crash(self):
        self.path = self.write_json("apps.json", {"apps": []})
        app = self.make()
        self.assertEqual(app.rows, [])
        texts = [app.list.itemcget(i, "text") for i in app.list.find_all() if app.list.type(i) == "text"]
        self.assertTrue(any("Edit list" in t for t in texts))
        app.handle_char("1")
        app.launch_everything()
        self.assertEqual(self.launched.apps, [])

    def test_more_than_nine_apps_only_the_first_nine_have_keys(self):
        self.path = self.write_json("apps.json", {"apps": [entry(f"App{i}") for i in range(1, 13)]})
        app = self.make()
        self.assertEqual(len(app.rows), 12)
        texts = [app.list.itemcget(i, "text") for i in app.list.find_all() if app.list.type(i) == "text"]
        self.assertIn("9", texts)
        self.assertNotIn("10", texts)
        app.handle_char("9")
        self.assertEqual(self.launched.apps, ["App9"])
        app.list.yview_moveto(1.0)   # scrolling a long list must not fail
        app._move_cursor(11)

    def test_hint_line_lists_the_keys(self):
        app = self.make()
        self.assertIn("1\u20133 launch", self.status())
        self.assertIn("A launch all", self.status())
        self.assertIn("Esc close", self.status())


class KeyboardTests(UiCase):
    def test_digits_launch_the_matching_app(self):
        app = self.make()
        app._on_key(key("2", "2"))
        app._on_key(key("1", "1"))
        self.assertEqual(self.launched.apps, ["Beta", "Alpha"])

    def test_digit_beyond_the_list_does_nothing(self):
        app = self.make()
        app._on_key(key("9", "9"))
        app._on_key(key("0", "0"))
        self.assertEqual(self.launched.apps, [])

    def test_a_launches_all_in_either_case(self):
        app = self.make()
        app._on_key(key("a", "a"))
        app._on_key(key("A", "A", state=1))
        self.assertEqual(self.launched.apps, ["Alpha", "Beta", "Gamma"] * 2)

    def test_escape_closes_the_window(self):
        app = self.make()
        app._on_key(key("Escape", "\x1b"))
        self.assertTrue(app._closed)
        with self.assertRaises(tk.TclError):
            self.root.state()      # the window is gone

    def test_ctrl_and_alt_combinations_are_ignored(self):
        app = self.make()
        app._on_key(key("1", "1", state=0x4))
        app._on_key(key("a", "a", state=0x20000))
        self.assertEqual(self.launched.apps, [])

    def test_arrows_move_a_cursor_and_enter_launches_it(self):
        app = self.make()
        app._on_key(key("Return", "\r"))                 # no cursor yet: nothing happens
        self.assertEqual(self.launched.apps, [])
        app._on_key(key("Down"))
        app._on_key(key("Down"))
        self.assertEqual(app.cursor, 1)
        app._on_key(key("Return", "\r"))
        app._on_key(key("Up"))
        app._on_key(key("space", " "))
        app._on_key(key("Up"))                            # wraps round to the last
        self.assertEqual(app.cursor, 2)
        self.assertEqual(self.launched.apps, ["Beta", "Alpha"])


class LaunchTests(UiCase):
    def test_launch_all_skips_the_running_and_says_so(self):
        self.running = [Proc(1, "x.exe", "x.exe", "x.exe mark-Beta")]
        app = self.make()
        app.launch_everything()
        self.assertEqual(self.launched.apps, ["Alpha", "Gamma"])
        self.assertIn("Launched 2", self.status())
        self.assertIn("1 already running", self.status())
        self.assertEqual(app.states, [False, True, False])

    def test_launch_all_leaves_out_opted_out_entries_and_mentions_them(self):
        self.path = self.write_json("apps.json", {"apps": [entry("Alpha"), entry("Page", launch_all=False)]})
        app = self.make()
        app.launch_everything()
        self.assertEqual(self.launched.apps, ["Alpha"])
        self.assertIn("1 not in Launch all", self.status())

    def test_failed_launch_shows_a_short_message_and_the_window_survives(self):
        self.launched = Recorder(fail={"Beta": LaunchError("file not found: C:\\gone.exe")})
        app = self.make()
        app.launch_index(1)
        self.assertIn("Could not start Beta", self.status())
        self.assertIn("file not found", self.status())
        self.assertEqual(app.callback_errors, 0)
        app.launch_index(0)                               # still works afterwards
        self.assertEqual(self.launched.apps, ["Alpha"])
        self.assertIn("Started Alpha", self.status())

    def test_unexpected_exception_is_contained_too(self):
        self.launched = Recorder(fail={"Alpha": RuntimeError("boom")})
        app = self.make()
        app.launch_index(0)
        self.assertIn("RuntimeError: boom", self.status())

    def test_launch_all_reports_failures_and_still_starts_the_rest(self):
        self.launched = Recorder(fail={"Alpha": LaunchError("access denied")})
        app = self.make()
        app.launch_everything()
        self.assertEqual(self.launched.apps, ["Beta", "Gamma"])
        self.assertIn("failed: Alpha", self.status())
        self.assertIn("access denied", self.status())

    def test_mouse_click_on_a_row_launches_it(self):
        app = self.make()
        x, y = self.row_center(2)
        self.click(app.list, x, y)
        self.assertEqual(self.launched.apps, ["Gamma"])

    def test_click_in_the_gap_between_rows_does_nothing(self):
        app = self.make()
        x, y = self.row_center(0)
        self.click(app.list, x, y + app.px(ROW_H) // 2 + app.px(GAP) // 2)
        self.assertEqual(self.launched.apps, [])

    def test_press_on_one_row_and_release_on_another_cancels(self):
        app = self.make()
        x0, y0 = self.row_center(0)
        x1, y1 = self.row_center(1)
        app._on_press(SimpleNamespace(widget=app.list, x=x0, y=y0))
        app._on_release(SimpleNamespace(widget=app.list, x=x1, y=y1))
        self.assertEqual(self.launched.apps, [])

    def test_launch_all_button_and_edit_button(self):
        app = self.make()
        x0, y0, x1, y1 = app._btn_rects["all"]
        self.click(app.bar, (x0 + x1) // 2, (y0 + y1) // 2)
        self.assertEqual(self.launched.apps, list(self.names))
        x0, y0, x1, y1 = app._btn_rects["edit"]
        self.click(app.bar, (x0 + x1) // 2, (y0 + y1) // 2)
        self.assertEqual(self.edited, [str(self.path)])
        self.assertIn("Opened apps.json", self.status())

    def test_edit_list_recreates_a_deleted_file_before_opening_it(self):
        app = self.make()
        self.path.unlink()
        app.edit_list()
        self.assertTrue(self.path.exists())
        self.assertEqual(self.edited, [str(self.path)])

    def test_real_launch_of_a_dummy_entry_end_to_end(self):
        marker = self.tmp / "m.json"
        self.path = self.write_json("apps.json", {"apps": [{
            "name": "Dummy", "command": str(support.pythonw()), "args": [str(support.DUMMY), str(marker), "hi there"],
            "working_dir": str(self.tmp)}]})
        app = self.make(launch_fn=commands.launch_app)
        app.handle_char("1")
        info = support.wait_for(marker)
        self.assertIsNotNone(info, self.status())
        self.assertEqual(info["args"], ["hi there"])
        self.assertIn("Started Dummy", self.status())

    def test_real_launch_error_for_a_missing_file_is_shown_not_raised(self):
        self.path = self.write_json("apps.json", {"apps": [{"name": "Gone", "command": str(self.tmp / "gone.exe")}]})
        app = self.make(launch_fn=commands.launch_app)
        app.handle_char("1")
        self.assertIn("Could not start Gone: program not found", self.status())


class RunningDotTests(UiCase):
    def test_dot_and_label_follow_the_scan(self):
        self.running = [Proc(7, "x.exe", "x.exe", "x.exe mark-Gamma")]
        app = self.make()
        app.refresh_running()
        dots = [app.list.itemcget(r.dot, "state") for r in app.rows]
        self.assertEqual(dots, ["hidden", "hidden", "normal"])
        self.assertEqual(app.list.itemcget(app.rows[2].sub, "text"), "Running")
        self.assertEqual(app.list.itemcget(app.rows[0].sub, "text"), "Not running")
        self.running = []
        app.refresh_running()
        self.assertEqual([app.list.itemcget(r.dot, "state") for r in app.rows], ["hidden"] * 3)

    def test_failed_scan_leaves_dots_unknown_instead_of_wrong(self):
        app = self.make(scan=lambda: None)
        app.refresh_running()
        self.assertEqual(app.states, [None, None, None])
        self.assertEqual(app.list.itemcget(app.rows[0].sub, "text"), "Click to start")

    def test_a_crashing_scan_is_survived(self):
        def bad():
            raise OSError("nope")
        app = self.make(scan=bad)
        app.refresh_running()
        self.assertEqual(app.states, [None, None, None])
        self.assertEqual(app.callback_errors, 0)

    def test_threaded_scan_delivers_its_result(self):
        self.running = [Proc(7, "x.exe", "x.exe", "x.exe mark-Alpha")]
        app = self.make(threaded=True)
        for _ in range(200):
            self.root.update()
            if app.states == [True, False, False]:
                break
            time.sleep(0.02)
        self.assertEqual(app.states, [True, False, False])

    def test_page_entries_say_where_they_open(self):
        page = self.write_text("p.html", "<html>")
        self.path = self.write_json("apps.json", {"apps": [{"name": "Page", "command": str(page)}]})
        app = self.make()
        self.assertEqual(app.list.itemcget(app.rows[0].sub, "text"), "Opens in your browser")


class ReloadTests(UiCase):
    def test_edit_is_picked_up_and_unchanged_file_is_not_redrawn(self):
        app = self.make()
        self.assertFalse(app.reload())
        self.path.write_text(json.dumps({"apps": [entry("Only")]}), encoding="utf-8")
        self.assertTrue(app.reload())
        self.assertEqual([a.name for a in app.apps], ["Only"])
        self.assertEqual(len(app.rows), 1)
        self.assertFalse(app.reload())

    def test_window_height_follows_the_list(self):
        app = self.make()
        before = int(app.list.cget("height"))
        self.path.write_text(json.dumps({"apps": [entry(f"A{i}") for i in range(6)]}), encoding="utf-8")
        app.reload()
        self.root.update_idletasks()
        self.assertGreater(int(app.list.cget("height")), before)
        self.assertEqual(self.root.winfo_reqheight(), app.list.winfo_reqheight() + app.bar.winfo_reqheight())

    def test_broken_file_keeps_the_last_good_list_and_says_why_then_recovers(self):
        app = self.make()
        good = self.path.read_text(encoding="utf-8")
        self.path.write_text(good.replace("[", "[ ,", 1), encoding="utf-8")
        app.reload()
        self.assertEqual([a.name for a in app.apps], list(self.names))
        self.assertIn("not valid JSON", self.status())
        self.assertIn("previous list", self.status())
        self.path.write_text(good, encoding="utf-8")
        app.reload()
        self.assertIn("1\u20133 launch", self.status())     # message gone, hint back

    def test_bad_entries_are_reported_but_good_ones_show(self):
        self.path.write_text(json.dumps({"apps": [entry("Good"), {"name": "NoCommand"}]}), encoding="utf-8")
        app = self.make()
        self.assertEqual([a.name for a in app.apps], ["Good"])
        self.assertIn("NoCommand", self.status())

    def test_broken_at_startup_shows_an_empty_list_and_a_message(self):
        self.path.write_text("{ nope", encoding="utf-8")
        app = self.make()
        self.assertEqual(app.apps, [])
        self.assertIn("not valid JSON", self.status())

    def test_missing_file_at_runtime_keeps_the_list(self):
        app = self.make()
        self.path.unlink()
        app.reload()
        self.assertEqual(len(app.apps), 3)
        self.assertIn("not found", self.status())

    def test_regaining_focus_reloads(self):
        app = self.make()
        self.path.write_text(json.dumps({"apps": [entry("Fresh")]}), encoding="utf-8")
        app._on_focus_in(SimpleNamespace(widget=self.root))
        self.assertEqual([a.name for a in app.apps], ["Fresh"])

    def test_focus_events_from_child_widgets_are_ignored(self):
        app = self.make()
        self.path.write_text(json.dumps({"apps": [entry("Fresh")]}), encoding="utf-8")
        app._on_focus_in(SimpleNamespace(widget=app.list))
        self.assertEqual(len(app.apps), 3)


class ThemeTests(UiCase):
    def test_follows_the_windows_setting_and_switches_live(self):
        app = self.make(dark=None)
        first = app.pal.dark
        app._forced_dark = not first
        app._check_theme()
        self.assertEqual(app.pal.dark, not first)
        self.assertEqual(len(app.rows), 3)
        self.assertEqual(self.root.cget("bg"), app.pal.window)

    def test_light_palette_builds(self):
        app = self.make(dark=False)
        self.assertFalse(app.pal.dark)
        self.assertEqual(len(app.rows), 3)

    def test_dark_palette_builds(self):
        app = self.make(dark=True)
        self.assertTrue(app.pal.dark)
        self.assertEqual(len(app.rows), 3)


class CallbackErrorTests(UiCase):
    def test_exceptions_inside_tk_callbacks_are_logged_and_shown(self):
        app = self.make()
        try:
            raise ValueError("kaboom")
        except ValueError as exc:
            app._on_callback_error(type(exc), exc, exc.__traceback__)
        self.assertEqual(app.callback_errors, 1)
        self.assertIn("kaboom", self.status())


class CommandLineTests(support.TmpDirTestCase):
    """launcher.pyw itself, run as a program."""

    def run_launcher(self, *args, python=None, timeout=60):
        return subprocess.run([python or sys.executable, str(support.ROOT / "launcher.pyw"), *args],
                              capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)

    def test_smoke_test_builds_the_window_and_exits_zero(self):
        done = self.run_launcher("--smoke-test", "1", "--invisible")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("smoke test passed", done.stdout)
        self.assertRegex(done.stdout, r"rows=\d+")
        log = (support.TMP_ROOT / "test-launcher.log").read_text(encoding="utf-8")
        self.assertIn("smoke test passed", log, "test runs log to .tmp, not to the real launcher.log")

    def test_smoke_test_with_a_dummy_list_and_a_key_press_launches_the_dummy(self):
        marker = self.tmp / "m.json"
        apps = self.write_json("dummy-apps.json", {"apps": [{
            "name": "Dummy", "command": str(support.pythonw()), "args": [str(support.DUMMY), str(marker)],
            "working_dir": str(self.tmp)}]})
        done = self.run_launcher("--smoke-test", "1.5", "--invisible", "--apps", str(apps), "--press", "1")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIsNotNone(support.wait_for(marker, 5), "pressing 1 did not start the dummy app")

    def test_starting_a_second_launcher_just_focuses_the_first(self):
        # A private lock and window title (--instance-id) so this never meets the real launcher.
        tag = f"test-{self.tmp.name}"
        apps = self.write_json("a.json", {"apps": [{"name": "X", "command": r"C:\Windows\System32\notepad.exe"}]})
        common = ["--invisible", "--single-instance", "--instance-id", tag, "--apps", str(apps)]
        first = subprocess.Popen([sys.executable, str(support.ROOT / "launcher.pyw"), "--smoke-test", "10", *common],
                                 stdout=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW)
        self.addCleanup(lambda: (first.kill(), first.wait(10), first.stdout.close()))
        for _ in range(150):                      # wait until its window exists
            if self._window_exists(f"App Launcher [{tag}]"):
                break
            time.sleep(0.1)
        else:
            self.fail("the first launcher never opened its window")
        started = time.time()
        second = self.run_launcher("--smoke-test", "5", *common)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("already open", second.stdout)
        self.assertLess(time.time() - started, 5, "the second launcher should have exited straight away")
        self.assertIsNone(first.poll(), "the first launcher must be left alone")

    @staticmethod
    def _window_exists(title):
        import ctypes
        return bool(ctypes.windll.user32.FindWindowW(None, title))

    def test_smoke_test_with_a_broken_list_still_opens_and_exits_zero(self):
        apps = self.write_text("broken.json", "{ this is broken")
        done = self.run_launcher("--smoke-test", "1", "--invisible", "--apps", str(apps))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("not valid JSON", done.stdout)


if __name__ == "__main__":
    unittest.main()
