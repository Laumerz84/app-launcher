"""Running-detection logic: matching command lines / exe paths, and the real process scan."""
import os
import subprocess
import sys
import time
import unittest

import support
from applauncher import config, procs
from applauncher.commands import CREATE_NO_WINDOW
from applauncher.config import App
from applauncher.procs import Proc

UB = r"F:\ClodCode\utilitybelt"
SHELL_PS1 = r"C:\Users\andre\Python\screener-jobs\shell\screener-shell.ps1"


def p(pid, name, cmdline="", exe=""):
    return Proc(pid=pid, name=name, exe=exe or name, cmdline=cmdline)


# What the real processes look like (taken from a live process listing).
UI_REPORT_PROCS = [
    p(10, "pythonw.exe", r'"G:\UI Report Tool\.venv\Scripts\pythonw.exe" "G:\UI Report Tool\launch.pyw"'),
    p(11, "pythonw.exe", r'"C:\Users\x\AppData\Local\Python\pythonw.exe" "G:\UI Report Tool\launch.pyw"'),
]
BELT_PROCS = [
    p(20, "cmd.exe", r'C:\Windows\System32\cmd.exe /c "F:\ClodCode\utilitybelt\belt.cmd"'),
    p(21, "python.exe", r'python  "F:\ClodCode\utilitybelt\belt.py"'),
]
MINI_PROCS = [p(30, "python.exe", "python belt.py --mini")]
SCREENER_PROCS = [p(40, "powershell.exe",
                    r'''"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoExit -ExecutionPolicy Bypass '''
                    r'''-Command ". '%s'"''' % SHELL_PS1)]
NOISE = [p(1, "explorer.exe", r"C:\Windows\explorer.exe"),
         p(2, "python.exe", r"python -m http.server"),
         p(3, "cmd.exe", r"C:\WINDOWS\system32\cmd.exe /d /s /c desktop-commander"),
         p(4, "powershell.exe", r"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe"),
         p(5, "notepad.exe", r"notepad.exe F:\ClodCode\utilitybelt\belt.cmd")]  # an editor merely has the file open


class ShippedAppsDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apps = {a.name: a for a in config.load_apps(support.ROOT / "apps.json").apps}

    def state(self, name, procs_list):
        return procs.running_states([self.apps[name]], procs_list)[0]

    def test_each_requested_app_is_recognised_when_running(self):
        cases = {"UI Report Tool": UI_REPORT_PROCS, "UtilityBelt": BELT_PROCS, "UtilityBelt Mini": MINI_PROCS,
                 "Screener shell": SCREENER_PROCS}
        for name, running in cases.items():
            with self.subTest(name):
                self.assertIs(self.state(name, NOISE + running), True)

    def test_each_is_not_running_among_only_noise(self):
        for name in ("UI Report Tool", "UtilityBelt", "UtilityBelt Mini", "Screener shell", "UtilityBelt Dash"):
            with self.subTest(name):
                self.assertIs(self.state(name, NOISE), False)

    def test_apps_do_not_mistake_each_other(self):
        everything = UI_REPORT_PROCS + BELT_PROCS + SCREENER_PROCS  # no mini strip running
        self.assertIs(self.state("UtilityBelt Mini", everything), False)
        self.assertIs(self.state("UtilityBelt", MINI_PROCS + SCREENER_PROCS), False)

    def test_mini_is_found_by_its_python_process_not_the_short_lived_cmd(self):
        self.assertIs(self.state("UtilityBelt Mini", [p(1, "cmd.exe", r'cmd.exe /c "%s\belt-mini.cmd"' % UB)]), False)

    def test_ui_report_tool_from_another_copy_still_counts(self):
        desktop_copy = [p(9, "pythonw.exe", r'"C:\Users\a\Desktop\UI Report Tool\.venv\Scripts\pythonw.exe" '
                                            r'"C:\Users\a\Desktop\UI Report Tool\launch.pyw"')]
        self.assertIs(self.state("UI Report Tool", desktop_copy), True)

    def test_web_pages_cannot_be_detected(self):
        self.assertIsNone(self.state("Fractal Flame", NOISE))

    def test_slash_direction_and_case_do_not_matter(self):
        procs_list = [p(1, "PYTHON.EXE", 'python "f:/clodcode/UTILITYBELT/Belt.py"')]
        self.assertIs(self.state("UtilityBelt", procs_list), True)

    def test_utilitybelt_is_its_python_not_the_cmd_or_terminal_around_it(self):
        around = [p(1, "cmd.exe", r'cmd /c "F:\ClodCode\utilitybelt\belt.cmd"'),
                  p(2, "WindowsTerminal.exe", r"WindowsTerminal.exe --focus -w new -d F:\ClodCode\utilitybelt "
                                              r"cmd /c F:\ClodCode\utilitybelt\belt.cmd")]
        self.assertIs(self.state("UtilityBelt", around), False)


class DerivedMatcherTests(support.TmpDirTestCase):
    """Matching worked out from the command itself (no explicit "match")."""

    def test_script_marker_needs_a_script_host_process(self):
        script = self.write_text("tool.py", "pass")
        app = App(name="t", command=str(script))
        cmdline = f'"C:\\Python\\pythonw.exe" "{script}"'
        self.assertIs(procs.running_states([app], [p(1, "pythonw.exe", cmdline)])[0], True)
        # A text editor that merely has the same path on its command line is not the app.
        self.assertIs(procs.running_states([app], [p(2, "code.exe", f'code.exe "{script}"')])[0], False)

    def test_plain_program_matches_by_exe_path(self):
        app = App(name="n", command=r"C:\Windows\System32\notepad.exe")
        yes = p(1, "notepad.exe", "notepad.exe", exe=r"C:\Windows\System32\notepad.exe")
        other = p(2, "notepad.exe", "notepad.exe", exe=r"C:\Other\notepad.exe")
        self.assertIs(procs.running_states([app], [yes])[0], True)
        self.assertIs(procs.running_states([app], [other])[0], False)

    def test_bare_shell_with_nothing_to_tell_it_apart_is_unknown(self):
        app = App(name="shell", command=r"C:\Windows\System32\cmd.exe", args=("/k", "echo hi"))
        self.assertIsNone(procs.running_states([app], [p(1, "cmd.exe", "cmd.exe /k echo hi")])[0])

    def test_script_path_is_pulled_out_of_a_quoted_argument(self):
        text = r'''-Command ". 'C:\Some Dir\my shell.ps1'"'''
        self.assertEqual(procs._SCRIPT_RE.findall(text), [r"C:\Some Dir\my shell.ps1"])
        app = App(name="s", command=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                  args=("-NoExit", "-Command", r". 'C:\Some Dir\my shell.ps1'"))
        running = p(1, "powershell.exe", r'''powershell.exe -NoExit -Command ". 'C:\Some Dir\my shell.ps1'"''')
        self.assertIs(procs.running_states([app], [running])[0], True)

    def test_document_entries_are_unknown(self):
        page = self.write_text("x.html", "<html>")
        self.assertIsNone(procs.running_states([App(name="p", command=str(page))], [p(1, "chrome.exe")])[0])

    def test_explicit_match_needs_every_text(self):
        app = App(name="m", command="whatever.exe", match=("alpha", "beta"))
        self.assertIs(procs.running_states([app], [p(1, "x.exe", "x.exe alpha")])[0], False)
        self.assertIs(procs.running_states([app], [p(2, "x.exe", "x.exe BETA alpha")])[0], True)

    def test_our_own_process_is_ignored(self):
        app = App(name="m", command="whatever.exe", match=("launcher-self-test-marker",))
        mine = p(os.getpid(), "pythonw.exe", "pythonw.exe launcher-self-test-marker")
        self.assertIs(procs.running_states([app], [mine], ignore_pid=os.getpid())[0], False)
        self.assertIs(procs.running_states([app], [mine])[0], True)

    def test_no_process_list_means_unknown_for_everything(self):
        apps = [App(name="a", command="a.exe"), App(name="b", command="b.exe")]
        self.assertEqual(procs.running_states(apps, None), [None, None])

    def test_states_line_up_with_apps(self):
        apps = [App(name="a", command="x.exe", match=("aaa",)), App(name="b", command="x.exe", match=("bbb",))]
        self.assertEqual(procs.running_states(apps, [p(1, "x.exe", "x bbb")]), [False, True])


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class RealScanTests(unittest.TestCase):
    def setUp(self):
        self.mark = f"launcher-test-marker-{os.getpid()}-{int(time.time() * 1000)}"
        self.child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", self.mark],
                                      creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_child)

    def stop_child(self):
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait(timeout=10)

    def wait_until_listed(self):
        for _ in range(100):
            found = procs.list_processes()
            if found and any(pr.pid == self.child.pid and self.mark in pr.cmdline for pr in found):
                return found
            time.sleep(0.1)
        self.fail("the scan never saw the test process")

    def test_scan_sees_a_live_process_with_its_command_line_and_path(self):
        found = self.wait_until_listed()
        mine = next(pr for pr in found if pr.pid == self.child.pid)
        self.assertEqual(mine.name.lower(), os.path.basename(sys.executable).lower())
        self.assertEqual(os.path.normcase(mine.exe), os.path.normcase(sys.executable))
        self.assertIn(self.mark, mine.cmdline)
        self.assertTrue(any(pr.pid == os.getpid() for pr in found))

    def test_running_dot_logic_end_to_end_then_process_ends(self):
        app = App(name="live", command="whatever.exe", match=(self.mark,))
        self.assertEqual(procs.running_states([app], self.wait_until_listed()), [True])
        self.child.kill()
        self.child.wait(timeout=10)
        self.assertEqual(procs.running_states([app], procs.list_processes()), [False])


if __name__ == "__main__":
    unittest.main()
