"""Windows helpers: single instance, theme, harmless calls."""
import subprocess
import sys
import unittest

import support
from applauncher import winutil
from applauncher.commands import CREATE_NO_WINDOW


class SingleInstanceTests(unittest.TestCase):
    def run_py(self, code):
        return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30,
                              cwd=str(support.ROOT), creationflags=CREATE_NO_WINDOW)

    def test_second_claim_on_the_same_name_is_refused(self):
        name = f"Local\\ClodCode.AppLauncher.Test.{id(self)}"
        code = (f"from applauncher import winutil; import sys; "
                f"a = winutil.acquire_single_instance({name!r}); b = winutil.acquire_single_instance({name!r}); "
                f"print(a, b)")
        self.assertEqual(self.run_py(code).stdout.split(), ["True", "False"])

    def test_two_separate_processes_cannot_both_claim_it(self):
        name = f"Local\\ClodCode.AppLauncher.Test2.{id(self)}"
        holder = subprocess.Popen(
            [sys.executable, "-c",
             f"from applauncher import winutil; import time; "
             f"print(winutil.acquire_single_instance({name!r}), flush=True); time.sleep(6)"],
            stdout=subprocess.PIPE, text=True, cwd=str(support.ROOT), creationflags=CREATE_NO_WINDOW)
        self.addCleanup(lambda: (holder.kill(), holder.wait(10), holder.stdout.close()))
        self.assertEqual(holder.stdout.readline().strip(), "True")
        second = self.run_py(f"from applauncher import winutil; print(winutil.acquire_single_instance({name!r}))")
        self.assertEqual(second.stdout.strip(), "False")

    def test_focusing_a_window_that_does_not_exist_is_harmless(self):
        self.assertFalse(winutil.focus_existing_window("No such window title 7f3a9"))


class MiscTests(unittest.TestCase):
    def test_theme_query_returns_a_bool(self):
        self.assertIsInstance(winutil.system_uses_dark_theme(), bool)

    def test_best_effort_calls_never_raise(self):
        winutil.enable_dpi_awareness()
        winutil.set_app_user_model_id("ClodCode.AppLauncher.Test")
        winutil.set_dark_title_bar(0, True)
        winutil.set_window_icons(0, "nope.ico")
        self.assertIsNone(winutil.capture_window_bgra(0))


if __name__ == "__main__":
    unittest.main()
