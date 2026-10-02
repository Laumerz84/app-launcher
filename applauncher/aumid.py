"""Read / write the AppUserModelID stored inside a .lnk shortcut.

Why: Windows groups taskbar buttons by AppUserModelID. The launcher sets an
explicit id on its own process (see winutil.APP_USER_MODEL_ID); if the pinned
shortcut carries the same id, the running window and the pinned icon become one
taskbar button (instead of a pinned icon plus a second "python" button).

Plain ctypes against the shell's property store; no packages needed.

    python -m applauncher.aumid get "<file.lnk>"
    python -m applauncher.aumid set "<file.lnk>" [id]
"""
from __future__ import annotations

import ctypes
import sys
import uuid
from ctypes import wintypes

from .winutil import APP_USER_MODEL_ID

_GPS_READWRITE = 0x2
_VT_LPWSTR = 31
_IID_IPropertyStore = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
_PKEY_AppUserModel_ID = ("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3", 5)


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "_GUID":
        g = cls()
        ctypes.memmove(ctypes.byref(g), uuid.UUID(text).bytes_le, 16)
        return g


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    # vt + three reserved words, then a 16-byte union (on 64-bit Windows).
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("value", ctypes.c_void_p), ("pad", ctypes.c_void_p)]


def _store(path: str):
    ole32 = ctypes.WinDLL("ole32")
    shell32 = ctypes.WinDLL("shell32")
    ole32.CoInitializeEx(None, 2)  # apartment threaded; harmless if already initialised
    shell32.SHGetPropertyStoreFromParsingName.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetPropertyStoreFromParsingName.restype = ctypes.HRESULT
    pps = ctypes.c_void_p()
    shell32.SHGetPropertyStoreFromParsingName(
        path, None, _GPS_READWRITE, ctypes.byref(_GUID.parse(_IID_IPropertyStore)), ctypes.byref(pps))
    vtable = ctypes.cast(ctypes.cast(pps, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))

    def method(index, *argtypes):
        return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])

    return pps, method, ole32


def _key() -> _PROPERTYKEY:
    key = _PROPERTYKEY()
    key.fmtid = _GUID.parse(_PKEY_AppUserModel_ID[0])
    key.pid = _PKEY_AppUserModel_ID[1]
    return key


def set_shortcut_aumid(lnk_path: str, aumid: str = APP_USER_MODEL_ID) -> None:
    pps, method, ole32 = _store(str(lnk_path))
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
        ctypes.cast(ctypes.cast(pps, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))[2])
    try:
        ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
        ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
        buf = ctypes.create_unicode_buffer(aumid)
        mem = ole32.CoTaskMemAlloc(ctypes.sizeof(buf))
        ctypes.memmove(mem, buf, ctypes.sizeof(buf))
        pv = _PROPVARIANT()
        pv.vt = _VT_LPWSTR
        pv.value = mem
        key = _key()
        try:
            method(6, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(
                pps, ctypes.byref(key), ctypes.byref(pv))   # SetValue
            method(7)(pps)                                   # Commit
        finally:
            ole32.PropVariantClear(ctypes.byref(pv))
    finally:
        release(pps)


def get_shortcut_aumid(lnk_path: str) -> str:
    """The AppUserModelID stored in the shortcut, or '' if none."""
    pps, method, ole32 = _store(str(lnk_path))
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(
        ctypes.cast(ctypes.cast(pps, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))[2])
    try:
        pv = _PROPVARIANT()
        key = _key()
        method(5, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))(
            pps, ctypes.byref(key), ctypes.byref(pv))       # GetValue
        try:
            if pv.vt == _VT_LPWSTR and pv.value:
                return ctypes.wstring_at(pv.value)
            return ""
        finally:
            ole32.PropVariantClear(ctypes.byref(pv))
    finally:
        release(pps)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "set":
        set_shortcut_aumid(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else APP_USER_MODEL_ID)
        print("AppUserModelID set:", get_shortcut_aumid(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "get":
        print(get_shortcut_aumid(sys.argv[2]))
    else:
        print(__doc__)
        sys.exit(2)
