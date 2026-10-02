"""Detached launching, using dummy entries (never the real apps)."""
import ctypes
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

import support
from applauncher import commands, procs
from applauncher.commands import (CREATE_BREAKAWAY_FROM_JOB, CREATE_NEW_CONSOLE, CREATE_NO_WINDOW,
                                  LaunchError, LaunchPlan, launch, launch_app)
from applauncher.config import App


def dummy_app(marker, *extra, console=False, python=None, **kw):
    """An App entry that runs tests/dummy_app.py."""
    exe = python or (support.python_console() if console else support.pythonw())
    return App(name="dummy", command=exe, args=(str(support.DUMMY), str(marker), *extra), **kw)


class RealLaunchTests(support.TmpDirTestCase):
    def test_launched_with_its_own_working_folder_and_intact_args(self):
        marker = self.tmp / "m.json"
        workdir = self.tmp / "work dir"
        workdir.mkdir()
        pid = launch_app(dummy_app(marker, "first arg", "second", working_dir=str(workdir)))
        info = support.wait_for(marker)
        self.assertIsNotNone(info, "the dummy app never ran")
        self.assertEqual(os.path.normcase(info["cwd"]), os.path.normcase(str(workdir)))
        self.assertEqual(info["args"], ["first arg", "second"])
        self.assertEqual(info["pid"], pid)

    def test_console_program_started_without_a_console_gets_none(self):
        # python.exe is a console-subsystem exe: the hard case for "no console window".
        marker = self.tmp / "m.json"
        launch_app(dummy_app(marker, console=True, needs_console=False, working_dir=str(self.tmp)))
        info = support.wait_for(marker)
        self.assertIsNotNone(info)
        self.assertEqual(info["console_window"], 0)

    def test_gui_program_has_no_console(self):
        marker = self.tmp / "m.json"
        launch_app(dummy_app(marker, working_dir=str(self.tmp)))
        self.assertEqual(support.wait_for(marker)["console_window"], 0)

    def test_needs_console_gets_its_own_new_console(self):
        # window="hidden" so nothing appears on screen while testing; the console still exists.
        marker = self.tmp / "m.json"
        mine = ctypes.windll.kernel32.GetConsoleWindow()
        launch_app(dummy_app(marker, console=True, needs_console=True, window="hidden", working_dir=str(self.tmp)))
        info = support.wait_for(marker)
        self.assertIsNotNone(info)
        self.assertNotEqual(info["console_window"], 0)
        self.assertNotEqual(info["console_window"], mine)

    def test_child_keeps_running_after_the_launching_process_exits(self):
        marker, late = self.tmp / "m.json", self.tmp / "late.txt"
        helper = (
            "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
            "import support\n"
            "from applauncher.commands import launch_app\n"
            "from applauncher.config import App\n"
            "launch_app(App(name='d', command=support.pythonw(), args=(%r, %r, '--sleep', '3', '--late-marker', %r),"
            " working_dir=%r))\n"
        ) % (str(support.ROOT), str(support.ROOT / "tests"), str(support.DUMMY), str(marker), str(late),
             str(self.tmp))
        done = subprocess.run([sys.executable, "-c", helper], capture_output=True, text=True, timeout=60,
                              creationflags=CREATE_NO_WINDOW)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIsNotNone(support.wait_for(marker), "child never started")
        # The launcher process is gone by now; the child must still be alive to write this later.
        self.assertFalse(late.exists(), "child finished too early to prove anything")
        self.assertTrue(support.wait_for_file(late, 15), "child died with its launcher")

    def test_cmd_script_with_spaces_in_its_path_runs_through_cmd(self):
        folder = self.tmp / "with space"
        folder.mkdir()
        script = folder / "run it.cmd"
        script.write_text('@echo %CD%> "%~1"\r\n@echo %~2>> "%~1"\r\n', encoding="ascii")
        marker = self.tmp / "m.txt"
        app = App(name="cmd", command=str(script), args=(str(marker), "hello"), needs_console=True,
                  window="hidden", working_dir=str(folder))
        launch_app(app)
        self.assertTrue(support.wait_for_file(marker), "the .cmd never ran")
        lines = marker.read_text().split()
        self.assertEqual(os.path.normcase(" ".join(lines[:-1])), os.path.normcase(str(folder)))
        self.assertEqual(lines[-1], "hello")

    def test_powershell_script(self):
        script = self.write_text("go.ps1", "Set-Content -Path $args[0] -Value ((Get-Location).Path + '|' + $args[1])\n")
        marker = self.tmp / "m.txt"
        launch_app(App(name="ps", command=str(script), args=(str(marker), "x y"), working_dir=str(self.tmp)))
        self.assertTrue(support.wait_for_file(marker, 40), "the .ps1 never ran")
        where, arg = marker.read_text().strip().split("|")
        self.assertEqual(os.path.normcase(where), os.path.normcase(str(self.tmp)))
        self.assertEqual(arg, "x y")


def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)


class ShapeOfTheRealEntriesTests(support.TmpDirTestCase):
    """The shapes the real entries use (cmd /c script, powershell -NoExit -Command), with dummies, hidden."""

    def tearDown(self):
        for pid in getattr(self, "pids", []):
            kill_tree(pid)
        super().tearDown()

    def start(self, app):
        pid = launch_app(app)
        self.pids = getattr(self, "pids", []) + [pid]
        return pid

    def test_cmd_slash_c_script_stays_open_and_is_seen_as_running_until_killed(self):
        folder = self.tmp / "belt dir"
        folder.mkdir()
        script = folder / "belt.cmd"
        script.write_text('@echo started> "%~1"\r\n@ping -n 60 127.0.0.1 > nul\r\n', encoding="ascii")
        marker = self.tmp / "belt.txt"
        app = App(name="Belt", command=commands.cmd_exe(), args=("/c", str(script), str(marker)),
                  working_dir=str(folder), needs_console=True, window="hidden")
        pid = self.start(app)
        self.assertTrue(support.wait_for_file(marker), "the .cmd never ran")
        self.assertEqual(procs.running_states([app], procs.list_processes()), [True])
        kill_tree(pid)
        for _ in range(50):
            if procs.running_states([app], procs.list_processes()) == [False]:
                break
            time.sleep(0.1)
        self.assertEqual(procs.running_states([app], procs.list_processes()), [False])

    def test_powershell_noexit_command_with_a_spaced_path_like_the_screener_shell(self):
        folder = self.tmp / "shell dir"
        folder.mkdir()
        script = folder / "shell.ps1"
        marker = self.tmp / "shell.txt"
        script.write_text("Set-Content -Path '%s' -Value ((Get-Location).Path)\r\n" % marker, encoding="ascii")
        app = App(name="Shell", command=commands.powershell_exe(),
                  args=("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", ". '%s'" % script),
                  working_dir=str(self.tmp), needs_console=True, window="hidden")
        self.start(app)
        self.assertTrue(support.wait_for_file(marker, 40), "the powershell command never ran")
        self.assertEqual(os.path.normcase(marker.read_text().strip()), os.path.normcase(str(self.tmp)))
        # -NoExit: it must still be there, and recognised by the script path in its command line
        self.assertEqual(procs.running_states([app], procs.list_processes()), [True])


class LaunchFailureTests(support.TmpDirTestCase):
    def test_missing_program_is_a_launch_error_not_a_crash(self):
        with self.assertRaises(LaunchError) as ctx:
            launch_app(App(name="n", command=str(self.tmp / "nope.exe")))
        self.assertIn("not found", str(ctx.exception))

    def test_not_a_windows_program_gets_a_readable_message(self):
        bogus = self.tmp / "bogus.exe"
        bogus.write_text("this is not an executable")
        with self.assertRaises(LaunchError) as ctx:
            launch_app(App(name="n", command=str(bogus)))
        self.assertIn("not a valid Windows program", str(ctx.exception))


class FakePopen:
    """Records what launch() asked for."""

    def __init__(self, fail_first_with=None):
        self.calls = []
        self.fail_first_with = fail_first_with

    def __call__(self, cmdline, **kwargs):
        self.calls.append((cmdline, kwargs))
        if self.fail_first_with and len(self.calls) == 1:
            raise self.fail_first_with
        return mock.Mock(pid=4242)


class LaunchFlagTests(unittest.TestCase):
    def plan(self, **kw):
        base = dict(mode="exe", exe="C:\\x\\a.exe", cmdline="C:\\x\\a.exe /q", cwd="C:\\x",
                    creationflags=CREATE_NO_WINDOW)
        base.update(kw)
        return LaunchPlan(**base)

    def test_no_console_launch_is_detached_with_null_handles(self):
        popen = FakePopen()
        self.assertEqual(launch(self.plan(), popen=popen), 4242)
        cmdline, kw = popen.calls[0]
        self.assertEqual(cmdline, "C:\\x\\a.exe /q")
        self.assertEqual(kw["cwd"], "C:\\x")
        self.assertEqual(kw["executable"], "C:\\x\\a.exe")
        self.assertTrue(kw["creationflags"] & CREATE_NO_WINDOW)
        self.assertTrue(kw["creationflags"] & CREATE_BREAKAWAY_FROM_JOB)
        self.assertFalse(kw["creationflags"] & CREATE_NEW_CONSOLE)
        self.assertTrue(kw["close_fds"])
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(kw[stream], subprocess.DEVNULL)
        self.assertIsNone(kw["startupinfo"])

    def test_console_launch_asks_for_a_new_console_and_keeps_stdio_alone(self):
        popen = FakePopen()
        launch(self.plan(creationflags=CREATE_NEW_CONSOLE), popen=popen)
        _, kw = popen.calls[0]
        self.assertTrue(kw["creationflags"] & CREATE_NEW_CONSOLE)
        self.assertFalse(kw["creationflags"] & CREATE_NO_WINDOW)
        self.assertNotIn("stdout", kw)

    def test_minimized_and_hidden_set_the_show_window_state(self):
        for mode, expected in (("minimized", 7), ("hidden", 0)):
            popen = FakePopen()
            launch(self.plan(creationflags=CREATE_NEW_CONSOLE, window=mode), popen=popen)
            si = popen.calls[0][1]["startupinfo"]
            self.assertTrue(si.dwFlags & subprocess.STARTF_USESHOWWINDOW)
            self.assertEqual(si.wShowWindow, expected)

    def test_breakaway_refused_by_the_job_falls_back_to_a_plain_launch(self):
        denied = OSError(5, "Access is denied", None, 5)
        popen = FakePopen(fail_first_with=denied)
        self.assertEqual(launch(self.plan(), popen=popen), 4242)
        self.assertEqual(len(popen.calls), 2)
        self.assertTrue(popen.calls[0][1]["creationflags"] & CREATE_BREAKAWAY_FROM_JOB)
        self.assertFalse(popen.calls[1][1]["creationflags"] & CREATE_BREAKAWAY_FROM_JOB)

    def test_os_errors_become_readable_launch_errors(self):
        cases = {2: "file not found", 5: "access denied", 193: "not a valid Windows program",
                 740: "needs administrator rights"}
        for code, text in cases.items():
            popen = FakePopen(fail_first_with=OSError(code, "raw windows text", None, code))
            if code == 5:  # 5 is the breakaway fallback; make the retry fail as well
                popen = mock.Mock(side_effect=OSError(5, "raw", None, 5))
            with self.subTest(code=code):
                with self.assertRaises(LaunchError) as ctx:
                    launch(self.plan(), popen=popen)
                self.assertEqual(str(ctx.exception), text)

    def test_open_kind_uses_the_default_app_via_startfile(self):
        plan = LaunchPlan(mode="open", target="C:\\pages\\index.html", cwd="C:\\pages")
        with mock.patch("os.startfile") as startfile:
            self.assertIsNone(launch(plan))
        startfile.assert_called_once_with("C:\\pages\\index.html", cwd="C:\\pages")

    def test_open_kind_with_url_and_no_folder(self):
        plan = LaunchPlan(mode="open", target="https://example.com/")
        with mock.patch("os.startfile") as startfile:
            launch(plan)
        startfile.assert_called_once_with("https://example.com/")

    def test_open_failure_is_a_launch_error(self):
        plan = LaunchPlan(mode="open", target="C:\\pages\\index.html")
        with mock.patch("os.startfile", side_effect=OSError(2, "x", None, 2)):
            with self.assertRaises(LaunchError):
                launch(plan)


class EditorTests(unittest.TestCase):
    def test_edit_list_opens_notepad_on_the_file(self):
        popen = mock.Mock()
        commands.open_in_editor("F:\\x\\apps.json", popen=popen)
        argv = popen.call_args[0][0]
        self.assertEqual(os.path.basename(argv[0]).lower(), "notepad.exe")
        self.assertEqual(argv[1], "F:\\x\\apps.json")


if __name__ == "__main__":
    unittest.main()
