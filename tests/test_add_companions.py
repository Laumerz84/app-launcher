"""add_companions.py: puts UtilityBelt and UI Report Tool into apps.json with this PC's paths."""
import contextlib
import io
import json

import support  # noqa: F401  (sys.path + temp folders on F:)

import add_companions
from applauncher import config
from applauncher.commands import build_plan


class AddCompanionsTest(support.TmpDirTestCase):
    def setUp(self):
        super().setUp()
        self.launcher = self.tmp / "launcher"
        self.launcher.mkdir()
        self.belt = self.tmp / "utilitybelt"
        self.belt.mkdir()
        (self.belt / "belt.cmd").write_text("@echo off\n", encoding="utf-8")
        self.report = self.tmp / "ui-report-tool"
        (self.report / ".venv" / "Scripts").mkdir(parents=True)
        (self.report / "launch.pyw").write_text("", encoding="utf-8")
        (self.report / ".venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
        self.apps_json = self.launcher / "apps.json"
        self.apps_json.write_text(json.dumps({
            "_help": {"name": "Text on the button."},
            "apps": [
                {"name": "Notepad", "command": "notepad"},
                {"name": "UI Report Tool", "command": r"G:\someone else\pythonw.exe"},
            ],
        }), encoding="utf-8")

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = add_companions.main([*args, "--apps-json", str(self.apps_json)])
        return code, out.getvalue()

    def apps(self):
        return {a["name"]: a for a in json.loads(self.apps_json.read_text(encoding="utf-8"))["apps"]}

    def test_finds_companions_cloned_next_to_the_launcher(self):
        self.assertEqual(add_companions.find_sibling(add_companions.UTILITYBELT_NAMES, "belt.cmd", self.launcher),
                         self.belt)
        self.assertEqual(add_companions.find_sibling(add_companions.UI_REPORT_NAMES, "launch.pyw", self.launcher),
                         self.report)
        (self.tmp / "empty").mkdir()
        self.assertIsNone(add_companions.find_sibling(("empty",), "belt.cmd", self.launcher))

    def test_adds_both_with_this_pcs_paths_and_keeps_everything_else(self):
        code, out = self.run_main("--utilitybelt", str(self.belt), "--ui-report-tool", str(self.report))
        self.assertEqual(code, 0)
        self.assertIn("added UtilityBelt", out)
        self.assertIn("updated UI Report Tool", out)  # someone else's paths replaced
        data = json.loads(self.apps_json.read_text(encoding="utf-8"))
        self.assertEqual(data["_help"], {"name": "Text on the button."})
        self.assertEqual([a["name"] for a in data["apps"]], ["Notepad", "UI Report Tool", "UtilityBelt"])
        apps = self.apps()
        report = apps["UI Report Tool"]
        self.assertEqual(report["command"], str(self.report / ".venv" / "Scripts" / "pythonw.exe"))
        self.assertEqual(report["args"], [str(self.report / "launch.pyw")])
        self.assertEqual(report["stop_args"], [str(self.report / "launch.pyw"), "--quit"])
        self.assertEqual(report["match"], [str(self.report / "launch.pyw")])
        belt = apps["UtilityBelt"]
        self.assertEqual(belt["args"], ["/c", str(self.belt / "belt.cmd")])
        self.assertEqual(belt["match"], [str(self.belt / "belt.py")])
        self.assertTrue(belt["needs_console"])

    def test_the_launcher_accepts_and_can_start_the_added_entries(self):
        self.run_main("--utilitybelt", str(self.belt), "--ui-report-tool", str(self.report))
        loaded = {a.name: a for a in config.load_apps(self.apps_json).apps}
        for name in ("UtilityBelt", "UI Report Tool"):
            plan = build_plan(loaded[name], check=True)  # raises if a file it needs is missing
            self.assertTrue(plan.cmdline)

    def test_running_it_twice_changes_nothing_more(self):
        self.run_main("--utilitybelt", str(self.belt), "--ui-report-tool", str(self.report))
        first = self.apps_json.read_text(encoding="utf-8")
        self.run_main("--utilitybelt", str(self.belt), "--ui-report-tool", str(self.report))
        self.assertEqual(self.apps_json.read_text(encoding="utf-8"), first)

    def test_a_ui_report_tool_without_setup_is_added_with_a_reminder(self):
        (self.report / ".venv" / "Scripts" / "pythonw.exe").unlink()
        code, out = self.run_main("--ui-report-tool", str(self.report), "--utilitybelt", str(self.tmp / "nope"))
        self.assertEqual(code, 0)
        self.assertIn("setup.bat", out)
        self.assertIn("UtilityBelt not found", out)
        self.assertNotIn("UtilityBelt", self.apps())

    def test_nothing_found_leaves_apps_json_untouched(self):
        before = self.apps_json.read_text(encoding="utf-8")
        code, out = self.run_main("--utilitybelt", str(self.tmp / "a"), "--ui-report-tool", str(self.tmp / "b"))
        self.assertEqual(code, 1)
        self.assertIn("not found", out)
        self.assertEqual(self.apps_json.read_text(encoding="utf-8"), before)
