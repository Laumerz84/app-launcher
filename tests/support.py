"""Shared helpers for the tests. Import this first: it puts the launcher on sys.path and
makes sure every temporary file lands on F: (under .tmp), never on C:."""
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

logging.getLogger("applauncher").addHandler(logging.NullHandler())   # keep expected warnings out of the test output

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TMP_ROOT = ROOT / ".tmp"
TMP_ROOT.mkdir(exist_ok=True)
tempfile.tempdir = str(TMP_ROOT)          # tempfile / mkdtemp default to F:
os.environ["TEMP"] = os.environ["TMP"] = str(TMP_ROOT)
os.environ["LAUNCHER_LOG"] = str(TMP_ROOT / "test-launcher.log")   # never write into the real launcher.log

DUMMY = Path(__file__).resolve().parent / "dummy_app.py"
DUMMY_STOP = Path(__file__).resolve().parent / "dummy_stop.py"
IS_WINDOWS = sys.platform == "win32"


class TmpDirTestCase(unittest.TestCase):
    """Gives each test its own scratch folder under .tmp, removed afterwards."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="t-", dir=TMP_ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_json(self, name, data):
        path = self.tmp / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def write_text(self, name, text):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path


def wait_for(path, timeout=15.0):
    """Wait for a file to appear; returns its parsed JSON or None."""
    end = time.time() + timeout
    while time.time() < end:
        if Path(path).exists():
            try:
                return json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        time.sleep(0.05)
    return None


def wait_for_file(path, timeout=20.0):
    """Wait for a non-empty text file to appear and settle; True if it did."""
    end = time.time() + timeout
    while time.time() < end:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            time.sleep(0.4)  # let the writer finish its last line
            return True
        time.sleep(0.05)
    return False


def pythonw():
    from applauncher import commands
    return commands.python_exe(console=False)


def python_console():
    from applauncher import commands
    return commands.python_exe(console=True)


# --------------------------------------------------------------------------- real-process helpers
def pid_alive(pid):
    """True while the process exists and has not exited."""
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x00100000, False, pid)        # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(handle)


def wait_dead(pid, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


def kill_tree(pid):
    """Test cleanup only: end a dummy process and everything it started."""
    import subprocess
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)
