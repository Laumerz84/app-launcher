"""The pieces the Stop feature stands on: the new apps.json fields, the claims each entry lays on
running processes, the process snapshot's parent/start-time data, and start_process."""
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

import support
from applauncher import commands, config, procs
from applauncher.commands import CREATE_NO_WINDOW, LaunchError, start_process
from applauncher.config import App
from applauncher.procs import Proc


class StopFieldTests(support.TmpDirTestCase):
    def load(self, entry):
        return config.load_apps(self.write_json("apps.json", {"apps": [{"name": "A", "command": "a.exe", **entry}]}))

    def test_defaults_are_empty(self):
        (app,) = self.load({}).apps
        self.assertEqual(app.stop_command, "")
        self.assertEqual(app.stop_args, ())

    def test_stop_command_and_a_list_of_args(self):
        result = self.load({"stop_command": r"C:\x\quit.exe", "stop_args": ["--now", "a b"]})
        self.assertEqual((result.errors, result.warnings), ([], []))
        (app,) = result.apps
        self.assertEqual(app.stop_command, r"C:\x\quit.exe")
        self.assertEqual(app.stop_args, ("--now", "a b"))

    def test_stop_args_work_like_args_a_single_string_is_passed_as_is(self):
        (app,) = self.load({"stop_command": "q.exe", "stop_args": '--x "y z"'}).apps
        self.assertEqual(app.stop_args, '--x "y z"')

    def test_wrong_types_are_reported_and_the_entry_skipped(self):
        for entry, needle in (({"stop_command": 5}, "'stop_command' must be text"),
                              ({"stop_command": "q.exe", "stop_args": [1]}, "stop_args item 1 must be text"),
                              ({"stop_command": "q.exe", "stop_args": 7}, "'stop_args' must be a list")):
            with self.subTest(entry=entry):
                result = self.load(entry)
                self.assertEqual(result.apps, [])
                self.assertIn(needle, result.message)

    def test_stop_args_without_a_stop_command_is_a_warning(self):
        result = self.load({"stop_args": ["--quit"]})
        self.assertEqual(len(result.apps), 1)
        self.assertIn("stop_args is ignored without stop_command", result.warnings[0])

    def test_the_fields_are_known_keys_so_no_typo_warning(self):
        self.assertEqual(self.load({"stop_command": "q.exe"}).warnings, [])


class ShippedFileStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = json.loads((support.ROOT / "apps.json").read_text(encoding="utf-8"))
        cls.apps = {a.name: a for a in config.load_apps(support.ROOT / "apps.json").apps}

    def test_help_documents_both_new_fields(self):
        for field in ("stop_command", "stop_args"):
            self.assertIn(field, self.raw["_help"])
        self.assertIn("never force-kills", self.raw["_help"]["stop_command"])
        self.assertIn("10 seconds", self.raw["_help"]["stop_command"])

    def test_the_help_block_is_exactly_the_documented_text(self):
        self.assertEqual(self.raw["_help"], config.HELP_TEXT)

    def test_ui_report_tool_is_set_up_as_asked(self):
        app = self.apps["UI Report Tool"]
        self.assertEqual(app.stop_command, r"G:\UI Report Tool\.venv\Scripts\pythonw.exe")
        self.assertEqual(app.stop_args, (r"G:\UI Report Tool\launch.pyw", "--quit"))

    def test_the_other_entries_have_no_stop_command(self):
        for name, app in self.apps.items():
            if name != "UI Report Tool":
                self.assertEqual(app.stop_command, "", name)

    def test_the_readme_documents_stop(self):
        readme = (support.ROOT / "README.md").read_text(encoding="utf-8")
        for needle in ("stop_command", "stop_args", "Click again to stop", "Shift+", "polite", "never force"):
            self.assertIn(needle, readme)


def P(pid, name, cmd="", ppid=1, started=1):
    return Proc(pid, name, name, cmd, ppid, started)


class ClaimsTests(unittest.TestCase):
    def test_owned_shared_rivals_and_detectable(self):
        apps = [App(name="A", command="x.exe", match=("one",)),
                App(name="B", command="x.exe", match=("one", "two")),       # more specific than A
                App(name="C", command="x.exe", match=("two", "one")),       # exactly as specific as B
                App(name="Page", command=r"C:\pages\i.html")]
        world = [P(10, "p.exe", "one"), P(11, "p.exe", "one two"), P(12, "p.exe", "nothing")]
        claims = procs.compute_claims(apps, world)
        self.assertEqual(claims.owned, [frozenset({10}), frozenset(), frozenset(), frozenset()])
        self.assertEqual(claims.shared, [frozenset(), frozenset({11}), frozenset({11}), frozenset()])
        self.assertEqual(claims.rivals, [frozenset(), frozenset({2}), frozenset({1}), frozenset()])
        self.assertEqual(claims.detectable, [True, True, True, False])
        self.assertEqual([claims.running(i) for i in range(4)], [True, True, True, None])

    def test_the_ignored_pid_claims_nothing(self):
        apps = [App(name="A", command="x.exe", match=("one",))]
        claims = procs.compute_claims(apps, [P(10, "p.exe", "one")], ignore_pid=10)
        self.assertEqual(claims.owned, [frozenset()])
        self.assertIs(claims.running(0), False)

    def test_specificity_orders_by_number_of_texts_then_length(self):
        few = procs.Matcher(tokens=("aaaa",), explicit=True)
        many = procs.Matcher(tokens=("a", "b"), explicit=True)
        long_text = procs.Matcher(tokens=("aaaaaa",), explicit=True)
        self.assertGreater(procs.specificity(many), procs.specificity(few))
        self.assertGreater(procs.specificity(long_text), procs.specificity(few))

    def test_running_states_still_behaves_as_before_for_independent_entries(self):
        apps = [App(name="A", command="x.exe", match=("aaa",)), App(name="B", command="x.exe", match=("bbb",))]
        self.assertEqual(procs.running_states(apps, [P(1, "p.exe", "bbb")]), [False, True])
        self.assertEqual(procs.running_states(apps, None), [None, None])


@unittest.skipUnless(os.name == "nt", "Windows only")
class SnapshotTests(unittest.TestCase):
    def test_the_snapshot_knows_parents_and_start_times(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (child.kill(), child.wait(10)))
        found = {}
        for _ in range(100):
            found = {p.pid: p for p in procs.list_processes() or []}
            if child.pid in found:
                break
            time.sleep(0.05)
        me, kid = found[os.getpid()], found[child.pid]
        self.assertEqual(kid.ppid, me.pid)
        self.assertTrue(me.started and kid.started)
        self.assertGreaterEqual(kid.started, me.started, "a child cannot be older than its parent")


class StartProcessTests(unittest.TestCase):
    def plan(self, **kw):
        base = dict(mode="exe", exe=r"C:\x\a.exe", cmdline=r"C:\x\a.exe /q", cwd=r"C:\x", creationflags=CREATE_NO_WINDOW)
        base.update(kw)
        return commands.LaunchPlan(**base)

    def test_hands_back_the_process_object_unlike_launch_which_lets_go_of_it(self):
        popen = mock.Mock(return_value=mock.Mock(pid=7, returncode=None))
        proc = start_process(self.plan(), popen=popen)
        self.assertEqual(proc.pid, 7)
        self.assertIsNone(proc.returncode)            # still ours to poll
        self.assertEqual(popen.call_args[0][0], r"C:\x\a.exe /q")
        self.assertEqual(commands.launch(self.plan(), popen=popen), 7)

    def test_a_failure_is_a_launch_error(self):
        popen = mock.Mock(side_effect=OSError(2, "gone", None, 2))
        with self.assertRaises(LaunchError) as ctx:
            start_process(self.plan(), popen=popen)
        self.assertEqual(str(ctx.exception), "file not found")


if __name__ == "__main__":
    unittest.main()
