"""Small Windows helpers, ctypes only (no third-party packages).

Everything here is best-effort: each function swallows its own failure and
returns a harmless default, because none of it is essential to launching apps.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# One id shared by the running process and the Start menu shortcut, so the
# taskbar groups the window with the pinned shortcut instead of showing a
# separate "pythonw" button.
APP_USER_MODEL_ID = "ClodCode.AppLauncher"
WINDOW_TITLE = "App Launcher"


def enable_dpi_awareness() -> None:
    """Ask for system DPI awareness so text and icons are sharp on scaled displays."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_app_user_model_id(aumid: str = APP_USER_MODEL_ID) -> None:
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(aumid))
    except Exception:
        pass


def system_uses_dark_theme() -> bool:
    """True when Windows "app mode" is dark. Read-only registry lookup."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except Exception:
        return False


# --------------------------------------------------------------------------- single instance
_ERROR_ALREADY_EXISTS = 183
_instance_handle = None  # keep the mutex alive for the life of the process


def acquire_single_instance(name: str = "Local\\ClodCode.AppLauncher.Instance") -> bool:
    """True if we are the only launcher window; False if one is already open."""
    global _instance_handle
    if not IS_WINDOWS:
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle(handle)
            return False
        _instance_handle = handle
        return True
    except Exception:
        return True


def focus_existing_window(title: str = WINDOW_TITLE) -> bool:
    """Bring an already-open launcher window to the front."""
    if not IS_WINDOWS:
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- window chrome
def toplevel_hwnd(tk_root) -> int:
    """The real top-level window handle behind a Tk root."""
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.restype = wintypes.HWND
        user32.GetParent.argtypes = [wintypes.HWND]
        child = tk_root.winfo_id()
        return int(user32.GetParent(child) or child)
    except Exception:
        return 0


def set_dark_title_bar(hwnd: int, dark: bool) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    try:
        dwm = ctypes.WinDLL("dwmapi")
        value = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD]
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20 on current builds, 19 on early ones)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def set_window_icons(hwnd: int, ico_path: str) -> None:
    """Load the .ico at the exact small/large sizes so the title bar and taskbar are crisp."""
    if not IS_WINDOWS or not hwnd:
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x80, 0, 1
        for which, cx_metric, cy_metric in ((ICON_SMALL, 49, 50), (ICON_BIG, 11, 12)):
            cx, cy = user32.GetSystemMetrics(cx_metric), user32.GetSystemMetrics(cy_metric)
            hicon = user32.LoadImageW(None, ico_path, IMAGE_ICON, cx, cy, LR_LOADFROMFILE)
            if hicon:
                user32.SendMessageW(hwnd, WM_SETICON, which, hicon)
    except Exception:
        pass


def show_error_box(message: str, title: str = WINDOW_TITLE) -> None:
    """Last-resort visible error (pythonw has no console)."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        pass


def capture_window_bgra(hwnd: int):
    """(width, height, top-down BGRA bytes) of a window's own pixels, or None.

    Used only by the smoke test's --snapshot option to check the layout; it asks the
    window to paint itself (PrintWindow), so it works even when the window is covered.
    """
    if not IS_WINDOWS or not hwnd:
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowDC.restype = wintypes.HDC
        user32.GetWindowDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        hdc = user32.GetWindowDC(hwnd)
        mem = gdi32.CreateCompatibleDC(hdc)
        bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
        old = gdi32.SelectObject(mem, bitmap)
        try:
            user32.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT

            class _Header(ctypes.Structure):
                _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                            ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                            ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                            ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
                            ("pad", wintypes.DWORD)]

            info = _Header()
            info.biSize = 40
            info.biWidth, info.biHeight, info.biPlanes, info.biBitCount = width, -height, 1, 32
            buf = ctypes.create_string_buffer(width * height * 4)
            gdi32.SelectObject(mem, old)  # a bitmap must not be selected while reading its bits
            gdi32.GetDIBits(mem, bitmap, 0, height, buf, ctypes.byref(info), 0)
            return width, height, buf.raw
        finally:
            gdi32.DeleteObject(bitmap)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(hwnd, hdc)
    except Exception:
        return None
