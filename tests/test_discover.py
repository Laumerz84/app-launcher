"""Finding projects for the terminal launcher, and turning typed text into a program."""
import os
import time
import unittest

import support  # noqa: F401  (sets up sys.path and temp dirs)
from applauncher import discover
from applauncher.config import App


class FindEntryTests(support.TmpDirTestCase):
    def project(self, name, files):
        folder = self.tmp / name
        for rel, text in files.items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return folder

    def test_newest_built_program_wins_over_scripts(self):
        folder = self.project("Tool", {"main.py": "if __name__ == '__main__': pass", "dist/Old.exe": "x"})
        time.sleep(0.02)
        (folder / "dist" / "New.exe").write_text("x")
        found = discover.find_entry(folder)
        self.assertEqual((found.kind, os.path.basename(found.app.command)), ("program", "New.exe"))
        self.assertTrue(found.app.needs_console)
        self.assertIn("new", found.aliases)

    def test_explicit_launcher_before_streamlit(self):
        folder = self.project("A", {"start_app.py": "", "app.py": "import streamlit as st"})
        self.assertTrue(discover.find_entry(folder).app.args[0].endswith("start_app.py"))

    def test_pyw_launcher_runs_without_console(self):
        found = discover.find_entry(self.project("A", {"launch.pyw": ""}))
        self.assertFalse(found.app.needs_console)
        self.assertTrue(found.app.command.lower().endswith("pythonw.exe"))

    def test_streamlit_app_prefers_app_py(self):
        found = discover.find_entry(self.project("A", {"app.py": "import streamlit as st",
                                                       "other.py": "from streamlit import x"}))
        self.assertEqual(found.app.args[:3], ("-m", "streamlit", "run"))
        self.assertTrue(found.app.args[3].endswith("app.py"))

    def test_main_script_named_like_folder(self):
        found = discover.find_entry(self.project("Email Prompter", {
            "email_prompter.py": "if __name__ == '__main__': pass",
            "helper.py": "if __name__ == '__main__': pass",
            "make_data.py": "if __name__ == '__main__': pass"}))
        self.assertTrue(found.app.args[0].endswith("email_prompter.py"))

    def test_single_script_without_guard(self):
        found = discover.find_entry(self.project("Game", {"game_loop.py": "print('hi')"}))
        self.assertEqual(found.kind, "script")

    def test_ambiguous_scripts_find_nothing(self):
        self.assertIsNone(discover.find_entry(self.project("Lib", {
            "a.py": "if __name__ == '__main__': pass", "b.py": "if __name__ == '__main__': pass"})))

    def test_tests_and_build_scripts_are_not_entries(self):
        self.assertIsNone(discover.find_entry(self.project("X", {"selftest.py": "", "build_exe.py": ""})))

    def test_notebook_skips_backups(self):
        found = discover.find_entry(self.project("NB", {"run.ipynb": "{}", "run_BACKUP.ipynb": "{}"}))
        self.assertEqual((found.kind, os.path.basename(found.app.command)), ("notebook", "run.ipynb"))

    def test_project_venv_is_used(self):
        folder = self.project("V", {"main.py": "if __name__ == '__main__': pass",
                                    ".venv/Scripts/python.exe": "x"})
        self.assertTrue(discover.find_entry(folder).app.command.startswith(str(folder)))

    def test_apps_json_entry_covers_its_folder(self):
        tool = self.project("Tool", {"main.py": "if __name__ == '__main__': pass"})
        self.project("Other", {"main.py": "if __name__ == '__main__': pass"})
        self.project("Empty", {"notes.txt": "x"})
        listed = [App(name="My Tool", command="python", args=(str(tool / "main.py"),))]
        programs, skipped = discover.all_programs(listed, self.tmp)
        self.assertEqual([p.app.name for p in programs], ["My Tool", "Other"])
        self.assertEqual(skipped, ["Empty"])


class TypedTextTests(unittest.TestCase):
    def setUp(self):
        def p(name, *aliases):
            return discover.Program(App(name=name, command="x.exe"), "program", "", aliases)
        self.programs = [p("RMD Script", "rmdaggregator"), p("OrionRecon", "orionreconciliation"),
                         p("Fee Displayer"), p("FeeCashRaise"), p("Space invaders")]

    def name(self, text):
        found, _ = discover.resolve(text, self.programs)
        return found.app.name if found else None

    def test_number_name_prefix_alias(self):
        self.assertEqual(self.name("2"), "OrionRecon")
        self.assertEqual(self.name("rmd script"), "RMD Script")
        self.assertEqual(self.name("RMD-Aggregator"), "RMD Script")
        self.assertEqual(self.name("orion"), "OrionRecon")
        self.assertEqual(self.name("invaders"), "Space invaders")
        self.assertIsNone(self.name("99"))

    def test_ambiguous_offers_candidates(self):
        found, candidates = discover.resolve("fee", self.programs)
        self.assertIsNone(found)
        self.assertEqual([c.app.name for c in candidates], ["Fee Displayer", "FeeCashRaise"])

    def test_misspelling_suggests(self):
        _, candidates = discover.resolve("orionrecno", self.programs)
        self.assertEqual([c.app.name for c in candidates], ["OrionRecon"])

    def test_extra_words_become_arguments(self):
        program, extra, _ = discover.split_command("rmd script --inputs C:\\x", self.programs)
        self.assertEqual((program.app.name, extra), ("RMD Script", ["--inputs", "C:\\x"]))
        app = discover.with_args(App(name="a", command="a.exe", args=("-q",)), extra)
        self.assertEqual(app.args, ("-q", "--inputs", "C:\\x"))


if __name__ == "__main__":
    unittest.main()
