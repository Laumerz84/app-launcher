"""Launch all: skips running apps, leaves out opted-out entries, survives failures."""
import json
import subprocess
import unittest

import support
from applauncher import procs
from applauncher.commands import CREATE_NO_WINDOW, LaunchAllResult, LaunchError, launch_all, launch_app
from applauncher.config import App


def apps(*names, **special):
    return [App(name=n, command=f"{n}.exe", **special.get(n, {})) for n in names]


class Recorder:
    def __init__(self, fail=None):
        self.started = []
        self.fail = fail or {}

    def __call__(self, app):
        if app.name in self.fail:
            raise self.fail[app.name]
        self.started.append(app.name)


class LaunchAllTests(unittest.TestCase):
    def test_starts_everything_when_nothing_is_running(self):
        rec = Recorder()
        result = launch_all(apps("a", "b", "c"), [False, False, False], rec)
        self.assertEqual(rec.started, ["a", "b", "c"])
        self.assertEqual(result.launched, ["a", "b", "c"])
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.summary(), "Launched 3.")

    def test_skips_apps_that_are_already_running(self):
        rec = Recorder()
        result = launch_all(apps("a", "b", "c", "d"), [True, False, True, False], rec)
        self.assertEqual(rec.started, ["b", "d"])
        self.assertEqual(result.skipped, ["a", "c"])
        self.assertEqual(result.summary(), "Launched 2, 2 already running.")

    def test_unknown_state_counts_as_not_running(self):
        rec = Recorder()
        launch_all(apps("a", "b"), [None, True], rec)
        self.assertEqual(rec.started, ["a"])

    def test_missing_or_short_state_list_launches_the_rest(self):
        rec = Recorder()
        launch_all(apps("a", "b", "c"), [True], rec)
        self.assertEqual(rec.started, ["b", "c"])

    def test_everything_already_running_launches_nothing(self):
        rec = Recorder()
        result = launch_all(apps("a", "b"), [True, True], rec)
        self.assertEqual(rec.started, [])
        self.assertEqual(result.summary(), "Nothing to launch - 2 already running.")

    def test_entries_opted_out_of_launch_all_are_left_alone(self):
        rec = Recorder()
        entries = apps("a", "page", "c", page={"launch_all": False})
        result = launch_all(entries, [False, False, False], rec)
        self.assertEqual(rec.started, ["a", "c"])
        self.assertEqual(result.excluded, ["page"])

    def test_one_failure_does_not_stop_the_others(self):
        rec = Recorder(fail={"b": LaunchError("file not found"), "c": RuntimeError("boom")})
        result = launch_all(apps("a", "b", "c", "d"), [False] * 4, rec)
        self.assertEqual(rec.started, ["a", "d"])
        self.assertEqual(result.launched, ["a", "d"])
        self.assertEqual(result.failed, [("b", "file not found"), ("c", "RuntimeError: boom")])
        self.assertEqual(result.summary(), "Launched 2, failed: b, c.")

    def test_a_running_app_is_never_launched_even_if_it_would_fail(self):
        rec = Recorder(fail={"a": LaunchError("would fail")})
        result = launch_all(apps("a"), [True], rec)
        self.assertEqual(result.failed, [])

    def test_empty_list(self):
        self.assertEqual(launch_all([], [], Recorder()).summary(), "Nothing to launch.")

    def test_result_type(self):
        self.assertIsInstance(launch_all([], [], Recorder()), LaunchAllResult)


class RealLaunchAllTests(support.TmpDirTestCase):
    """Launch all with real (dummy) processes: the running one is skipped, the others start."""

    def dummy(self, name, marker, *extra, **kw):
        return App(name=name, command=support.pythonw(), args=(str(support.DUMMY), str(marker), *extra),
                   working_dir=str(self.tmp), **kw)

    def test_running_dummy_is_skipped_and_the_others_are_started(self):
        marker_a, marker_b, marker_c = (self.tmp / f"{n}.json" for n in "abc")
        token = f"launch-all-test-{self.tmp.name}"
        a = self.dummy("A", marker_a, f"--tag={token}", "--sleep", "60", match=(token,))
        # B and C run the same script as A; only their (distinct) marker paths tell the processes apart,
        # which is exactly what an explicit "match" is for.
        b = self.dummy("B", marker_b, match=(str(marker_b),))
        c = self.dummy("C", marker_c, launch_all=False, match=(str(marker_c),))

        pid_a = launch_app(a)
        self.addCleanup(lambda: subprocess.run(["taskkill", "/PID", str(pid_a), "/T", "/F"], capture_output=True,
                                               creationflags=CREATE_NO_WINDOW))
        first = support.wait_for(marker_a)
        self.assertIsNotNone(first)

        apps = [a, b, c]
        states = procs.running_states(apps, procs.list_processes())
        self.assertEqual(states, [True, False, False])
        result = launch_all(apps, states, launch_app)

        self.assertEqual(result.skipped, ["A"])
        self.assertEqual(result.launched, ["B"])
        self.assertEqual(result.excluded, ["C"])
        self.assertIsNotNone(support.wait_for(marker_b), "B was not started")
        self.assertFalse(marker_c.exists(), "C opted out of Launch all")
        self.assertEqual(json.loads(marker_a.read_text())["pid"], first["pid"], "A must not have been started again")


if __name__ == "__main__":
    unittest.main()
