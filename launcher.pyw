"""App Launcher - entry point.

Run with pythonw (the Start menu shortcut does) so no console window appears:

    pythonw launcher.pyw

Options for testing:  --smoke-test [SECONDS]   --apps PATH   see  launcher.pyw --help
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from applauncher.ui import main
except Exception as exc:  # pythonw has no console, so say it in a box
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, f"App Launcher could not start:\n\n{type(exc).__name__}: {exc}",
                                     "App Launcher", 0x10)
    raise SystemExit(1)

if __name__ == "__main__":
    sys.exit(main())
