"""Best-effort "is this app running?" detection.

Processes are listed in-process with ctypes (Toolhelp snapshot for names, then
``QueryFullProcessImageNameW`` and ``NtQueryInformationProcess`` for the exe
path and full command line) - no subprocess, so a refresh costs a few
milliseconds and never flashes a window.

Matching, per app:

1. If the entry has ``match`` texts, a process matches when *all* of them
   appear in its command line (or exe path). Any process type may match.
2. Otherwise the first script path found in the command / args (``.py``,
   ``.pyw``, ``.cmd``, ``.bat``, ``.ps1``, ...) is the marker: a process
   matches when it is the program itself or a normal script host (python,
   cmd, powershell, ...) and its command line contains that path.
3. Otherwise, for a plain program, the process's exe path must equal it.
4. Otherwise (documents / web pages, or a bare shell like cmd.exe with
   nothing to tell it apart) the answer is unknown: ``None``.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from .commands import LaunchError, build_plan
from .config import App

IS_WINDOWS = sys.platform == "win32"

# Programs that legitimately "host" a script: a process running one of these with
# the script path on its command line counts as the app running.
SCRIPT_HOSTS = frozenset({
    "python.exe", "pythonw.exe", "py.exe", "pyw.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "wt.exe", "node.exe", "wscript.exe", "cscript.exe", "conhost.exe", "openconsole.exe",
})

_SCRIPT_RE = re.compile(
    r"[A-Za-z]:[\\/][^\"'<>|?*\r\n]*?\.(?:pyw?|cmd|bat|ps1|vbs|jse?|wsf|hta)(?=$|[\s\"'])", re.IGNORECASE
)


@dataclass(frozen=True)
class Proc:
    pid: int
    name: str      # image file name, e.g. "cmd.exe"
    exe: str = ""  # full path when readable
    cmdline: str = ""
    ppid: int = 0      # parent process id (0 = unknown)
    started: int = 0   # creation time as a Windows FILETIME (0 = unknown); tells a reused pid from the original


def norm(text: str) -> str:
    """Lower-case with forward slashes turned to backslashes, for comparing paths."""
    return text.lower().replace("/", "\\")


# --------------------------------------------------------------------------- matching
@dataclass(frozen=True)
class Matcher:
    tokens: tuple[str, ...] = ()        # normalised texts that must all be in the command line
    exe_path: str = ""                  # normalised exe path to compare (mode 3)
    hosts: frozenset[str] = frozenset() # allowed process names for token matching (mode 2)
    explicit: bool = False              # tokens came from the entry's "match"


def matcher_for(app: App) -> Optional[Matcher]:
    """How to recognise *app* among running processes; None if it cannot be told."""
    if app.match:
        return Matcher(tokens=tuple(norm(t) for t in app.match), explicit=True)
    try:
        plan = build_plan(app, check=False)
    except LaunchError:
        return None
    if plan.is_open:
        return None

    exe_name = os.path.basename(plan.exe).lower()
    hosts = SCRIPT_HOSTS | {exe_name}

    # The script this entry runs: the command itself, else the first script path in the args.
    candidates: list[str] = []
    command = os.path.expandvars(os.path.expanduser(app.command.strip().strip('"')))
    if plan.mode in ("cmd", "powershell", "python"):
        candidates.append(command)
    arg_text = app.args if isinstance(app.args, str) else " ".join(app.args)
    candidates.extend(_SCRIPT_RE.findall(os.path.expandvars(arg_text)))
    for candidate in candidates:
        if os.path.isabs(candidate):
            return Matcher(tokens=(norm(candidate),), hosts=frozenset(hosts))

    if exe_name in SCRIPT_HOSTS:
        return None  # a bare shell with no script to identify it
    if os.path.isabs(plan.exe):
        return Matcher(exe_path=norm(plan.exe))
    return Matcher(exe_path=norm(exe_name))


def _matches(matcher: Matcher, name: str, cmd: str, exe: str) -> bool:
    """*name* lower-case image name; *cmd* and *exe* already passed through norm()."""
    if matcher.explicit:
        haystack = cmd + " " + exe
        return all(token in haystack for token in matcher.tokens)
    if matcher.tokens:
        return name in matcher.hosts and all(token in cmd for token in matcher.tokens)
    if matcher.exe_path:
        if os.path.isabs(matcher.exe_path):
            return exe == matcher.exe_path
        return name == matcher.exe_path
    return False


def matches(matcher: Matcher, proc: Proc) -> bool:
    return _matches(matcher, proc.name.lower(), norm(proc.cmdline), norm(proc.exe))


def specificity(matcher: Matcher) -> tuple[int, int]:
    """How narrowly a matcher points at a process: more texts, then longer texts, is more specific."""
    texts = matcher.tokens or ((matcher.exe_path,) if matcher.exe_path else ())
    return len(texts), sum(len(t) for t in texts)


@dataclass
class Claims:
    """Which running processes each entry lays claim to.

    When two entries both match a process (say two entries run the same script, one with an extra
    ``--mini``), the more specific matcher wins it. A process two entries match *equally* well is
    ``shared``: both entries count as running, but nothing may be stopped through it.
    """
    owned: list[frozenset[int]]        # pids only this entry claims
    shared: list[frozenset[int]]       # pids this entry ties on with another entry
    rivals: list[frozenset[int]]       # indexes of the other entries it ties with
    detectable: list[bool]             # False: matcher is None (documents, bare shells)

    def running(self, index: int) -> Optional[bool]:
        if not self.detectable[index]:
            return None
        return bool(self.owned[index] or self.shared[index])


def compute_claims(apps: Sequence[App], procs: Iterable[Proc], ignore_pid: Optional[int] = None) -> Claims:
    matchers = [matcher_for(app) for app in apps]
    scores = [specificity(m) if m else None for m in matchers]
    owned: list[set[int]] = [set() for _ in apps]
    shared: list[set[int]] = [set() for _ in apps]
    rivals: list[set[int]] = [set() for _ in apps]
    for proc in procs:
        if proc.pid == ignore_pid:
            continue
        name, cmd, exe = proc.name.lower(), norm(proc.cmdline), norm(proc.exe)
        hits = [i for i, m in enumerate(matchers) if m is not None and _matches(m, name, cmd, exe)]
        if not hits:
            continue
        best = max(scores[i] for i in hits)
        winners = [i for i in hits if scores[i] == best]
        for i in winners:
            if len(winners) == 1:
                owned[i].add(proc.pid)
            else:
                shared[i].add(proc.pid)
                rivals[i].update(j for j in winners if j != i)
    return Claims([frozenset(s) for s in owned], [frozenset(s) for s in shared],
                  [frozenset(s) for s in rivals], [m is not None for m in matchers])


def running_states(apps: Sequence[App], procs: Optional[Iterable[Proc]],
                   ignore_pid: Optional[int] = None) -> list[Optional[bool]]:
    """One entry per app: True running, False not running, None unknown."""
    if procs is None:
        return [None] * len(apps)
    claims = compute_claims(apps, procs, ignore_pid)
    return [claims.running(i) for i in range(len(apps))]


# --------------------------------------------------------------------------- process listing
_TH32CS_SNAPPROCESS = 0x2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_COMMAND_LINE_INFORMATION = 60
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("MaximumLength", ctypes.c_ushort), ("Buffer", ctypes.c_void_p)]


_api = None


def _load_api():
    global _api
    if _api is not None:
        return _api
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    ntdll.NtQueryInformationProcess.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long
    _api = (kernel32, ntdll)
    return _api


def _image_path(kernel32, handle) -> str:
    size = wintypes.DWORD(1024)
    buf = ctypes.create_unicode_buffer(size.value)
    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return buf.value
    return ""


def creation_time(kernel32, handle) -> int:
    """Process creation time as a FILETIME integer (0 if unreadable)."""
    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                    ctypes.byref(kernel), ctypes.byref(user)):
        return 0
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def _command_line(ntdll, handle) -> str:
    length = 4096
    for _ in range(3):
        buf = ctypes.create_string_buffer(length)
        needed = wintypes.ULONG(0)
        status = ntdll.NtQueryInformationProcess(
            handle, _PROCESS_COMMAND_LINE_INFORMATION, buf, length, ctypes.byref(needed))
        if status == 0:
            us = _UNICODE_STRING.from_buffer(buf)
            if not us.Buffer or not us.Length:
                return ""
            return ctypes.wstring_at(us.Buffer, us.Length // 2)
        if status & 0xFFFFFFFF in (0xC0000004, 0xC0000023) and needed.value > length:  # length mismatch / too small
            length = needed.value + 16
            continue
        return ""
    return ""


def list_processes() -> Optional[list[Proc]]:
    """Every process this user can inspect, or None if listing failed altogether."""
    if not IS_WINDOWS:
        return None
    try:
        kernel32, ntdll = _load_api()
        snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == _INVALID_HANDLE:
            return None
        procs: list[Proc] = []
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                pid = int(entry.th32ProcessID)
                exe = cmdline = ""
                started = 0
                if pid:
                    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                    if handle:
                        try:
                            exe = _image_path(kernel32, handle)
                            cmdline = _command_line(ntdll, handle)
                            started = creation_time(kernel32, handle)
                        finally:
                            kernel32.CloseHandle(handle)
                procs.append(Proc(pid=pid, name=entry.szExeFile, exe=exe, cmdline=cmdline,
                                  ppid=int(entry.th32ParentProcessID), started=started))
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return procs
    except Exception:
        return None
