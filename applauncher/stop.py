"""Stopping a running app from the launcher.

Two ways, chosen per entry:

``stop_command`` set
    The launcher runs that command (no console) and then watches the same
    running-detection that lights the green dot, for up to ~10 seconds. If the
    app is still there at the end (say it is asking about unsaved work and the
    user chose Cancel) nothing is forced: the launcher only says so.

no ``stop_command``
    1. a polite close: WM_CLOSE to the visible top-level windows of the
       processes the entry's detection matches, and of their descendants
       (a console app's window belongs to its console host, a child);
    2. if any window was asked to close, wait up to ~2 seconds;
    3. then terminate whatever is left of the matched processes and the
       processes they started (the parent/child tree from the process snapshot).

What may be touched is deliberately narrow: only processes this entry's own
detection claims (see ``procs.compute_claims``), plus their descendants, minus
anything another entry claims, the launcher itself and its ancestors, and a few
system processes. Two entries that both match a process equally well (a tie)
leave it alone. Nothing is ever touched by name alone.
"""
from __future__ import annotations

import ctypes
import logging
import os
import time
from collections import defaultdict, deque
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from . import commands, procs
from .commands import LaunchError
from .config import App
from .procs import Proc

log = logging.getLogger("applauncher.stop")

STOP_COMMAND_WAIT = 10.0   # seconds to wait for an app to vanish after its stop_command
CLOSE_WAIT = 2.0           # seconds to give a polite close before terminating
KILL_WAIT = 3.0            # seconds to let terminated processes disappear from the list
POLL = 0.2
MAX_MATCHED = 12           # an entry whose detection matches more processes than this is refused as too broad

# Never terminated, even if they turn up as somebody's descendant.
PROTECTED_NAMES = frozenset({
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "svchost.exe", "dwm.exe", "explorer.exe", "sihost.exe", "fontdrvhost.exe", "taskhostw.exe",
    "windowsterminal.exe",
})

_ERROR_INVALID_PARAMETER = 87  # OpenProcess on a pid that no longer exists


@dataclass
class StopResult:
    ok: bool                     # the app is gone (or was never running)
    message: str                 # one short status line for the window
    level: str = "ok"            # "ok" | "warn" (still open, nothing wrong) | "error"
    method: str = ""             # "stop_command" | "close" | ""
    killed: list[int] = field(default_factory=list)
    windows_closed: int = 0


# --------------------------------------------------------------------------- choosing what may be touched
@dataclass
class Targets:
    owned: list[Proc] = field(default_factory=list)        # matched by this entry alone
    descendants: list[Proc] = field(default_factory=list)  # started (directly or not) by those
    rivals: list[str] = field(default_factory=list)        # other entries that tie on a process this one matches
    depth: dict[int, int] = field(default_factory=dict)    # pid -> distance from a matched process

    @property
    def everything(self) -> list[Proc]:
        return self.owned + self.descendants

    def leaf_first(self, extra: Iterable[Proc] = ()) -> list[Proc]:
        seen: dict[int, Proc] = {p.pid: p for p in self.everything}
        for p in extra:
            seen.setdefault(p.pid, p)
        return sorted(seen.values(), key=lambda p: (-self.depth.get(p.pid, 0), p.pid))


def protected_pids(process_list: Sequence[Proc], own_pid: Optional[int]) -> set[int]:
    """The launcher itself, everything above it, and the system essentials."""
    by_pid = {p.pid: p for p in process_list}
    safe = {p.pid for p in process_list if p.name.lower() in PROTECTED_NAMES}
    pid, hops = own_pid, 0
    while pid and hops < 64:
        safe.add(pid)
        proc = by_pid.get(pid)
        pid, hops = (proc.ppid if proc else 0), hops + 1
    return safe


def _children_index(process_list: Sequence[Proc]) -> dict[int, list[Proc]]:
    children: dict[int, list[Proc]] = defaultdict(list)
    for p in process_list:
        if p.ppid:
            children[p.ppid].append(p)
    return children


def _is_child_of(child: Proc, parent: Proc) -> bool:
    """ppid alone can be stale (pids get reused); a real child cannot be older than its parent."""
    return not (child.started and parent.started and child.started < parent.started)


def find_targets(index: int, apps: Sequence[App], process_list: Sequence[Proc],
                 own_pid: Optional[int] = None) -> Targets:
    """What stopping ``apps[index]`` may terminate, given a process snapshot."""
    claims = procs.compute_claims(apps, process_list, ignore_pid=own_pid)
    by_pid = {p.pid: p for p in process_list}
    safe = protected_pids(process_list, own_pid)
    elsewhere: set[int] = set()
    for j in range(len(apps)):
        if j != index:
            elsewhere |= claims.owned[j] | claims.shared[j]
    elsewhere |= claims.shared[index]          # tied with someone else: hands off

    targets = Targets()
    targets.rivals = sorted({apps[j].name for j in claims.rivals[index]})
    targets.owned = [by_pid[pid] for pid in sorted(claims.owned[index]) if pid not in safe and pid in by_pid]
    children = _children_index(process_list)
    seen = {p.pid for p in targets.owned}
    for p in targets.owned:
        targets.depth[p.pid] = 0
    queue = deque(targets.owned)
    while queue:
        parent = queue.popleft()
        for child in children.get(parent.pid, ()):
            if (child.pid in seen or child.pid == own_pid or child.pid in safe or child.pid in elsewhere
                    or not _is_child_of(child, parent)):
                continue
            seen.add(child.pid)
            targets.depth[child.pid] = targets.depth[parent.pid] + 1
            targets.descendants.append(child)
            queue.append(child)
    return targets


# --------------------------------------------------------------------------- Windows actions
_WM_CLOSE = 0x0010
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def close_windows(pids: Iterable[int]) -> int:
    """Post WM_CLOSE to every visible top-level window owned by one of *pids*. Returns how many."""
    wanted = set(pids)
    if not wanted:
        return 0
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    count = [0]

    def visit(hwnd, _lparam):
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in wanted and user32.IsWindowVisible(hwnd) and user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0):
            count[0] += 1
        return True

    callback = enum_proc(visit)       # keep a reference for the duration of the call
    user32.EnumWindows(callback, 0)
    return count[0]


def terminate_process(pid: int, started: int = 0, exit_code: int = 0) -> int:
    """End one process. 0 means it is gone (or already was); otherwise a Win32 error code.

    When *started* (the creation time from the snapshot) is given, the process is only ended if it
    is still that same process - never a newer one that reused the pid.

    The exit code is 0 on purpose: a terminal host such as Windows Terminal then treats it as a
    normal exit and closes the tab, instead of leaving "process exited with code 1" on screen.
    """
    kernel32, _ = procs._load_api()
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        code = ctypes.get_last_error() or ctypes.GetLastError()
        return 0 if code == _ERROR_INVALID_PARAMETER else (code or 5)
    try:
        if started and procs.creation_time(kernel32, handle) != started:
            return 0  # the original process is gone; this is somebody else
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        if kernel32.TerminateProcess(handle, exit_code):
            return 0
        return ctypes.get_last_error() or ctypes.GetLastError() or 5
    finally:
        kernel32.CloseHandle(handle)


# --------------------------------------------------------------------------- the stop itself
def _ok(app: App, method: str, **kw) -> StopResult:
    return StopResult(True, f"Stopped {app.name}", "ok", method, **kw)


def _fail(app: App, reason: str, method: str = "", **kw) -> StopResult:
    return StopResult(False, f"Could not stop {app.name}: {reason}", "error", method, **kw)


def stop_app(app: App, apps: Sequence[App], *,
             scan: Callable[[], Optional[list[Proc]]] = procs.list_processes,
             own_pid: Optional[int] = None,
             close: Callable[[Iterable[int]], int] = close_windows,
             terminate: Callable[..., int] = terminate_process,
             start: Callable = commands.start_process,
             sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic,
             stop_wait: float = STOP_COMMAND_WAIT, close_wait: float = CLOSE_WAIT,
             kill_wait: float = KILL_WAIT, poll: float = POLL) -> StopResult:
    """Stop *app* (an entry of *apps*). Blocks for up to a few seconds; run it off the UI thread."""
    if own_pid is None:
        own_pid = os.getpid()
    pool = list(apps)
    if app in pool:
        index = pool.index(app)
    else:
        pool.append(app)
        index = len(pool) - 1

    def look() -> tuple[Optional[list[Proc]], Optional[bool]]:
        found = scan()
        if found is None:
            return None, None
        return found, procs.compute_claims(pool, found, own_pid).running(index)

    found, running = look()
    if found is None:
        return _fail(app, "could not read the list of running programs")
    if running is None:
        return _fail(app, "it cannot be detected as running, so there is nothing to stop")
    if not running:
        return StopResult(True, f"{app.name} is not running", "ok")

    if app.stop_command:
        return _stop_with_command(app, index, pool, look, start, sleep, clock, stop_wait, poll)
    return _stop_by_closing(app, index, pool, found, look, own_pid, close, terminate, sleep, clock,
                            close_wait, kill_wait, poll)


def _wait_until(done: Callable[[], bool], timeout: float, sleep, clock, poll: float) -> bool:
    end = clock() + timeout
    while True:
        if done():
            return True
        if clock() >= end:
            return False
        sleep(poll)


def _stop_with_command(app, index, pool, look, start, sleep, clock, stop_wait, poll) -> StopResult:
    method = "stop_command"
    try:
        plan = commands.build_stop_plan(app)
        proc = start(plan)
    except LaunchError as exc:
        log.warning("stop command for %s did not start: %s", app.name, exc)
        return _fail(app, str(exc), method)
    log.info("stop %s: ran stop_command %s", app.name, plan.cmdline)
    failed_code: Optional[int] = None

    def gone() -> bool:
        nonlocal failed_code
        code = proc.poll()
        if code not in (None, 0):
            failed_code = code
        found, running = look()
        return found is not None and running is False

    try:
        end = clock() + stop_wait
        while True:
            if gone():
                return _ok(app, method)
            if failed_code is not None:
                return _fail(app, f"the stop command failed (exit code {failed_code})", method)
            if clock() >= end:
                break
            sleep(poll)
    finally:
        if proc.poll() is None:
            proc.returncode = 0   # still running on purpose; keep Popen from warning when it is collected
    log.info("stop %s: still running after %.0f s, left alone", app.name, stop_wait)
    return StopResult(False, f"{app.name} is still open (it may be asking you something)", "warn", method)


def _stop_by_closing(app, index, pool, found, look, own_pid, close, terminate, sleep, clock,
                     close_wait, kill_wait, poll) -> StopResult:
    method = "close"
    targets = find_targets(index, pool, found, own_pid)
    if not targets.owned:
        who = " and ".join(targets.rivals) or "another entry"
        return _fail(app, f"its processes also match {who}, so nothing was touched", method)
    if len(targets.owned) > MAX_MATCHED:
        return _fail(app, f"it matches {len(targets.owned)} processes, which looks too broad - "
                          "give the entry a narrower match", method)

    tree = {(p.pid, p.started) for p in targets.everything}
    closed = close({p.pid for p in targets.everything})
    log.info("stop %s: asked %d window(s) to close; tree=%s", app.name, closed,
             sorted(p.pid for p in targets.everything))
    if closed:
        _wait_until(lambda: look()[1] is False, close_wait, sleep, clock, poll)

    found, running = look()
    if found is None:
        return _fail(app, "could not read the list of running programs", method, windows_closed=closed)

    # Victims: whatever the entry still claims (and what those started), plus what is left of the tree
    # seen before the close even if its parent has already gone - minus anything another entry claims.
    fresh = find_targets(index, pool, found, own_pid) if running else Targets()
    claims = procs.compute_claims(pool, found, own_pid)
    off_limits = protected_pids(found, own_pid) | claims.shared[index]
    for j in range(len(pool)):
        if j != index:
            off_limits |= claims.owned[j] | claims.shared[j]
    leftovers = [p for p in found if (p.pid, p.started) in tree and p.pid not in off_limits]
    depth = {**targets.depth, **fresh.depth}
    victims = {p.pid: p for p in fresh.everything + leftovers}.values()
    victims = sorted(victims, key=lambda p: (-depth.get(p.pid, 0), p.pid))   # children before parents
    if not victims:
        if running:   # still there, but only through processes it shares with another entry
            who = " and ".join(sorted({pool[j].name for j in claims.rivals[index]})) or "another entry"
            return _fail(app, f"its processes also match {who}, so nothing was touched", method,
                         windows_closed=closed)
        return _ok(app, method, windows_closed=closed)

    killed: list[int] = []
    refused: list[tuple[int, int]] = []
    for p in victims:
        code = terminate(p.pid, p.started)
        if code == 0:
            killed.append(p.pid)
        else:
            refused.append((p.pid, code))
    log.info("stop %s: terminated=%s refused=%s", app.name, killed, refused)

    def settled() -> bool:
        seen, running_now = look()
        if seen is None:
            return False
        alive = {p.pid for p in seen}
        return running_now is False and not any(pid in alive for pid in killed)

    gone = _wait_until(settled, kill_wait, sleep, clock, poll)
    if gone and not refused:
        return _ok(app, method, killed=killed, windows_closed=closed)
    count = len(refused)
    why = f"{count if count else 'some'} process{'es' if count != 1 else ''} would not end"
    if any(code == 5 for _, code in refused):
        why += " (access denied)"
    return _fail(app, why, method, killed=killed, windows_closed=closed)
