"""Finding runnable projects in the Python folder, so a new project shows up in the terminal
launcher without anyone editing a list.

Every folder next to this launcher's own folder is a project. Its entry point is the first of:

=====================  =====================================================================
built program          the newest ``dist\\*.exe`` (a PyInstaller build)
explicit launcher      ``start_app.py`` or ``launcher.py`` (console), ``launch.pyw`` or
                       ``launcher.pyw`` (no console)
Streamlit app          a top-level ``.py`` that imports streamlit: ``app.py``, then
                       ``dashboard.py``, then any other; run as ``python -m streamlit run``
main script            a top-level script with an ``if __name__ == "__main__"`` block (or the
                       only script): the one named like the folder, then ``main.py``,
                       ``run.py``, ``app.py``; otherwise the single candidate
notebook               the newest ``.ipynb`` that is not a backup, opened in its default app
=====================  =====================================================================

A project's own ``.venv`` is used for its scripts when there is one. Folders that ``apps.json``
already covers are left to their apps.json entry, which always wins.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import commands
from .config import App

SKIP_FOLDERS = {"__pycache__", "node_modules", ".git", ".venv", "venv"}
NOT_ENTRY = re.compile(r"^(_?selftest|test_|conftest|setup|build|make_|migrate_|fixtures|generate|"
                       r"diag|peek_|bench_|try_|regress_)", re.I)
NOTEBOOK_SKIP = re.compile(r"backup|checkpoint", re.I)
MAIN_GUARD = re.compile(r"""if\s+__name__\s*==\s*['"]__main__['"]""")
STREAMLIT = re.compile(r"^\s*(import\s+streamlit|from\s+streamlit\s+import)", re.M)


@dataclass(frozen=True)
class Program:
    app: App
    kind: str                 # "program" | "app" | "script" | "notebook" | "listed"
    folder: str = ""          # project folder ('' for apps.json entries outside the Python folder)
    aliases: tuple[str, ...] = ()


def key(text: str) -> str:
    """What names are compared on: letters and digits only, lower case."""
    return re.sub(r"[^0-9a-z]", "", text.lower())


def projects_root(launcher_dir: Path) -> Path:
    """The folder whose subfolders are projects: APP_LAUNCHER_PROJECTS, else the launcher's parent."""
    return Path(os.environ.get("APP_LAUNCHER_PROJECTS") or launcher_dir.parent)


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _interpreter(folder: Path, console: bool) -> str:
    venv = folder / ".venv" / "Scripts" / ("python.exe" if console else "pythonw.exe")
    return str(venv) if venv.is_file() else commands.python_exe(console=console)


def _script_app(name: str, folder: Path, script: Path) -> App:
    console = script.suffix.lower() == ".py"
    return App(name=name, command=_interpreter(folder, console), args=(str(script),),
               working_dir=str(folder), needs_console=console, match=(str(script),))


def find_entry(folder: Path) -> Optional[Program]:
    """The way to start the project in *folder*, or None if it has nothing runnable."""
    name = folder.name
    dist = folder / "dist"
    if dist.is_dir():
        exes = sorted(dist.glob("*.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
        if exes:
            exe = exes[0]
            # A console exe needs its own window (closing it stops the app); a GUI exe ignores it.
            return Program(App(name=name, command=str(exe), working_dir=str(dist), needs_console=True),
                           "program", str(folder), (key(exe.stem),))

    scripts = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in (".py", ".pyw"))
    by_name = {p.name.lower(): p for p in scripts}
    for explicit in ("start_app.py", "launcher.py", "launch.pyw", "launcher.pyw"):
        if explicit in by_name:
            return Program(_script_app(name, folder, by_name[explicit]), "app", str(folder))

    py = [p for p in scripts if p.suffix.lower() == ".py" and not NOT_ENTRY.match(p.stem)]
    streamlit = [p for p in py if STREAMLIT.search(_read(p))]
    if streamlit:
        order = {"app.py": 0, "dashboard.py": 1}
        app_py = min(streamlit, key=lambda p: (order.get(p.name.lower(), 2), p.name.lower()))
        return Program(App(name=name, command=_interpreter(folder, True),
                           args=("-m", "streamlit", "run", str(app_py)), working_dir=str(folder),
                           needs_console=True, match=(str(app_py),)), "app", str(folder))

    runnable = [p for p in scripts if not NOT_ENTRY.match(p.stem)]
    mains = [p for p in runnable if MAIN_GUARD.search(_read(p))] or (runnable if len(runnable) == 1 else [])
    if mains:
        preferred = [key(name), "main", "run", "app"]
        pick = next((p for want in preferred for p in mains if key(p.stem) == want), None)
        if pick is None and len(mains) == 1:
            pick = mains[0]
        if pick is not None:
            return Program(_script_app(name, folder, pick), "script", str(folder))

    notebooks = sorted((p for p in folder.glob("*.ipynb") if not NOTEBOOK_SKIP.search(p.name)),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if notebooks:
        return Program(App(name=name, command=str(notebooks[0]), working_dir=str(folder), launch_all=False),
                       "notebook", str(folder))
    return None


def _covered(app: App, folder: Path) -> bool:
    """True if an apps.json entry runs something inside *folder*."""
    texts = [app.command, app.working_dir] + ([app.args] if isinstance(app.args, str) else list(app.args))
    root = os.path.normcase(str(folder)).rstrip("\\/") + os.sep
    for text in texts:
        path = os.path.normcase(os.path.expandvars(text.strip().strip('"')))
        if path == root.rstrip(os.sep) or path.startswith(root):
            return True
    return False


def all_programs(listed: Sequence[App], root: Path, exclude: Iterable[Path] = ()) -> tuple[list[Program], list[str]]:
    """apps.json entries first, then every project found under *root* that apps.json does not cover.
    Also returns the names of project folders with nothing runnable."""
    programs = [Program(app, "listed", "") for app in listed]
    skipped: list[str] = []
    excluded = {os.path.normcase(str(p)) for p in exclude}
    try:
        folders = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        return programs, skipped
    for folder in folders:
        if folder.name.startswith(".") or folder.name in SKIP_FOLDERS or os.path.normcase(str(folder)) in excluded:
            continue
        if any(_covered(app, folder) for app in listed):
            continue
        try:
            found = find_entry(folder)
        except OSError:
            found = None
        if found is None:
            skipped.append(folder.name)
        else:
            programs.append(found)
    return programs, skipped


# --------------------------------------------------------------------------- typing a name
def names_of(program: Program) -> list[str]:
    return [key(program.app.name)] + [a for a in program.aliases if a]


def resolve(text: str, programs: Sequence[Program]) -> tuple[Optional[Program], list[Program]]:
    """(the program *text* means, or None; and when None, the candidates worth suggesting).

    Accepted: the list number, the name (spaces, case and punctuation ignored), the start of a
    name, or a part of it - as long as only one program fits."""
    wanted = key(text)
    if not wanted:
        return None, []
    if wanted.isdigit():
        n = int(wanted)
        return (programs[n - 1], []) if 1 <= n <= len(programs) else (None, [])
    for test in (lambda n: n == wanted, lambda n: n.startswith(wanted), lambda n: wanted in n):
        hits = [p for p in programs if any(test(n) for n in names_of(p))]
        if len(hits) == 1:
            return hits[0], []
        if len(hits) > 1:
            return None, hits
    import difflib
    close = difflib.get_close_matches(wanted, [key(p.app.name) for p in programs], n=3, cutoff=0.6)
    return None, [p for p in programs if key(p.app.name) in close]


def split_command(line: str, programs: Sequence[Program]) -> tuple[Optional[Program], list[str], list[Program]]:
    """Split typed text into a program and extra arguments. The longest run of leading words that
    names exactly one program wins, so names with spaces ("RMD Script") need no quotes."""
    words = line.split()
    best: list[Program] = []
    for cut in range(len(words), 0, -1):
        found, candidates = resolve(" ".join(words[:cut]), programs)
        if found is not None:
            return found, words[cut:], []
        if candidates and not best:
            best = candidates
    return None, [], best


def with_args(app: App, extra: Sequence[str]) -> App:
    if not extra:
        return app
    if isinstance(app.args, str):
        import subprocess
        return replace(app, args=(app.args + " " + subprocess.list2cmdline(list(extra))).strip())
    return replace(app, args=tuple(app.args) + tuple(extra))
