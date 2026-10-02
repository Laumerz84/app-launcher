"""The Stop control in the window: the pill, two-click arming, Shift+digit, status lines.

Every test injects a pretend stop and a pretend process list; nothing real is ever ended here.
"""
import json
import threading
import time
from types import SimpleNamespace
from unittest import mock

import support
from applauncher import stop, ui
from applauncher.config import App
from applauncher.procs import Proc
from test_ui import UiCase, entry


def skey(keysym="", char="", state=0, keycode=0):
    return SimpleNamespace(keysym=keysym, char=char, state=state, keycode=keycode)


SHIFT_1 = dict(keysym="exclam", char="!", state=1, keycode=49)
SHIFT_2 = dict(keysym="at", char="@", state=1, keycode=50)


class StopUiCase(UiCase):
    def setUp(self):
        super().setUp()
        self.stops = []
        self.result = None       # set to make the pretend stop answer with something else

    def fake_stop(self, app, apps):
        self.stops.append(app.name)
        if self.result is None:                                   # the app really does go away
            self.running = [p for p in self.running if f"mark-{app.name}" not in p.cmdline]
            return stop.StopResult(True, f"Stopped {app.name}")
        return self.result

    def make(self, **kw):
        kw.setdefault("stop_fn", self.fake_stop)
        return super().make(**kw)

    def set_running(self, *names):
        """Make the pretend process scan report these entries as running."""
        self.running = [Proc(100 + i, "x.exe", "x.exe", f"x.exe mark-{n}") for i, n in enumerate(names)]

    def pill(self, index):
        app = self.app
        return app.list.itemcget(app.rows[index].stop_img, "state"), app.list.itemcget(app.rows[index].stop_txt, "text")

    def click_pill(self, index):
        x0, y0, x1, y1 = self.app._stop_box(index)
        self.click(self.app.list, (x0 + x1) // 2, (y0 + y1) // 2)

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)


class PillVisibilityTests(StopUiCase):
    def test_only_running_rows_get_a_stop_pill(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        self.assertEqual([self.pill(i)[0] for i in range(3)], ["hidden", "normal", "hidden"])
        self.assertEqual(self.pill(1)[1], "Stop")
        self.assertEqual([app.list.itemcget(r.dot, "state") for r in app.rows], ["hidden", "normal", "hidden"])

    def test_the_pill_goes_away_when_the_app_does(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        self.running = []
        app.refresh_running()
        self.assertEqual(self.pill(1)[0], "hidden")

    def test_entries_that_cannot_be_detected_never_get_one(self):
        page = self.write_text("p.html", "<html>")
        self.path = self.write_json("apps.json", {"apps": [entry("Alpha"), {"name": "Page", "command": str(page)}]})
        self.set_running("Alpha")
        app = self.make()
        app.refresh_running()
        self.assertEqual(self.pill(0)[0], "normal")
        self.assertEqual(self.pill(1)[0], "hidden")
        self.assertFalse(app.rows[1].detectable)
        self.assertFalse(app._can_stop(1))
        x, y = self.row_center(1)
        app._set_hover(app._hit(SimpleNamespace(widget=app.list, x=app.px(ui.W) - 40, y=y)))
        self.assertEqual(app.hover, ("row", 1), "no pill hit area on a page entry")

    def test_the_hint_line_mentions_shift_and_the_digits(self):
        app = self.make()
        self.assertIn("Shift+1–3 stop", self.status())

    def check_theme(self, dark):
        self.set_running("Alpha")
        app = self.make(dark=dark)
        app.refresh_running()
        self.assertEqual(self.pill(0), ("normal", "Stop"))
        app.stop_clicked(0)
        self.assertEqual(self.pill(0)[1], "Click again\nto stop")
        app.set_theme(not dark)                       # switching theme live keeps the state and redraws
        self.assertEqual(self.pill(0)[1], "Click again\nto stop")

    def test_light_theme_draws_the_pill(self):
        self.check_theme(False)

    def test_dark_theme_draws_the_pill(self):
        self.check_theme(True)


class TwoClickTests(StopUiCase):
    def test_first_click_arms_second_click_stops(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        self.click_pill(1)
        self.assertEqual(app.armed, 1)
        self.assertEqual(self.pill(1), ("normal", "Click again\nto stop"))
        self.assertEqual(app.list.itemcget(app.rows[1].dot, "state"), "hidden", "the armed pill takes the dot's place")
        self.assertEqual(self.stops, [], "one click must not stop anything")
        self.click_pill(1)
        self.assertEqual(self.stops, ["Beta"])
        self.assertIsNone(app.armed)

    def test_clicking_the_pill_never_launches_but_the_rest_of_the_row_still_does(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        self.click_pill(1)
        self.click_pill(1)
        self.assertEqual(self.launched.apps, [])
        x, y = self.row_center(1)
        self.click(app.list, x, y)
        self.assertEqual(self.launched.apps, ["Beta"])

    def test_the_armed_state_lasts_three_seconds_by_default_and_then_times_out(self):
        self.assertEqual(ui.ARM_MS, 3000)
        self.set_running("Beta")
        with mock.patch.object(ui, "ARM_MS", 250):
            app = self.make(timers=False)
            app.refresh_running()
            self.click_pill(1)
            self.assertEqual(app.armed, 1)
            self.pump(0.1)
            self.assertEqual(app.armed, 1, "still armed just before the timeout")
            self.pump(0.6)
            self.assertIsNone(app.armed)
            self.assertEqual(self.pill(1), ("normal", "Stop"))
            self.click_pill(1)                     # a click after the timeout arms again, it does not stop
            self.assertEqual(app.armed, 1)
            self.assertEqual(self.stops, [])

    def test_a_click_just_inside_the_window_does_stop(self):
        self.set_running("Beta")
        with mock.patch.object(ui, "ARM_MS", 800):
            app = self.make()
            app.refresh_running()
            self.click_pill(1)
            self.pump(0.2)
            self.click_pill(1)
        self.assertEqual(self.stops, ["Beta"])

    def test_arming_another_row_moves_the_arming_instead_of_stopping(self):
        self.set_running("Alpha", "Beta")
        app = self.make()
        app.refresh_running()
        self.click_pill(0)
        self.click_pill(1)
        self.assertEqual(app.armed, 1)
        self.assertEqual(self.pill(0)[1], "Stop")
        self.assertEqual(self.pill(1)[1], "Click again\nto stop")
        self.assertEqual(self.stops, [])

    def test_launching_anything_cancels_the_arming(self):
        self.set_running("Alpha", "Beta")
        app = self.make()
        app.refresh_running()
        self.click_pill(0)
        x, y = self.row_center(1)
        self.click(app.list, x, y)                      # launches Beta
        self.assertIsNone(app.armed)
        self.click_pill(0)
        app.launch_everything()
        self.assertIsNone(app.armed)
        self.assertEqual(self.stops, [])

    def test_the_armed_pill_grows_but_stays_under_the_pointer(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        idle = app._stop_box(1)
        app.stop_clicked(1)
        armed = app._stop_box(1)
        self.assertGreater(armed[2] - armed[0], idle[2] - idle[0])
        self.assertGreater(armed[3] - armed[1], idle[3] - idle[1])
        centre = ((idle[0] + idle[2]) // 2, (idle[1] + idle[3]) // 2)
        self.assertTrue(armed[0] <= centre[0] < armed[2] and armed[1] <= centre[1] < armed[3])
        self.assertEqual(armed[2], idle[2], "it grows leftwards from the same right edge")

    def test_an_armed_app_that_quits_by_itself_drops_the_arming(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        app.stop_clicked(1)
        self.running = []
        app.refresh_running()
        self.assertIsNone(app.armed)
        self.assertEqual(self.pill(1)[0], "hidden")

    def test_reloading_the_list_drops_the_arming(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        app.stop_clicked(1)
        self.path.write_text(json.dumps({"apps": [entry("Alpha"), entry("Beta"), entry("Delta")]}), encoding="utf-8")
        self.assertTrue(app.reload())
        self.assertIsNone(app.armed)
        self.assertEqual(self.stops, [])


class ShiftDigitTests(StopUiCase):
    def test_first_press_arms_second_press_stops(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        app._on_key(skey(**SHIFT_2))
        self.assertEqual(app.armed, 1)
        self.assertEqual(self.pill(1)[1], "Click again\nto stop")
        self.assertIn("Press Shift+2 again to stop Beta", self.status())
        self.assertEqual(self.stops, [])
        app._on_key(skey(**SHIFT_2))
        self.assertEqual(self.stops, ["Beta"])

    def test_the_keyboard_arming_times_out_too(self):
        self.set_running("Beta")
        with mock.patch.object(ui, "ARM_MS", 200):
            app = self.make()
            app.refresh_running()
            app._on_key(skey(**SHIFT_2))
            self.pump(0.5)
            self.assertIsNone(app.armed)
            app._on_key(skey(**SHIFT_2))
            self.assertEqual(self.stops, [])

    def test_shifted_digit_of_a_different_row_moves_the_arming(self):
        self.set_running("Alpha", "Beta")
        app = self.make()
        app.refresh_running()
        app._on_key(skey(**SHIFT_1))
        app._on_key(skey(**SHIFT_2))
        self.assertEqual(app.armed, 1)
        self.assertEqual(self.stops, [])

    def test_a_row_that_is_not_running_says_so(self):
        app = self.make()
        app._on_key(skey(**SHIFT_2))
        self.assertIn("Beta is not running", self.status())
        self.assertIsNone(app.armed)

    def test_a_page_entry_explains_why_there_is_no_stop(self):
        page = self.write_text("p.html", "<html>")
        self.path = self.write_json("apps.json", {"apps": [entry("Alpha"), {"name": "Page", "command": str(page)}]})
        app = self.make()
        app._on_key(skey(**SHIFT_2))
        self.assertIn("cannot be detected as running", self.status())

    def test_a_digit_beyond_the_list_does_nothing(self):
        app = self.make()
        app._on_key(skey("asterisk", "*", 1, 56))        # Shift+8
        self.assertEqual((app.armed, self.stops, self.launched.apps), (None, [], []))

    def test_a_digit_that_needs_shift_on_another_keyboard_layout_still_launches(self):
        """On a layout where typing "2" takes Shift, the key event carries the character "2"."""
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        app._on_key(skey("2", "2", 1, 50))
        self.assertEqual(self.launched.apps, ["Beta"])
        self.assertIsNone(app.armed)

    def test_plain_digits_and_ctrl_shift_combinations(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        app._on_key(skey("2", "2", 0, 50))
        self.assertEqual(self.launched.apps, ["Beta"])
        app._on_key(skey(keysym="at", char="@", state=1 | 0x4, keycode=50))     # Ctrl+Shift+2: not ours
        self.assertIsNone(app.armed)

    def test_other_layouts_shifted_digit_characters_work_through_the_key_code(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        app._on_key(skey("sterling", "£", 1, 51))       # UK: Shift+3 types a pound sign
        self.assertIn("Gamma is not running", self.status())
        self.assertIsNone(app.armed)
        app._on_key(skey("quotedbl", '"', 1, 50))            # UK: Shift+2 types a double quote
        self.assertEqual(app.armed, 1)


class StopOutcomeTests(StopUiCase):
    def arm_and_stop(self, app, index=1):
        app.stop_clicked(index)
        app.stop_clicked(index)

    def test_success_says_stopped_and_the_dot_goes_out_at_once(self):
        self.set_running("Beta")
        app = self.make()
        app.refresh_running()
        self.assertIs(app.states[1], True)
        self.arm_and_stop(app)
        self.assertEqual(self.stops, ["Beta"])
        self.assertEqual(self.status(), "Stopped Beta")
        self.assertIs(app.states[1], False)
        self.assertEqual(self.pill(1)[0], "hidden")
        self.assertEqual(app.list.itemcget(app.rows[1].dot, "state"), "hidden")
        self.assertEqual(app.list.itemcget(app.rows[1].sub, "text"), "Not running")

    def test_a_failure_is_shown_with_its_reason_in_the_error_colour_and_the_card_stays_running(self):
        self.set_running("Beta")
        self.result = stop.StopResult(False, "Could not stop Beta: 2 processes would not end (access denied)", "error")
        app = self.make()
        app.refresh_running()
        self.arm_and_stop(app)
        self.assertEqual(self.status(), "Could not stop Beta: 2 processes would not end (access denied)")
        self.assertEqual(app.bar.itemcget(app.status_id, "fill"), app.pal.error)
        self.assertIs(app.states[1], True)
        self.assertEqual(self.pill(1), ("normal", "Stop"), "it can be tried again")

    def test_still_open_is_plain_text_not_an_error_and_the_card_stays_running(self):
        self.set_running("Beta")
        self.result = stop.StopResult(False, "Beta is still open (it may be asking you something)", "warn")
        app = self.make()
        app.refresh_running()
        self.arm_and_stop(app)
        self.assertEqual(self.status(), "Beta is still open (it may be asking you something)")
        self.assertEqual(app.bar.itemcget(app.status_id, "fill"), app.pal.text)
        self.assertIs(app.states[1], True)

    def test_an_exception_inside_the_stop_is_contained(self):
        self.set_running("Beta")

        def explode(app, apps):
            raise RuntimeError("boom")

        app = self.make(stop_fn=explode)
        app.refresh_running()
        self.arm_and_stop(app)
        self.assertIn("Could not stop Beta: RuntimeError: boom", self.status())
        self.assertEqual(app.callback_errors, 0)
        self.assertEqual(self.pill(1), ("normal", "Stop"))

    def test_the_stop_is_handed_the_entry_and_the_current_list(self):
        self.set_running("Beta")
        seen = []
        app = self.make(stop_fn=lambda a, apps: seen.append((a.name, [x.name for x in apps])) or stop.StopResult(True, "ok"))
        app.refresh_running()
        self.arm_and_stop(app)
        self.assertEqual(seen, [("Beta", ["Alpha", "Beta", "Gamma"])])

    def test_the_default_stop_uses_the_windows_own_scan_and_with_nothing_running_touches_nothing(self):
        """No stop_fn injected: the real stop code runs, but against an empty pretend process list."""
        app = self.make(stop_fn=None, scan=lambda: [])
        result = app._run_stop(app.apps[0], list(app.apps))
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Alpha is not running")


class ThreadedStopTests(StopUiCase):
    def test_the_window_shows_stopping_while_it_works_ignores_clicks_then_reports(self):
        self.set_running("Beta")
        release, entered, calls = threading.Event(), threading.Event(), []

        def slow(app, apps):
            calls.append(app.name)
            entered.set()
            release.wait(10)
            self.running = []
            return stop.StopResult(True, f"Stopped {app.name}")

        app = self.make(stop_fn=slow, threaded=True)
        for _ in range(100):                                 # let the first background scan land
            self.root.update()
            if app.states[1] is True:
                break
            time.sleep(0.02)
        self.assertIs(app.states[1], True)
        self.click_pill(1)
        self.click_pill(1)
        self.assertTrue(entered.wait(5))
        self.assertEqual(self.pill(1), ("normal", "Stopping…"))
        self.assertEqual(self.status(), "Stopping Beta…")
        self.click_pill(1)                                   # clicks while stopping do nothing
        app.stop_clicked(1)
        self.assertEqual(calls, ["Beta"])
        release.set()
        for _ in range(200):
            self.root.update()
            if self.status() == "Stopped Beta":
                break
            time.sleep(0.02)
        self.assertEqual(self.status(), "Stopped Beta")
        self.assertIs(app.states[1], False)
        self.assertEqual(self.pill(1)[0], "hidden")
        self.assertEqual(calls, ["Beta"])

    def test_two_different_stops_can_run_at_once(self):
        self.set_running("Alpha", "Beta")
        gate = threading.Barrier(2, timeout=10)

        def meet(app, apps):
            gate.wait()                                       # both stops are inside this function together
            return stop.StopResult(True, f"Stopped {app.name}")

        app = self.make(stop_fn=meet, threaded=True)
        for _ in range(100):
            self.root.update()
            if app.states[:2] == [True, True]:
                break
            time.sleep(0.02)
        app.stop_clicked(0); app.stop_clicked(0)
        app.stop_clicked(1); app.stop_clicked(1)
        for _ in range(300):
            self.root.update()
            if not app._stopping:
                break
            time.sleep(0.02)
        self.assertEqual(app._stopping, [])


class RealStopThroughTheWindowTests(StopUiCase):
    """The window, the real process scan and the real stop code together - on dummy entries only."""

    def setUp(self):
        super().setUp()
        self.pids = []
        self.addCleanup(lambda: [support.kill_tree(pid) for pid in self.pids])

    def dummy_list(self, **entry_kw):
        tag = f"window-stop-{self.tmp.name}"
        self.marker = self.tmp / "d.json"
        self.quit_file = self.tmp / "quit.flag"
        item = {"name": "Dummy", "command": str(support.pythonw()),
                "args": [str(support.DUMMY), str(self.marker), f"--tag={tag}", "--sleep", "90",
                         "--quit-file", str(self.quit_file)],
                "working_dir": str(self.tmp), "match": [tag], **entry_kw}
        self.path = self.write_json("apps.json", {"apps": [item]})

    def open_window(self):
        from applauncher import commands, procs
        app = self.make(scan=procs.list_processes, launch_fn=commands.launch_app, stop_fn=None, threaded=True)
        app.handle_char("1")                                      # start the dummy through the window
        info = support.wait_for(self.marker, 30)
        self.assertIsNotNone(info, "the dummy never started")
        self.pids.append(info["pid"])
        for _ in range(300):                                      # until the window's own scan sees it
            app.refresh_running()
            self.root.update()
            if app.states and app.states[0] is True:
                break
            time.sleep(0.05)
        self.assertIs(app.states[0], True)
        return app, info

    def stop_through_the_window(self, app):
        self.click_pill(0)
        self.assertEqual(self.pill(0)[1], "Click again\nto stop")
        self.click_pill(0)
        for _ in range(600):
            self.root.update()
            if self.status().startswith(("Stopped", "Could not", "Dummy is still")):
                break
            time.sleep(0.05)

    def test_two_clicks_end_a_dummy_that_has_no_stop_command(self):
        self.dummy_list()
        app, info = self.open_window()
        self.stop_through_the_window(app)
        self.assertEqual(self.status(), "Stopped Dummy")
        self.assertTrue(support.wait_dead(info["pid"], 8))
        self.assertIs(app.states[0], False)
        self.assertEqual(self.pill(0)[0], "hidden")

    def test_two_clicks_run_the_stop_command_when_there_is_one(self):
        self.dummy_list(stop_command=str(support.pythonw()), stop_args=[str(support.DUMMY_STOP), "PLACEHOLDER"])
        item = json.loads(self.path.read_text(encoding="utf-8"))
        item["apps"][0]["stop_args"] = [str(support.DUMMY_STOP), str(self.quit_file)]
        self.path.write_text(json.dumps(item), encoding="utf-8")
        app, info = self.open_window()
        self.stop_through_the_window(app)
        self.assertEqual(self.status(), "Stopped Dummy")
        self.assertTrue(self.quit_file.exists(), "the stop command ran")
        self.assertTrue(support.wait_dead(info["pid"], 8))

    def test_shift_digit_twice_does_the_same(self):
        self.dummy_list()
        app, info = self.open_window()
        app._on_key(skey(**SHIFT_1))
        self.assertEqual(self.pill(0)[1], "Click again\nto stop")
        app._on_key(skey(**SHIFT_1))
        for _ in range(600):
            self.root.update()
            if self.status() == "Stopped Dummy":
                break
            time.sleep(0.05)
        self.assertEqual(self.status(), "Stopped Dummy")
        self.assertTrue(support.wait_dead(info["pid"], 8))


class ClosingWhileStoppingTests(StopUiCase):
    def test_closing_the_window_lets_a_stop_in_progress_finish_first(self):
        self.set_running("Beta")
        entered, finished = threading.Event(), threading.Event()

        def slow(app, apps):
            entered.set()
            time.sleep(0.6)                      # e.g. a parent ended, its children still to go
            finished.set()
            return stop.StopResult(True, "Stopped Beta")

        app = self.make(stop_fn=slow, threaded=True)
        for _ in range(100):
            self.root.update()
            if app.states[1] is True:
                break
            time.sleep(0.02)
        app.stop_clicked(1)
        app.stop_clicked(1)
        self.assertTrue(entered.wait(5))
        started = time.time()
        app.close()
        self.assertTrue(finished.is_set(), "close() must not abandon a half-done stop")
        self.assertLess(time.time() - started, 5)

    def test_but_it_never_hangs_forever(self):
        self.set_running("Beta")
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def stuck(app, apps):
            entered.set()
            release.wait(30)
            return stop.StopResult(True, "x")

        with mock.patch.object(ui, "CLOSE_GRACE_S", 0.4):
            app = self.make(stop_fn=stuck, threaded=True)
            for _ in range(100):
                self.root.update()
                if app.states[1] is True:
                    break
                time.sleep(0.02)
            app.stop_clicked(1)
            app.stop_clicked(1)
            self.assertTrue(entered.wait(5))
            started = time.time()
            app.close()
            self.assertLess(time.time() - started, 3)
