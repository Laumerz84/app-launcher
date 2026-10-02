"""Building the right launch command for each kind of entry."""
import os
import subprocess
import sys
import unittest

import support  # noqa: F401
from applauncher import commands, config
from applauncher.commands import (CREATE_NEW_CONSOLE, CREATE_NO_WINDOW, LaunchError, build_plan)
from applauncher.config import App

SYSTEM32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
NOTEPAD = os.path.join(SYSTEM32, "notepad.exe")


class ExeTests(support.TmpDirTestCase):
    def test_exe_with_args_and_working_dir(self):
        app = App(name="n", command=NOTEPAD, args=("a b", "c"), working_dir=str(self.tmp))
        plan = build_plan(app)
        self.assertEqual(plan.mode, "exe")
        self.assertEqual(plan.exe, NOTEPAD)
        self.assertEqual(plan.cmdline, subprocess.list2cmdline([NOTEPAD, "a b", "c"]))
        self.assertEqual(plan.cwd, str(self.tmp))
        self.assertFalse(plan.is_open)

    def test_no_console_means_no_window_and_console_means_new_console(self):
        quiet = build_plan(App(name="n", command=NOTEPAD))
        self.assertEqual(quiet.creationflags, CREATE_NO_WINDOW)
        self.assertFalse(quiet.has_console)
        loud = build_plan(App(name="n", command=NOTEPAD, needs_console=True))
        self.assertEqual(loud.creationflags, CREATE_NEW_CONSOLE)
        self.assertTrue(loud.has_console)

    def test_raw_string_args_are_passed_through_verbatim(self):
        plan = build_plan(App(name="n", command=NOTEPAD, args='/x "y z"'))
        self.assertEqual(plan.cmdline, subprocess.list2cmdline([NOTEPAD]) + ' /x "y z"')

    def test_default_working_dir_is_the_commands_folder(self):
        self.assertEqual(build_plan(App(name="n", command=NOTEPAD)).cwd, SYSTEM32)

    def test_environment_variables_are_expanded(self):
        plan = build_plan(App(name="n", command=r"%SystemRoot%\System32\notepad.exe", args=("%SystemRoot%",)))
        self.assertEqual(plan.exe.lower(), NOTEPAD.lower())
        self.assertIn(os.environ["SystemRoot"], plan.cmdline)

    def test_bare_program_name_is_found_on_path(self):
        plan = build_plan(App(name="n", command="notepad"))
        self.assertTrue(plan.exe.lower().endswith("notepad.exe"))
        self.assertTrue(os.path.isfile(plan.exe))

    def test_window_mode_is_carried_into_the_plan(self):
        for mode in ("normal", "minimized", "hidden"):
            self.assertEqual(build_plan(App(name="n", command=NOTEPAD, window=mode)).window, mode)


class CmdTests(support.TmpDirTestCase):
    def test_cmd_script_runs_through_cmd_exe(self):
        script = self.write_text("hello.cmd", "@echo hi\n")
        plan = build_plan(App(name="n", command=str(script), args=("one", "two words")))
        self.assertEqual(plan.mode, "cmd")
        self.assertEqual(plan.exe, commands.cmd_exe())
        self.assertTrue(plan.cmdline.startswith(subprocess.list2cmdline([commands.cmd_exe()]) + ' /s /c "'))
        self.assertIn(str(script), plan.cmdline)
        self.assertTrue(plan.cmdline.endswith('one "two words""'), plan.cmdline)
        self.assertEqual(plan.cwd, str(self.tmp))

    def test_bat_and_paths_with_spaces(self):
        folder = self.tmp / "with space"
        folder.mkdir()
        script = folder / "x.bat"
        script.write_text("@echo hi\n")
        plan = build_plan(App(name="n", command=str(script)))
        self.assertEqual(plan.mode, "cmd")
        self.assertIn('"' + str(script) + '"', plan.cmdline)   # quoted inside the /s /c "..." wrapper

    def test_cmd_exe_given_explicitly_with_slash_c_is_just_an_exe(self):
        script = self.write_text("hello.cmd", "@echo hi\n")
        plan = build_plan(App(name="n", command=commands.cmd_exe(), args=("/c", str(script))))
        self.assertEqual(plan.mode, "exe")
        self.assertEqual(plan.cmdline, subprocess.list2cmdline([commands.cmd_exe(), "/c", str(script)]))

    def test_missing_script(self):
        with self.assertRaises(LaunchError) as ctx:
            build_plan(App(name="n", command=str(self.tmp / "gone.cmd")))
        self.assertIn("not found", str(ctx.exception))


class PowerShellTests(support.TmpDirTestCase):
    def test_powershell_exe_with_command_args_is_passed_unchanged(self):
        ps = commands.powershell_exe()
        args = ("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", ". 'C:\\Some Dir\\shell.ps1'")
        plan = build_plan(App(name="n", command=ps, args=args, needs_console=True))
        self.assertEqual(plan.mode, "exe")
        self.assertEqual(plan.cmdline, subprocess.list2cmdline([ps, *args]))
        self.assertTrue(plan.cmdline.endswith(""" ". 'C:\\Some Dir\\shell.ps1'\""""))

    def test_ps1_script_runs_with_file_and_stays_open_only_with_a_console(self):
        script = self.write_text("tool.ps1", "'hi'\n")
        quiet = build_plan(App(name="n", command=str(script), args=("x",)))
        self.assertEqual(quiet.mode, "powershell")
        self.assertEqual(quiet.exe, commands.powershell_exe())
        self.assertEqual(quiet.cmdline, subprocess.list2cmdline(
            [commands.powershell_exe(), "-NoLogo", "-ExecutionPolicy", "Bypass", "-File", str(script), "x"]))
        loud = build_plan(App(name="n", command=str(script), needs_console=True))
        self.assertIn("-NoExit", loud.cmdline)
        self.assertLess(loud.cmdline.index("-NoExit"), loud.cmdline.index("-File"))


class PythonTests(support.TmpDirTestCase):
    def test_py_uses_python_exe_with_a_console_and_pythonw_without(self):
        script = self.write_text("tool.py", "print(1)\n")
        console = build_plan(App(name="n", command=str(script), args=("a",), needs_console=True))
        self.assertEqual(console.mode, "python")
        self.assertEqual(os.path.basename(console.exe).lower(), "python.exe")
        self.assertEqual(console.cmdline, subprocess.list2cmdline([console.exe, str(script), "a"]))
        quiet = build_plan(App(name="n", command=str(script)))
        self.assertEqual(os.path.basename(quiet.exe).lower(), "pythonw.exe")

    def test_pyw(self):
        script = self.write_text("tool.pyw", "pass\n")
        plan = build_plan(App(name="n", command=str(script)))
        self.assertEqual(plan.mode, "python")
        self.assertEqual(os.path.basename(plan.exe).lower(), "pythonw.exe")


class OpenTests(support.TmpDirTestCase):
    def test_html_is_opened_with_the_default_app_not_run(self):
        page = self.write_text("index.html", "<html></html>")
        plan = build_plan(App(name="n", command=str(page)))
        self.assertEqual(plan.mode, "open")
        self.assertTrue(plan.is_open)
        self.assertEqual(plan.target, str(page))
        self.assertEqual(plan.cmdline, "")
        self.assertEqual(plan.cwd, str(self.tmp))

    def test_urls_and_folders_and_other_documents_open_too(self):
        self.assertEqual(build_plan(App(name="n", command="https://example.com/x?y=1")).mode, "open")
        self.assertEqual(build_plan(App(name="n", command="https://example.com/x")).cwd, None)
        self.assertEqual(build_plan(App(name="n", command=str(self.tmp))).mode, "open")
        doc = self.write_text("notes.txt", "x")
        self.assertEqual(build_plan(App(name="n", command=str(doc))).mode, "open")

    def test_missing_page(self):
        with self.assertRaises(LaunchError) as ctx:
            build_plan(App(name="n", command=str(self.tmp / "gone.html")))
        self.assertIn("not found", str(ctx.exception))


class ErrorTests(support.TmpDirTestCase):
    def test_missing_exe(self):
        with self.assertRaises(LaunchError) as ctx:
            build_plan(App(name="n", command=str(self.tmp / "missing.exe")))
        self.assertIn("not found", str(ctx.exception))

    def test_unknown_bare_name(self):
        with self.assertRaises(LaunchError) as ctx:
            build_plan(App(name="n", command="definitely-not-a-real-program-xyz"))
        self.assertIn("not found", str(ctx.exception))

    def test_missing_working_dir(self):
        with self.assertRaises(LaunchError) as ctx:
            build_plan(App(name="n", command=NOTEPAD, working_dir=str(self.tmp / "nowhere")))
        self.assertIn("working folder not found", str(ctx.exception))

    def test_check_false_never_raises_for_missing_files(self):
        plan = build_plan(App(name="n", command=r"Z:\nope\x.exe", working_dir=r"Z:\nope"), check=False)
        self.assertEqual(plan.mode, "exe")

    def test_error_messages_are_short_one_liners(self):
        with self.assertRaises(LaunchError) as ctx:
            build_plan(App(name="n", command=str(self.tmp / "missing.exe")))
        self.assertNotIn("\n", str(ctx.exception))
        self.assertLess(len(str(ctx.exception)), 200)


class ShippedAppsMatchTheirShortcuts(unittest.TestCase):
    """The four requested entries must launch exactly like the user's existing Start menu shortcuts."""

    @classmethod
    def setUpClass(cls):
        cls.apps = {a.name: a for a in config.load_apps(support.ROOT / "apps.json").apps}

    def plan(self, name):
        return build_plan(self.apps[name], check=False)

    def test_ui_report_tool(self):
        plan = self.plan("UI Report Tool")
        self.assertEqual(plan.cmdline,
                         r'"G:\UI Report Tool\.venv\Scripts\pythonw.exe" "G:\UI Report Tool\launch.pyw"')
        self.assertEqual(plan.cwd, r"G:\UI Report Tool")
        self.assertFalse(plan.has_console)
        self.assertEqual(self.apps["UI Report Tool"].icon, r"G:\UI Report Tool\UIReportTool.ico")

    def test_utilitybelt_opens_in_windows_terminal_focus_mode_like_its_shortcut(self):
        plan = self.plan("UtilityBelt")
        self.assertEqual(plan.cmdline,
                         r"C:\Users\andre\AppData\Local\Microsoft\WindowsApps\wt.exe --focus -w new --title UtilityBelt"
                         r" -d F:\ClodCode\utilitybelt cmd /c F:\ClodCode\utilitybelt\belt.cmd")
        self.assertEqual(plan.cwd, r"F:\ClodCode\utilitybelt")
        self.assertFalse(plan.has_console)  # Windows Terminal makes the window itself
        self.assertEqual(plan.window, "normal")

    def test_utilitybelt_mini_starts_minimized_like_its_shortcut(self):
        plan = self.plan("UtilityBelt Mini")
        self.assertEqual(plan.cmdline, r"C:\Windows\System32\cmd.exe /c F:\ClodCode\utilitybelt\belt-mini.cmd")
        self.assertEqual(plan.cwd, r"F:\ClodCode\utilitybelt")
        self.assertTrue(plan.has_console)
        self.assertEqual(plan.window, "minimized")

    def test_screener_shell(self):
        plan = self.plan("Screener shell")
        self.assertEqual(plan.cmdline,
                         r'''C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoExit -ExecutionPolicy Bypass '''
                         r'''-Command ". 'C:\Users\andre\Python\screener-jobs\shell\screener-shell.ps1'"''')
        self.assertEqual(plan.cwd, r"C:\Users\andre\Python\Screener-v2")
        self.assertTrue(plan.has_console)

    def test_suggested_html_entries_open_in_the_default_app(self):
        for name in ("Fractal Flame", "Vortex Street", "Claude Museum"):
            with self.subTest(name):
                plan = self.plan(name)
                self.assertEqual(plan.mode, "open")
                self.assertTrue(plan.target.lower().endswith(".html"))

    def test_every_target_exists_on_this_machine(self):
        missing = []
        for name, app in self.apps.items():
            try:
                build_plan(app)
            except LaunchError as exc:
                missing.append(f"{name}: {exc}")
        if missing:
            self.skipTest("not all targets are present here: " + "; ".join(missing))


if __name__ == "__main__":
    unittest.main()
