"""Stopping apps: what may be touched, the stop_command path, polite close then terminate.

The first half uses pretend process lists and pretend Windows actions. The second half stops real
*dummy* processes (tests/dummy_app.py). The user's real apps are never started or stopped here.
"""
import os
import subprocess
import sys
import time
import unittest

import support
from applauncher import commands, config, procs, stop
from applauncher.commands import CREATE_NO_WINDOW, LaunchError, launch_app
from applauncher.config import App
from applauncher.procs import Proc

UB = r"F:\ClodCode\utilitybelt"


def P(pid, name, cmdline="", ppid=1, started=None):
    """A pretend process. By default processes get later start times as pid grows, like real life."""
    return Proc(pid=pid, name=name, exe=name, cmdline=cmdline, ppid=ppid,
                started=(1000 + pid) if started is None else started)


# --------------------------------------------------------------------------- the real entries, pretend processes
def belt_world():
    """UtilityBelt, UtilityBelt Mini, the Screener shell and UI Report Tool all running at once."""
    return [
        # explorer -> launcher (the test process) -> ...
        P(1, "explorer.exe", r"C:\Windows\explorer.exe", ppid=0),
        P(50, "pythonw.exe", r'"C:\Python\pythonw.exe" "F:\ClodCode\launcher\launcher.pyw"', ppid=1),
        # One Windows Terminal process hosts both UtilityBelt windows, each with its own OpenConsole.
        P(200, "WindowsTerminal.exe", r"C:\Program Files\WindowsApps\WindowsTerminal.exe", ppid=1, started=1),
        # UtilityBelt (wt --focus): OpenConsole -> cmd /c belt.cmd -> python ...\belt.py -> powershell helper
        P(204, "OpenConsole.exe", r"OpenConsole.exe --headless", ppid=200, started=90),
        P(100, "cmd.exe", r'cmd /c F:\ClodCode\utilitybelt\belt.cmd', ppid=204, started=100),
        P(101, "python.exe", r'python  "F:\ClodCode\utilitybelt\belt.py"', ppid=100),
        P(102, "powershell.exe", r"powershell -NoProfile -NonInteractive -Command gpu-stream", ppid=101),
        # UtilityBelt Mini: OpenConsole -> python belt.py --mini -> powershell helper
        P(201, "OpenConsole.exe", r"OpenConsole.exe --headless", ppid=200),
        P(202, "python.exe", r"python belt.py --mini", ppid=201),
        P(203, "powershell.exe", r"powershell -NoProfile -NonInteractive -Command gpu-stream", ppid=202),
        # Screener shell
        P(300, "powershell.exe",
          r'''"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoExit -ExecutionPolicy Bypass '''
          r'''-Command ". 'C:\Users\andre\Python\screener-jobs\shell\screener-shell.ps1'"''', ppid=1),
        P(301, "python.exe", r"C:\Users\andre\Python\Screener-v2\.venv\Scripts\python.exe viewer.py", ppid=300),
        # UI Report Tool: venv trampoline + real interpreter
        P(400, "pythonw.exe", r'"G:\UI Report Tool\.venv\Scripts\pythonw.exe" "G:\UI Report Tool\launch.pyw"', ppid=1),
        P(401, "pythonw.exe", r'"C:\Python\pythonw.exe" "G:\UI Report Tool\launch.pyw"', ppid=400),
        # bystanders
        P(900, "python.exe", r"python -m http.server", ppid=1),
        P(901, "cmd.exe", r"C:\WINDOWS\system32\cmd.exe /d /s /c desktop-commander", ppid=1),
    ]


class RealEntriesNeverTouchEachOther(unittest.TestCase):
    """With the shipped apps.json: stopping one entry can only ever reach its own processes."""

    @classmethod
    def setUpClass(cls):
        cls.apps = config.load_apps(support.ROOT / "apps.json").apps
        cls.index = {a.name: i for i, a in enumerate(cls.apps)}
        cls.world = belt_world()

    def reach(self, name):
        targets = stop.find_targets(self.index[name], self.apps, self.world, own_pid=50)
        return {p.pid for p in targets.everything}

    def test_utilitybelt_reaches_its_own_tree_and_nothing_else(self):
        # its python and helper; the cmd running belt.cmd then finishes by itself and the window closes
        self.assertEqual(self.reach("UtilityBelt"), {101, 102})

    def test_utilitybelt_mini_reaches_only_its_own_python_and_helper(self):
        self.assertEqual(self.reach("UtilityBelt Mini"), {202, 203})     # not the terminal hosting it

    def test_screener_shell(self):
        self.assertEqual(self.reach("Screener shell"), {300, 301})

    def test_ui_report_tool(self):
        self.assertEqual(self.reach("UI Report Tool"), {400, 401})

    def test_the_launcher_and_bystanders_are_never_reached_by_anyone(self):
        everything = set()
        for name in self.index:
            everything |= self.reach(name)
        self.assertFalse(everything & {1, 50, 100, 200, 201, 204, 900, 901})

    def test_no_two_entries_share_a_target(self):
        seen = {}
        for name in self.index:
            for pid in self.reach(name):
                self.assertNotIn(pid, seen, f"{name} and {seen.get(pid)} would both end process {pid}")
                seen[pid] = name

    def test_entries_that_are_not_running_reach_nothing(self):
        for name in ("UtilityBelt Dash", "Fractal Flame", "Vortex Street", "Claude Museum"):
            self.assertEqual(self.reach(name), set(), name)

    def test_stopping_with_the_other_running_uses_only_the_right_victims(self):
        """End to end with pretend actions: stop UtilityBelt while Mini, Screener etc. are all running."""
        result, world = run_fake_stop(self.apps, "UtilityBelt", belt_world(), closes=False)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(set(world.terminated), {101, 102})
        self.assertEqual({p.pid for p in world.live.values()} & {101, 102}, set())
        for survivor in (1, 50, 200, 201, 202, 203, 204, 300, 301, 400, 401, 900, 901):
            self.assertIn(survivor, world.live)

    def test_and_the_other_way_round(self):
        result, world = run_fake_stop(self.apps, "UtilityBelt Mini", belt_world(), closes=False)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(set(world.terminated), {202, 203})
        for survivor in (100, 101, 102, 200, 201, 204, 300, 301, 400, 401):
            self.assertIn(survivor, world.live)


# --------------------------------------------------------------------------- choosing targets
class TargetSelectionTests(unittest.TestCase):
    def test_matched_processes_plus_all_descendants(self):
        apps = [App(name="X", command="x.exe", match=("tagx",))]
        world = [P(1, "explorer.exe", ppid=0), P(10, "python.exe", "python d.py tagx"), P(11, "python.exe", "child", ppid=10),
                 P(12, "conhost.exe", "grandchild", ppid=11), P(20, "python.exe", "unrelated"),
                 P(21, "python.exe", "its child", ppid=20)]
        targets = stop.find_targets(0, apps, world, own_pid=999)
        self.assertEqual([p.pid for p in targets.owned], [10])
        self.assertEqual({p.pid for p in targets.descendants}, {11, 12})
        self.assertEqual(targets.depth, {10: 0, 11: 1, 12: 2})
        self.assertEqual([p.pid for p in targets.leaf_first()], [12, 11, 10])

    def test_a_child_older_than_its_parent_is_a_reused_pid_not_a_child(self):
        apps = [App(name="X", command="x.exe", match=("tagx",))]
        world = [P(10, "python.exe", "python d.py tagx", started=500),
                 P(11, "python.exe", "stale ppid", ppid=10, started=100),     # older than "its parent"
                 P(12, "python.exe", "real child", ppid=10, started=600)]
        targets = stop.find_targets(0, apps, world, own_pid=999)
        self.assertEqual({p.pid for p in targets.descendants}, {12})

    def test_processes_other_entries_claim_are_never_targets_nor_are_their_subtrees(self):
        apps = [App(name="X", command="x.exe", match=("tagx",)), App(name="Y", command="y.exe", match=("tagy",))]
        world = [P(10, "python.exe", "python d.py tagx"),
                 P(11, "python.exe", "helper of X", ppid=10),
                 P(20, "python.exe", "python d.py tagy", ppid=10),            # Y was started by X
                 P(21, "python.exe", "helper of Y", ppid=20)]
        self.assertEqual({p.pid for p in stop.find_targets(0, apps, world, 999).everything}, {10, 11})
        self.assertEqual({p.pid for p in stop.find_targets(1, apps, world, 999).everything}, {20, 21})

    def test_same_script_different_args_the_more_specific_entry_owns_the_process(self):
        script = r"C:\tools\belt.py"
        plain = App(name="Plain", command=script)                       # derived: any process running the script
        mini = App(name="Mini", command=script, args=("--mini",), match=(script, "--mini"))
        world = [P(10, "python.exe", r'python "C:\tools\belt.py"'),
                 P(11, "python.exe", r'python "C:\tools\belt.py" --mini'),
                 P(12, "python.exe", "mini's helper", ppid=11)]
        for_plain = stop.find_targets(0, [plain, mini], world, 999)
        for_mini = stop.find_targets(1, [plain, mini], world, 999)
        self.assertEqual({p.pid for p in for_plain.everything}, {10})
        self.assertEqual({p.pid for p in for_mini.everything}, {11, 12})
        # ...and the dots agree: with only the mini running, "Plain" is not shown as running
        self.assertEqual(procs.running_states([plain, mini], [world[1]]), [False, True])
        self.assertEqual(procs.running_states([plain, mini], [world[0]]), [True, False])

    def test_an_exact_tie_leaves_the_process_alone_for_both_entries(self):
        twin_a = App(name="A", command="x.exe", match=("same",))
        twin_b = App(name="B", command="x.exe", match=("same",))
        world = [P(10, "python.exe", "python same")]
        for_a = stop.find_targets(0, [twin_a, twin_b], world, 999)
        self.assertEqual(for_a.owned, [])
        self.assertEqual(for_a.rivals, ["B"])
        self.assertEqual(stop.find_targets(1, [twin_a, twin_b], world, 999).rivals, ["A"])
        # both still count as "running" for their dots
        self.assertEqual(procs.running_states([twin_a, twin_b], world), [True, True])

    def test_the_launcher_its_ancestors_and_system_processes_are_off_limits(self):
        apps = [App(name="X", command="x.exe", match=("tagx",))]
        world = [P(1, "explorer.exe", "explorer tagx", ppid=0),                # matches, but protected
                 P(30, "cmd.exe", "console that started the launcher tagx", ppid=1),   # matches; is an ancestor of us
                 P(31, "pythonw.exe", "the launcher", ppid=30),
                 P(10, "python.exe", "python d.py tagx"),
                 P(11, "explorer.exe", "child", ppid=10),                       # protected name as a descendant
                 P(12, "python.exe", "launcher's own child", ppid=31)]
        targets = stop.find_targets(0, apps, world, own_pid=31)
        self.assertEqual({p.pid for p in targets.owned}, {10})
        self.assertEqual(targets.descendants, [])
        # the launcher's own pid never matches, even if its command line contained the text
        self.assertNotIn(31, {p.pid for p in targets.everything})

    def test_a_running_process_not_in_the_snapshot_parent_chain_is_not_walked_upwards(self):
        apps = [App(name="X", command="x.exe", match=("tagx",))]
        world = [P(5, "python.exe", "the parent of X, not X"), P(10, "python.exe", "python d.py tagx", ppid=5)]
        self.assertEqual({p.pid for p in stop.find_targets(0, apps, world, 999).everything}, {10})


# --------------------------------------------------------------------------- pretend Windows
class FakeWorld:
    """A process table plus fake close / terminate / clock, for testing the stop logic quickly."""

    def __init__(self, processes, closes=False, refuse=()):
        self.live = {p.pid: p for p in processes}
        self.closes = closes            # does WM_CLOSE make the matched processes exit?
        self.refuse = dict(refuse)      # pid -> Win32 error code terminate() should fail with
        self.now = 0.0
        self.closed_for = []
        self.terminated = []
        self.close_window_count = 1
        self.on_close = None            # optional hook(world, pids)

    def scan(self):
        return list(self.live.values())

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def close(self, pids):
        pids = set(pids)
        self.closed_for.append(pids)
        if self.on_close:
            self.on_close(self, pids)
        if self.closes:
            for pid in list(pids):
                self.live.pop(pid, None)
        return self.close_window_count

    def terminate(self, pid, started=0):
        if pid in self.refuse:
            return self.refuse[pid]
        self.terminated.append(pid)
        self.live.pop(pid, None)
        return 0

    def stop(self, app, apps, **kw):
        return stop.stop_app(app, apps, scan=self.scan, own_pid=50, close=self.close, terminate=self.terminate,
                             sleep=self.sleep, clock=self.clock, **kw)


def run_fake_stop(apps, name, processes, **world_kw):
    world = FakeWorld(processes, **world_kw)
    app = next(a for a in apps if a.name == name)
    return world.stop(app, apps), world


class StopByClosingLogicTests(unittest.TestCase):
    def apps(self):
        return [App(name="X", command="x.exe", match=("tagx",)), App(name="Y", command="y.exe", match=("tagy",))]

    def tree(self):
        return [P(1, "explorer.exe", ppid=0), P(10, "python.exe", "python d.py tagx"),
                P(11, "python.exe", "child", ppid=10), P(12, "python.exe", "grandchild", ppid=11),
                P(20, "python.exe", "python d.py tagy"), P(21, "python.exe", "y child", ppid=20),
                P(30, "notepad.exe", "unrelated")]

    def test_a_polite_close_that_works_means_nothing_is_terminated(self):
        world = FakeWorld(self.tree(), closes=True)
        result = world.stop(self.apps()[0], self.apps())
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Stopped X")
        self.assertEqual(world.terminated, [])
        self.assertEqual(world.closed_for, [{10, 11, 12}])
        self.assertEqual(result.windows_closed, 1)

    def test_without_any_window_there_is_no_waiting_and_the_tree_goes_children_first(self):
        world = FakeWorld(self.tree())
        world.close_window_count = 0
        result = world.stop(self.apps()[0], self.apps())
        self.assertTrue(result.ok, result.message)
        self.assertEqual(world.terminated, [12, 11, 10])
        self.assertLess(world.now, 1.0, "no window was asked to close, so there is nothing to wait for")
        self.assertEqual(result.killed, [12, 11, 10])

    def test_a_window_that_ignores_the_close_is_given_its_two_seconds_then_terminated(self):
        world = FakeWorld(self.tree())             # close "works" as a call but nothing exits
        result = world.stop(self.apps()[0], self.apps())
        self.assertTrue(result.ok, result.message)
        self.assertGreaterEqual(world.now, stop.CLOSE_WAIT)
        self.assertLess(world.now, stop.CLOSE_WAIT + 1.0)
        self.assertEqual(set(world.terminated), {10, 11, 12})

    def test_orphans_left_behind_by_the_polite_close_are_ended_too(self):
        def parent_exits(world, pids):
            world.live.pop(10, None)            # the matched process quits, its children linger
        world = FakeWorld(self.tree())
        world.on_close = parent_exits
        result = world.stop(self.apps()[0], self.apps())
        self.assertTrue(result.ok, result.message)
        self.assertEqual(set(world.terminated), {11, 12})
        self.assertIn(30, world.live)

    def test_only_the_entrys_own_processes_are_ever_closed_or_terminated(self):
        world = FakeWorld(self.tree())
        world.stop(self.apps()[0], self.apps())
        for pid in (1, 20, 21, 30):
            self.assertIn(pid, world.live)
        self.assertTrue(all(pids <= {10, 11, 12} for pids in world.closed_for))
        world2 = FakeWorld(self.tree())
        world2.stop(self.apps()[1], self.apps())
        self.assertEqual(set(world2.terminated), {20, 21})
        for pid in (1, 10, 11, 12, 30):
            self.assertIn(pid, world2.live)

    def test_a_process_that_refuses_to_die_is_reported(self):
        world = FakeWorld(self.tree(), refuse={11: 5})
        world.close_window_count = 0
        result = world.stop(self.apps()[0], self.apps())
        self.assertFalse(result.ok)
        self.assertEqual(result.level, "error")
        self.assertTrue(result.message.startswith("Could not stop X: "), result.message)
        self.assertIn("would not end", result.message)
        self.assertIn("access denied", result.message)

    def test_an_entry_whose_match_hits_far_too_many_processes_is_refused(self):
        apps = [App(name="Broad", command="x.exe", match=("python",))]
        world = FakeWorld([P(1, "explorer.exe", ppid=0)] + [P(100 + i, "python.exe", "python job") for i in range(13)])
        result = world.stop(apps[0], apps)
        self.assertFalse(result.ok)
        self.assertIn("matches 13 processes", result.message)
        self.assertIn("narrower match", result.message)
        self.assertEqual((world.terminated, world.closed_for), ([], []))
        self.assertEqual(len(world.live), 14)

    def test_twelve_matched_processes_is_still_fine(self):
        apps = [App(name="Wide", command="x.exe", match=("worker",))]
        world = FakeWorld([P(1, "explorer.exe", ppid=0)] + [P(100 + i, "python.exe", "python worker") for i in range(12)])
        world.close_window_count = 0
        self.assertTrue(world.stop(apps[0], apps).ok)
        self.assertEqual(len(world.terminated), 12)

    def test_not_running_is_a_quiet_success(self):
        world = FakeWorld([P(1, "explorer.exe", ppid=0)])
        result = world.stop(self.apps()[0], self.apps())
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "X is not running")
        self.assertEqual((world.terminated, world.closed_for), ([], []))

    def test_entries_that_cannot_be_detected_cannot_be_stopped(self):
        page = App(name="Page", command=r"C:\pages\index.html")
        world = FakeWorld(self.tree())
        result = world.stop(page, [page])
        self.assertFalse(result.ok)
        self.assertIn("cannot be detected as running", result.message)
        self.assertEqual((world.terminated, world.closed_for), ([], []))

    def test_unreadable_process_list(self):
        apps = self.apps()
        result = stop.stop_app(apps[0], apps, scan=lambda: None, own_pid=50)
        self.assertFalse(result.ok)
        self.assertIn("could not read the list of running programs", result.message)

    def test_a_tie_between_entries_touches_nothing_and_names_the_rival(self):
        apps = [App(name="A", command="x.exe", match=("same",)), App(name="B", command="x.exe", match=("same",))]
        world = FakeWorld([P(10, "python.exe", "python same"), P(11, "python.exe", "child", ppid=10)])
        result = world.stop(apps[0], apps)
        self.assertFalse(result.ok)
        self.assertIn("also match B", result.message)
        self.assertEqual((world.terminated, world.closed_for), ([], []))
        self.assertEqual(len(world.live), 2)

    def test_a_process_reused_pid_is_not_terminated(self):
        """terminate() is handed the snapshot's start time so it can refuse a newer process with the same pid."""
        world = FakeWorld(self.tree())
        world.close_window_count = 0
        calls = []
        original = world.terminate
        world.terminate = lambda pid, started=0: (calls.append((pid, started)), original(pid, started))[1]
        world.stop(self.apps()[0], self.apps())
        self.assertEqual(dict(calls), {10: 1010, 11: 1011, 12: 1012})


class StopCommandLogicTests(support.TmpDirTestCase):
    def app(self, **kw):
        exe = support.python_console()
        base = dict(name="X", command="x.exe", match=("tagx",), stop_command=exe, stop_args=("-c", "pass"))
        base.update(kw)
        return App(**base)

    def running_world(self):
        return FakeWorld([P(1, "explorer.exe", ppid=0), P(10, "python.exe", "python d.py tagx"),
                          P(11, "python.exe", "child", ppid=10)])

    class FakeProc:
        def __init__(self, codes):
            self.codes = list(codes)       # what poll() returns on successive calls; last one repeats
            self.returncode = None

        def poll(self):
            return self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]

    def test_success_runs_the_command_then_sees_the_app_disappear(self):
        world = self.running_world()
        app = self.app(working_dir=str(self.tmp))
        started = []

        def start(plan):
            started.append(plan)
            return self.FakeProc([None, 0])

        ticks = []
        real_sleep = world.sleep

        def sleep(seconds):
            ticks.append(seconds)
            real_sleep(seconds)
            if len(ticks) == 3:                # the app finally quits after a moment
                world.live.pop(10); world.live.pop(11)

        result = stop.stop_app(app, [app], scan=world.scan, own_pid=50, close=world.close, terminate=world.terminate,
                               start=start, sleep=sleep, clock=world.clock)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.message, "Stopped X")
        self.assertEqual(result.method, "stop_command")
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0].cmdline, subprocess.list2cmdline([support.python_console(), "-c", "pass"]))
        self.assertEqual(started[0].creationflags, CREATE_NO_WINDOW)       # detached, no console
        self.assertEqual(started[0].cwd, str(self.tmp))
        self.assertEqual((world.terminated, world.closed_for), ([], []), "a stop_command is used INSTEAD of killing")

    def test_an_app_that_lingers_is_reported_and_never_killed(self):
        world = self.running_world()
        app = self.app()
        result = stop.stop_app(app, [app], scan=world.scan, own_pid=50, close=world.close, terminate=world.terminate,
                               start=lambda plan: self.FakeProc([0]), sleep=world.sleep, clock=world.clock)
        self.assertFalse(result.ok)
        self.assertEqual(result.level, "warn")
        self.assertEqual(result.message, "X is still open (it may be asking you something)")
        self.assertGreaterEqual(world.now, stop.STOP_COMMAND_WAIT)
        self.assertLess(world.now, stop.STOP_COMMAND_WAIT + 1)
        self.assertEqual((world.terminated, world.closed_for), ([], []))
        self.assertEqual(len(world.live), 3, "nothing may be touched")

    def test_missing_stop_command_is_reported_and_the_app_is_left_alone(self):
        world = self.running_world()
        app = self.app(stop_command=str(self.tmp / "no-such-program.exe"))
        started = []
        result = stop.stop_app(app, [app], scan=world.scan, own_pid=50, close=world.close, terminate=world.terminate,
                               start=lambda plan: started.append(plan), sleep=world.sleep, clock=world.clock)
        self.assertFalse(result.ok)
        self.assertEqual(result.level, "error")
        self.assertTrue(result.message.startswith("Could not stop X: program not found"), result.message)
        self.assertEqual(started, [])
        self.assertEqual((world.terminated, world.closed_for), ([], []))
        self.assertEqual(len(world.live), 3)

    def test_a_stop_command_that_cannot_start_is_reported(self):
        world = self.running_world()
        app = self.app()

        def boom(plan):
            raise LaunchError("access denied")

        result = stop.stop_app(app, [app], scan=world.scan, own_pid=50, start=boom, sleep=world.sleep,
                               clock=world.clock)
        self.assertEqual(result.message, "Could not stop X: access denied")

    def test_a_stop_command_that_fails_is_reported_with_its_exit_code(self):
        world = self.running_world()
        app = self.app()
        result = stop.stop_app(app, [app], scan=world.scan, own_pid=50, close=world.close, terminate=world.terminate,
                               start=lambda plan: self.FakeProc([None, 1]), sleep=world.sleep, clock=world.clock)
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Could not stop X: the stop command failed (exit code 1)")
        self.assertEqual(world.terminated, [])

    def test_a_nonzero_exit_does_not_matter_if_the_app_is_gone_anyway(self):
        world = self.running_world()
        app = self.app()

        def start(plan):
            world.live.pop(10); world.live.pop(11)           # "no copy was running" style: it is gone
            return self.FakeProc([3])

        result = stop.stop_app(app, [app], scan=world.scan, own_pid=50, start=start, sleep=world.sleep,
                               clock=world.clock)
        self.assertTrue(result.ok, result.message)

    def test_the_stop_command_is_found_in_the_same_ways_as_a_command(self):
        for name, content, expected_mode in (("q.cmd", "@exit /b 0\r\n", "cmd"), ("q.ps1", "exit 0\r\n", "powershell"),
                                             ("q.py", "pass\r\n", "python")):
            script = self.write_text(name, content)
            plan = commands.build_stop_plan(self.app(stop_command=str(script), stop_args=("now",)))
            self.assertEqual(plan.mode, expected_mode, name)
            self.assertFalse(plan.has_console, name)

    def test_a_document_is_not_a_valid_stop_command(self):
        page = self.write_text("x.html", "<html>")
        with self.assertRaises(LaunchError):
            commands.build_stop_plan(self.app(stop_command=str(page)))

    def test_no_stop_command_means_no_plan(self):
        self.assertIsNone(commands.build_stop_plan(App(name="n", command="x.exe")))

    def test_shipped_ui_report_tool_entry_asks_the_app_to_quit_with_dash_dash_quit(self):
        apps = {a.name: a for a in config.load_apps(support.ROOT / "apps.json").apps}
        plan = commands.build_stop_plan(apps["UI Report Tool"], check=False)
        self.assertEqual(plan.cmdline, r'"G:\UI Report Tool\.venv\Scripts\pythonw.exe" "G:\UI Report Tool\launch.pyw" --quit')
        self.assertEqual(plan.cwd, r"G:\UI Report Tool")
        self.assertEqual(plan.creationflags, CREATE_NO_WINDOW)
        for name in ("UtilityBelt", "UtilityBelt Mini", "Screener shell"):
            self.assertEqual(apps[name].stop_command, "", name)


# --------------------------------------------------------------------------- real dummy processes
class RealDummies(support.TmpDirTestCase):
    """Base: start dummy apps (never real ones), always clean them up."""

    def setUp(self):
        super().setUp()
        self.pids = []
        self.popens = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for pid in self.pids:
            support.kill_tree(pid)
        for popen in self.popens:
            popen.wait(10)
            for stream in (popen.stdin, popen.stdout, popen.stderr):
                if stream:
                    stream.close()

    def entry(self, name, *extra, tag=None, **app_kw):
        tag = tag or f"stoptest-{name}-{self.tmp.name}"
        marker = self.tmp / f"{name}.json"
        defaults = dict(name=name, command=str(support.pythonw()),
                        args=(str(support.DUMMY), str(marker), f"--tag={tag}", "--sleep", "90", *extra),
                        working_dir=str(self.tmp), match=(tag,))
        defaults.update(app_kw)
        return App(**defaults), marker

    def start(self, app, marker):
        launch_app(app)
        info = support.wait_for(marker, 30)
        self.assertIsNotNone(info, f"{app.name} never started")
        self.pids += [info["pid"]] + [info[k] for k in ("child_pid", "grandchild_pid") if k in info]
        return info

    def sleeper(self):
        """A process that belongs to nobody: must survive every stop."""
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(90)", f"unrelated-{self.tmp.name}"],
                             creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.pids.append(p.pid)
        self.popens.append(p)
        return p.pid


class StopCommandRealTests(RealDummies):
    def test_success_the_dummy_is_asked_to_quit_and_disappears(self):
        quit_file = self.tmp / "quit.flag"
        app, marker = self.entry("Quitter", "--quit-file", str(quit_file),
                                 stop_command=str(support.pythonw()), stop_args=(str(support.DUMMY_STOP), str(quit_file)))
        info = self.start(app, marker)
        result = stop.stop_app(app, [app], stop_wait=20)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.method, "stop_command")
        self.assertEqual(result.killed, [], "a stop_command is used instead of killing anything")
        self.assertTrue(support.wait_dead(info["pid"], 5))

    def test_the_app_shrugs_it_off_so_it_is_reported_and_not_killed(self):
        quit_file = self.tmp / "never-created.flag"
        app, marker = self.entry("Stubborn", "--quit-file", str(quit_file),
                                 stop_command=str(support.pythonw()),
                                 stop_args=(str(support.DUMMY_STOP), str(quit_file), "--noop"))
        info = self.start(app, marker)
        started = time.time()
        result = stop.stop_app(app, [app], stop_wait=2.5)
        self.assertFalse(result.ok)
        self.assertEqual(result.level, "warn")
        self.assertEqual(result.message, "Stubborn is still open (it may be asking you something)")
        self.assertGreaterEqual(time.time() - started, 2.4)
        self.assertTrue(support.pid_alive(info["pid"]), "the app must NOT be force-killed")

    def test_the_stop_command_program_is_missing(self):
        app, marker = self.entry("NoStopper", stop_command=str(self.tmp / "missing-stopper.exe"))
        info = self.start(app, marker)
        result = stop.stop_app(app, [app], stop_wait=2)
        self.assertFalse(result.ok)
        self.assertTrue(result.message.startswith("Could not stop NoStopper: program not found"), result.message)
        self.assertTrue(support.pid_alive(info["pid"]))

    def test_the_stop_command_fails_with_an_exit_code(self):
        quit_file = self.tmp / "q.flag"
        app, marker = self.entry("Failing", "--quit-file", str(quit_file), stop_command=str(support.pythonw()),
                                 stop_args=(str(support.DUMMY_STOP), str(quit_file), "--noop", "--exit", "1"))
        info = self.start(app, marker)
        result = stop.stop_app(app, [app], stop_wait=8)
        self.assertFalse(result.ok)
        self.assertIn("exit code 1", result.message)
        self.assertTrue(support.pid_alive(info["pid"]))


class StopByClosingRealTests(RealDummies):
    def test_a_window_that_accepts_the_polite_close_ends_the_app_without_terminating_anything(self):
        app, marker = self.entry("Polite", "--window", "close")
        info = self.start(app, marker)
        result = stop.stop_app(app, [app], close_wait=8)
        self.assertTrue(result.ok, result.message)
        self.assertGreaterEqual(result.windows_closed, 1)
        self.assertEqual(result.killed, [], "the app closed itself when asked nicely")
        self.assertTrue(support.wait_dead(info["pid"], 5))

    def test_a_window_that_ignores_the_close_is_terminated_after_the_wait(self):
        app, marker = self.entry("Ignorer", "--window", "ignore")
        info = self.start(app, marker)
        started = time.time()
        result = stop.stop_app(app, [app], close_wait=1.0)
        self.assertTrue(result.ok, result.message)
        self.assertGreaterEqual(result.windows_closed, 1)
        self.assertIn(info["pid"], result.killed)
        self.assertGreaterEqual(time.time() - started, 1.0, "it should have waited out the polite close first")
        self.assertTrue(support.wait_dead(info["pid"], 5))

    def test_the_app_its_child_and_its_grandchild_all_die_and_bystanders_do_not(self):
        app, marker = self.entry("Family", "--child")
        other, other_marker = self.entry("Neighbour", "--child")
        info = self.start(app, marker)
        neighbour = self.start(other, other_marker)
        loner = self.sleeper()
        for pid in (info["child_pid"], info["grandchild_pid"]):
            self.assertTrue(support.pid_alive(pid))

        result = stop.stop_app(app, [app, other], close_wait=0.5)
        self.assertTrue(result.ok, result.message)
        for pid in (info["pid"], info["child_pid"], info["grandchild_pid"]):
            self.assertTrue(support.wait_dead(pid, 8), f"process {pid} should have been ended")
        self.assertEqual(set(result.killed), {info["pid"], info["child_pid"], info["grandchild_pid"]})
        for pid in (neighbour["pid"], neighbour["child_pid"], neighbour["grandchild_pid"], loner):
            self.assertTrue(support.pid_alive(pid), f"process {pid} belongs to someone else and must survive")

    def test_a_running_entry_does_not_need_its_window_or_its_children_to_cooperate(self):
        app, marker = self.entry("Plain")
        info = self.start(app, marker)
        result = stop.stop_app(app, [app], close_wait=0.5)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.windows_closed, 0)
        self.assertTrue(support.wait_dead(info["pid"], 5))

    def test_a_console_app_and_its_own_console_host_both_go(self):
        """needs_console entries run inside a console host (a child process); it is part of the app's tree."""
        app, marker = self.entry("Console", command=str(support.python_console()), needs_console=True, window="hidden")
        info = self.start(app, marker)
        hosts = [p.pid for p in procs.list_processes() if p.ppid == info["pid"] and p.name.lower() == "conhost.exe"]
        self.assertTrue(hosts, "expected a console host started by the console app")
        result = stop.stop_app(app, [app], close_wait=0.5)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(support.wait_dead(info["pid"], 8))
        for pid in hosts:
            self.assertTrue(support.wait_dead(pid, 8), "its console host should go with it")

    def test_stopping_something_that_is_not_running_is_a_quiet_success_and_touches_nothing(self):
        app, marker = self.entry("Ghost")
        loner = self.sleeper()
        result = stop.stop_app(app, [app], close_wait=0.3)
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Ghost is not running")
        self.assertTrue(support.pid_alive(loner))


class SameScriptDifferentArgsRealTests(RealDummies):
    """Two entries run the very same script; they differ only by an argument (the "match" case)."""

    def pair(self):
        script = str(support.DUMMY)
        plain = App(name="Plain", command=script, args=(str(self.tmp / "plain.json"), "--sleep", "90"),
                    working_dir=str(self.tmp))                                   # derived match: the script
        mini = App(name="Mini", command=script, args=(str(self.tmp / "mini.json"), "--mini", "--sleep", "90"),
                   working_dir=str(self.tmp), match=(script, "--mini"))          # explicit: the script AND --mini
        return plain, mini

    def start_both(self):
        plain, mini = self.pair()
        a = self.start(plain, self.tmp / "plain.json")
        b = self.start(mini, self.tmp / "mini.json")
        self.assertEqual(procs.running_states([plain, mini], procs.list_processes()), [True, True])
        return plain, mini, a["pid"], b["pid"]

    def test_stopping_the_plain_entry_cannot_hit_the_mini_entrys_process(self):
        plain, mini, plain_pid, mini_pid = self.start_both()
        result = stop.stop_app(plain, [plain, mini], close_wait=0.3)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.killed, [plain_pid])
        self.assertTrue(support.wait_dead(plain_pid, 8))
        self.assertTrue(support.pid_alive(mini_pid), "Mini's process must not be touched")
        # and the dots agree
        self.assertEqual(procs.running_states([plain, mini], procs.list_processes()), [False, True])

    def test_stopping_the_mini_entry_cannot_hit_the_plain_entrys_process(self):
        plain, mini, plain_pid, mini_pid = self.start_both()
        result = stop.stop_app(mini, [plain, mini], close_wait=0.3)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.killed, [mini_pid])
        self.assertTrue(support.wait_dead(mini_pid, 8))
        self.assertTrue(support.pid_alive(plain_pid), "Plain's process must not be touched")
        self.assertEqual(procs.running_states([plain, mini], procs.list_processes()), [True, False])

    def test_an_unlisted_entry_cannot_be_used_to_reach_a_listed_ones_process(self):
        """If the stopped entry is not even in the list, it is judged against the others all the same."""
        plain, mini, plain_pid, mini_pid = self.start_both()
        result = stop.stop_app(plain, [mini], close_wait=0.3)        # 'plain' appended as an extra entry
        self.assertEqual(result.killed, [plain_pid])
        self.assertTrue(support.pid_alive(mini_pid))

    def test_two_identical_entries_are_a_tie_and_nothing_is_stopped(self):
        script = str(support.DUMMY)
        twin_a = App(name="TwinA", command=script, args=(str(self.tmp / "t.json"), "--sleep", "90"),
                     working_dir=str(self.tmp))
        twin_b = App(name="TwinB", command=script, args=(str(self.tmp / "t.json"), "--sleep", "90"),
                     working_dir=str(self.tmp))
        info = self.start(twin_a, self.tmp / "t.json")
        result = stop.stop_app(twin_a, [twin_a, twin_b], close_wait=0.3)
        self.assertFalse(result.ok)
        self.assertIn("also match TwinB", result.message)
        self.assertTrue(support.pid_alive(info["pid"]))


class WindowsActionTests(RealDummies):
    def test_terminate_refuses_a_process_that_is_not_the_one_in_the_snapshot(self):
        app, marker = self.entry("Victim")
        info = self.start(app, marker)
        snapshot = next(p for p in procs.list_processes() if p.pid == info["pid"])
        self.assertNotEqual(snapshot.started, 0)
        # a wrong start time stands for "this pid has been reused by a different process"
        self.assertEqual(stop.terminate_process(info["pid"], snapshot.started + 12345), 0)
        self.assertTrue(support.pid_alive(info["pid"]), "a different process with that pid must be left alone")
        self.assertEqual(stop.terminate_process(info["pid"], snapshot.started), 0)
        self.assertTrue(support.wait_dead(info["pid"], 5))

    def test_terminating_a_pid_that_is_already_gone_counts_as_done(self):
        marker = self.tmp / "short.json"
        short = App(name="Short", command=str(support.pythonw()), args=(str(support.DUMMY), str(marker)),
                    working_dir=str(self.tmp))                       # writes its marker and exits at once
        launch_app(short)
        info = support.wait_for(marker, 30)
        self.assertIsNotNone(info)
        self.assertTrue(support.wait_dead(info["pid"], 10))
        self.assertEqual(stop.terminate_process(info["pid"], 0), 0)

    def test_close_windows_only_posts_to_the_given_processes_windows(self):
        mine, mine_marker = self.entry("Mine", "--window", "close")
        theirs, theirs_marker = self.entry("Theirs", "--window", "close")
        a = self.start(mine, mine_marker)
        b = self.start(theirs, theirs_marker)
        self.assertEqual(stop.close_windows(set()), 0)
        self.assertEqual(stop.close_windows({999999}), 0)
        self.assertGreaterEqual(stop.close_windows({a["pid"]}), 1)
        self.assertTrue(support.wait_dead(a["pid"], 8), "its window was asked to close")
        time.sleep(0.3)
        self.assertTrue(support.pid_alive(b["pid"]), "the other dummy's window was not asked")


if __name__ == "__main__":
    unittest.main()
