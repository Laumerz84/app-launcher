"""Turning an apps.json entry into a launch, and launching it detached.

``build_plan`` is pure (it only looks at the file system to check things
exist), so it is easy to test: it returns a ``LaunchPlan`` describing exactly
what will be started. ``launch`` then performs the plan.

Entry types, decided from the ``command``:

=================  ==========================================================
.exe / .com / name  started directly with its args
.cmd / .bat         run through ``cmd.exe /s /c "..."``
.ps1                run through ``powershell.exe ... -File script``
.py / .pyw          run with python.exe (console) or pythonw.exe (no console)
anything else       opened with its default app (``.html`` -> default browser,
                    ``https://...`` -> default browser, a folder -> Explorer)
=================  ==========================================================
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from .config import App

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
STARTF_USESHOWWINDOW = 0x00000001
SW_HIDE = 0
SW_SHOWMINNOACTIVE = 7

_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+://")


class LaunchError(Exception):
    """A launch could not happen; the message is short and meant to be shown to the user."""


@dataclass(frozen=True)
class LaunchPlan:
    mode: str                # "exe" | "cmd" | "powershell" | "python" | "open"
    exe: str = ""            # program to run (process modes)
    cmdline: str = ""        # exact command line handed to CreateProcess (process modes)
    cwd: Optional[str] = None
    creationflags: int = 0
    window: str = "normal"   # "normal" | "minimized" | "hidden"
    target: str = ""         # what to open (open mode)
    open_args: str = ""      # arguments for the default app (open mode)

    @property
    def is_open(self) -> bool:
        return self.mode == "open"

    @property
    def has_console(self) -> bool:
        return bool(self.creationflags & CREATE_NEW_CONSOLE)


# --------------------------------------------------------------------------- locating programs
def _expand(text: str) -> str:
    return os.path.expandvars(os.path.expanduser(text.strip().strip('"')))


def system32() -> str:
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")


def cmd_exe() -> str:
    return os.environ.get("ComSpec") or os.path.join(system32(), "cmd.exe")


def powershell_exe() -> str:
    path = os.path.join(system32(), "WindowsPowerShell", "v1.0", "powershell.exe")
    if os.path.isfile(path):
        return path
    return shutil.which("powershell") or shutil.which("pwsh") or path


def python_exe(console: bool) -> str:
    name = "python.exe" if console else "pythonw.exe"
    beside = os.path.join(os.path.dirname(sys.executable), name)
    if os.path.isfile(beside):
        return beside
    return shutil.which(name) or beside


def _looks_like_path(text: str) -> bool:
    return os.path.isabs(text) or "\\" in text or "/" in text


def _classify(command: str) -> str:
    if _URL_RE.match(command):
        return "open"
    if os.path.isdir(command):
        return "open"
    ext = os.path.splitext(command)[1].lower()
    if ext in (".exe", ".com", ""):
        return "exe"
    if ext in (".cmd", ".bat"):
        return "cmd"
    if ext == ".ps1":
        return "powershell"
    if ext in (".py", ".pyw"):
        return "python"
    return "open"


def _find_file(command: str, cwd: Optional[str], check: bool, what: str) -> str:
    """Resolve *command* to a file path, or raise LaunchError (when *check*)."""
    if _looks_like_path(command):
        candidate = command
        if not os.path.isabs(candidate) and cwd:
            candidate = os.path.join(cwd, candidate)
        if check and not os.path.isfile(candidate):
            raise LaunchError(f"{what} not found: {command}")
        return candidate
    found = shutil.which(command)
    if found:
        return found
    if cwd and os.path.isfile(os.path.join(cwd, command)):
        return os.path.join(cwd, command)
    if check:
        raise LaunchError(f"{what} not found: {command} (not a file, not on PATH)")
    return command


def _split_args(app: App) -> tuple[list[str], str]:
    """(args as a list, raw string) - exactly one of them is non-empty."""
    if isinstance(app.args, str):
        return [], _expand_raw(app.args)
    return [os.path.expandvars(a) for a in app.args], ""


def _expand_raw(text: str) -> str:
    return os.path.expandvars(text).strip()


def _join(exe: str, args: Sequence[str], raw: str) -> str:
    line = subprocess.list2cmdline([exe] + list(args))
    return f"{line} {raw}" if raw else line


# --------------------------------------------------------------------------- planning
def build_plan(app: App, check: bool = True) -> LaunchPlan:
    """Work out how to start *app*. With ``check`` it verifies files exist and raises LaunchError if not."""
    command = _expand(app.command)
    if not command:
        raise LaunchError("no command set")
    args, raw = _split_args(app)
    mode = _classify(command)

    # Working folder: the one given, else the folder that holds the command.
    cwd: Optional[str] = None
    if app.working_dir:
        cwd = _expand(app.working_dir)
        if check and not os.path.isdir(cwd):
            raise LaunchError(f"working folder not found: {cwd}")
    elif (mode != "open" and os.path.isabs(command)) or (mode == "open" and os.path.isfile(command)):
        folder = os.path.dirname(command)
        cwd = folder if os.path.isdir(folder) else None

    flags = CREATE_NEW_CONSOLE if app.needs_console else CREATE_NO_WINDOW

    if mode == "open":
        if check and not _URL_RE.match(command) and not os.path.exists(command):
            raise LaunchError(f"file not found: {command}")
        return LaunchPlan(mode="open", cwd=cwd, target=command, window=app.window,
                          open_args=(raw or subprocess.list2cmdline(args)) if (raw or args) else "")

    if mode == "exe":
        exe = _find_file(command, cwd, check, "program")
        return LaunchPlan(mode="exe", exe=exe, cmdline=_join(exe, args, raw), cwd=cwd,
                          creationflags=flags, window=app.window)

    if mode == "cmd":
        script = _find_file(command, cwd, check, "script")
        inner = _join(script, args, raw)
        shell = cmd_exe()
        return LaunchPlan(mode="cmd", exe=shell, cwd=cwd, creationflags=flags, window=app.window,
                          cmdline=f'{subprocess.list2cmdline([shell])} /s /c "{inner}"')

    if mode == "powershell":
        script = _find_file(command, cwd, check, "script")
        shell = powershell_exe()
        pre = ["-NoLogo", "-ExecutionPolicy", "Bypass"] + (["-NoExit"] if app.needs_console else []) + ["-File", script]
        return LaunchPlan(mode="powershell", exe=shell, cwd=cwd, creationflags=flags, window=app.window,
                          cmdline=_join(shell, pre + args, raw))

    # python
    script = _find_file(command, cwd, check, "script")
    interpreter = python_exe(console=app.needs_console)
    return LaunchPlan(mode="python", exe=interpreter, cwd=cwd, creationflags=flags, window=app.window,
                      cmdline=_join(interpreter, [script] + args, raw))


def build_stop_plan(app: App, check: bool = True) -> Optional[LaunchPlan]:
    """How to run the entry's stop_command (None if it has none).

    It is started like any other command - same kinds (program, .cmd, .ps1, .py), same quoting - but
    never with a console, and in the entry's own working folder when it has one.
    """
    if not app.stop_command:
        return None
    plan = build_plan(App(name=app.name, command=app.stop_command, args=app.stop_args,
                          working_dir=app.working_dir, needs_console=False, window="normal"), check=check)
    if plan.is_open:
        raise LaunchError(f"stop_command must be a program or script, not a document: {app.stop_command}")
    return plan


# --------------------------------------------------------------------------- launching
def _friendly_os_error(exc: OSError) -> str:
    code = getattr(exc, "winerror", None)
    table = {
        2: "file not found",
        3: "folder not found",
        5: "access denied",
        193: "not a valid Windows program",
        216: "not a valid Windows program",  # wrong CPU type / not an executable image
        740: "needs administrator rights",
    }
    if code in table:
        return table[code]
    return (exc.strerror or str(exc)).strip() or type(exc).__name__


def start_process(plan: LaunchPlan, popen: Callable = subprocess.Popen):
    """Start a process plan (not an 'open' plan) and hand back the Popen object."""
    try:
        startup = None
        if plan.window in ("minimized", "hidden"):
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= STARTF_USESHOWWINDOW
            startup.wShowWindow = SW_SHOWMINNOACTIVE if plan.window == "minimized" else SW_HIDE

        kwargs = dict(cwd=plan.cwd, startupinfo=startup, close_fds=True, executable=plan.exe or None)
        if not plan.has_console:
            # No console for the child; give it real handles so a launcher without
            # a console (pythonw) never trips over invalid inherited ones.
            kwargs.update(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            return popen(plan.cmdline, creationflags=plan.creationflags | CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 5:
                raise
            # The launcher runs inside a job that forbids breakaway; start without it.
            return popen(plan.cmdline, creationflags=plan.creationflags, **kwargs)
    except OSError as exc:
        raise LaunchError(_friendly_os_error(exc)) from exc


def launch(plan: LaunchPlan, popen: Callable = subprocess.Popen) -> Optional[int]:
    """Start the plan, fully detached from this process. Returns the pid (None for 'open')."""
    if plan.is_open:
        try:
            kwargs = {}
            if plan.cwd:
                kwargs["cwd"] = plan.cwd
            if plan.open_args:
                kwargs["arguments"] = plan.open_args
            os.startfile(plan.target, **kwargs)
        except OSError as exc:
            raise LaunchError(_friendly_os_error(exc)) from exc
        return None
    proc = start_process(plan, popen)
    pid = proc.pid
    proc.returncode = 0  # we never wait for it (that is the point); keeps Popen from warning at garbage collection
    return pid


def launch_app(app: App, popen: Callable = subprocess.Popen) -> Optional[int]:
    return launch(build_plan(app), popen=popen)


def open_in_editor(path: str, popen: Callable = subprocess.Popen) -> None:
    """Open a file in Notepad (falls back to its default app)."""
    notepad = shutil.which("notepad.exe") or os.path.join(system32(), "notepad.exe")
    try:
        popen([notepad, str(path)], close_fds=True, creationflags=CREATE_NO_WINDOW)
    except OSError:
        try:
            os.startfile(str(path))
        except OSError as exc:
            raise LaunchError(_friendly_os_error(exc)) from exc


# --------------------------------------------------------------------------- launch all
@dataclass
class LaunchAllResult:
    launched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # already running
    excluded: list[str] = field(default_factory=list)  # launch_all is false
    failed: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        if self.skipped and not self.launched and not self.failed:
            return f"Nothing to launch - {len(self.skipped)} already running."
        parts: list[str] = []
        if self.launched:
            parts.append(f"Launched {len(self.launched)}")
        if self.skipped:
            parts.append(f"{len(self.skipped)} already running")
        if self.failed:
            parts.append("failed: " + ", ".join(name for name, _ in self.failed))
        return (", ".join(parts) + ".") if parts else "Nothing to launch."


def launch_all(apps: Sequence[App], running: Sequence[Optional[bool]],
               launch_fn: Callable[[App], object]) -> LaunchAllResult:
    """Start every app that is not running. ``running[i]`` is True / False / None (unknown).

    Unknown counts as not running. Apps with ``launch_all`` false are left out.
    One app failing never stops the others.
    """
    result = LaunchAllResult()
    for index, app in enumerate(apps):
        if not app.launch_all:
            result.excluded.append(app.name)
            continue
        state = running[index] if index < len(running) else None
        if state is True:
            result.skipped.append(app.name)
            continue
        try:
            launch_fn(app)
        except LaunchError as exc:
            result.failed.append((app.name, str(exc)))
        except Exception as exc:  # never let one entry break the rest
            result.failed.append((app.name, f"{type(exc).__name__}: {exc}"))
        else:
            result.launched.append(app.name)
    return result
