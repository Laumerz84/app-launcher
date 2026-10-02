"""The Start menu shortcut installer (run against a scratch folder, never the real Start menu) and the
AppUserModelID stamped into it."""
import subprocess
import unittest

import support
from applauncher import aumid, winutil
from applauncher.commands import CREATE_NO_WINDOW

SCRIPT = support.ROOT / "install-shortcut.ps1"


def powershell(*args, timeout=90):
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
                          capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
                          cwd=str(support.ROOT))


def read_shortcut(path):
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
              "$s.TargetPath; $s.Arguments; $s.WorkingDirectory; $s.IconLocation; $s.WindowStyle" % path)
    out = powershell("-Command", script).stdout.strip().splitlines()
    return dict(zip(("target", "args", "cwd", "icon", "style"), out))


class InstallerTests(support.TmpDirTestCase):
    def test_creates_exactly_one_shortcut_with_the_right_target_args_folder_icon_and_id(self):
        done = powershell("-File", str(SCRIPT), "-Folder", str(self.tmp))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        shortcuts = list(self.tmp.glob("*.lnk"))
        self.assertEqual([s.name for s in shortcuts], ["App Launcher.lnk"])
        info = read_shortcut(shortcuts[0])
        self.assertTrue(info["target"].lower().endswith("\\pythonw.exe"), info)
        self.assertNotIn("windowsapps", info["target"].lower())      # the real interpreter, not the store alias
        self.assertEqual(info["args"], '"%s"' % (support.ROOT / "launcher.pyw"))
        self.assertEqual(info["cwd"], str(support.ROOT))
        self.assertEqual(info["icon"], "%s,0" % (support.ROOT / "launcher.ico"))
        self.assertEqual(aumid.get_shortcut_aumid(str(shortcuts[0])), winutil.APP_USER_MODEL_ID)

    def test_running_it_again_refreshes_rather_than_duplicating_and_remove_deletes(self):
        for _ in range(2):
            self.assertEqual(powershell("-File", str(SCRIPT), "-Folder", str(self.tmp)).returncode, 0)
        self.assertEqual(len(list(self.tmp.glob("*.lnk"))), 1)
        self.assertEqual(powershell("-File", str(SCRIPT), "-Folder", str(self.tmp), "-Remove").returncode, 0)
        self.assertEqual(list(self.tmp.glob("*.lnk")), [])

    def test_the_shortcut_command_actually_starts_the_launcher(self):
        """Run exactly what the shortcut would (pythonw launcher.pyw) in smoke-test mode."""
        powershell("-File", str(SCRIPT), "-Folder", str(self.tmp))
        info = read_shortcut(self.tmp / "App Launcher.lnk")
        done = subprocess.run(f'"{info["target"]}" {info["args"]} --smoke-test 1 --invisible --apps "{self.write_json("a.json", {"apps": []})}"',
                              cwd=info["cwd"], timeout=60, creationflags=CREATE_NO_WINDOW)
        self.assertEqual(done.returncode, 0)


class AumidTests(support.TmpDirTestCase):
    def test_round_trip_on_a_scratch_shortcut(self):
        lnk = self.tmp / "probe.lnk"
        powershell("-Command", "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
                               "$s.TargetPath='C:\\Windows\\System32\\notepad.exe'; $s.Save()" % lnk)
        self.assertTrue(lnk.exists())
        self.assertEqual(aumid.get_shortcut_aumid(str(lnk)), "")
        aumid.set_shortcut_aumid(str(lnk), "Some.Test.Id")
        self.assertEqual(aumid.get_shortcut_aumid(str(lnk)), "Some.Test.Id")
        aumid.set_shortcut_aumid(str(lnk))
        self.assertEqual(aumid.get_shortcut_aumid(str(lnk)), winutil.APP_USER_MODEL_ID)
        self.assertEqual(read_shortcut(lnk)["target"], "C:\\Windows\\System32\\notepad.exe")  # still intact


if __name__ == "__main__":
    unittest.main()
