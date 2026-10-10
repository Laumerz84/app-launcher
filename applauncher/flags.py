"""Finding the command-line options (``--mini``, ``-q`` ...) an app understands, for the Properties dialog.

Nothing is run: the app's own Python script is read and parsed.

1. The script is the last ``.py`` / ``.pyw`` / ``.cmd`` / ``.bat`` path in the entry's command and args
   (for ``wt ... cmd /c belt.cmd`` that is ``belt.cmd``: launch options are added at the very end,
   so they reach that innermost script). A ``.cmd`` / ``.bat`` is followed to the ``.py`` it calls.
2. In the script, options come from ``add_argument("--x", help=...)`` calls and from comparisons
   such as ``"--quit" in sys.argv``.
3. A launcher stub that does ``from pkg.app import main`` is followed into ``pkg/app.py`` (and once
   more from there), since that is where the real argument parsing usually lives.
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import App

SCRIPT_EXTS = (".py", ".pyw", ".cmd", ".bat")
FLAG_RE = re.compile(r"^--?[A-Za-z][\w-]*$")
LONG_FLAG_RE = re.compile(r"^--[A-Za-z][\w-]*$")
FOLLOW_NAMES = {"main", "cli", "run", "app"}  # imported names that point at the real entry point
NO_VALUE_ACTIONS = {"store_true", "store_false", "store_const", "count", "append_const", "version", "help",
                    "BooleanOptionalAction"}
MAX_FILES = 6


@dataclass(frozen=True)
class Flag:
    names: tuple[str, ...]          # every spelling, e.g. ("-m", "--mini")
    help: str = ""
    takes_value: bool = False
    metavar: str = ""

    @property
    def name(self) -> str:
        """The spelling to insert: the long one if there is one."""
        return max(self.names, key=len)

    def label(self) -> str:
        text = ", ".join(self.names) + (f" {self.metavar}" if self.takes_value else "")
        return f"{text}    {self.help}" if self.help else text


@dataclass
class Discovery:
    flags: list[Flag] = field(default_factory=list)
    sources: list[Path] = field(default_factory=list)   # files that were read
    script: Optional[Path] = None                        # where the search started
    note: str = ""                                       # why nothing was found, if so


# ----------------------------------------------------------------------------- finding the script
def _tokens(app: App) -> list[str]:
    parts = [app.command]
    if isinstance(app.args, str):
        parts += re.findall(r'"[^"]*"|\'[^\']*\'|\S+', app.args)
    else:
        parts += list(app.args)
    return [os.path.expandvars(p).strip().strip("'\"") for p in parts if p]


def _resolve(token: str, folder: str) -> Optional[Path]:
    path = Path(token)
    if not path.is_absolute() and folder:
        path = Path(folder) / path
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def target_script(app: App) -> Optional[Path]:
    """The innermost script this entry runs, or None (a plain .exe, a web page...)."""
    folder = os.path.expandvars(app.working_dir) if app.working_dir else ""
    found = None
    for token in _tokens(app):
        if token.lower().endswith(SCRIPT_EXTS):
            path = _resolve(token, folder) or _resolve(token, os.path.dirname(os.path.expandvars(app.command)))
            if path:
                found = path
    if found and found.suffix.lower() in (".cmd", ".bat"):
        return _python_in_batch(found) or found
    return found


def _python_in_batch(batch: Path) -> Optional[Path]:
    """The .py a .cmd / .bat file runs (``python "%~dp0belt.py" %*``)."""
    try:
        text = batch.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    here = str(batch.parent) + os.sep
    for line in text.splitlines():
        if line.strip().lower().startswith(("rem ", "::", "@rem")):
            continue
        line = line.replace("%~dp0", here)
        for token in re.findall(r'"([^"]+\.pyw?)"|(\S+\.pyw?)\b', line, flags=re.IGNORECASE):
            path = _resolve((token[0] or token[1]).strip("'"), str(batch.parent))
            if path:
                return path
    return None


# ----------------------------------------------------------------------------- reading a script
def _const(node: Optional[ast.AST]) -> object:
    try:
        return ast.literal_eval(node) if node is not None else None
    except (ValueError, SyntaxError, TypeError):
        return None


def _flags_in(tree: ast.AST) -> list[Flag]:
    flags: list[Flag] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("add_argument", "add_option"):
            names = tuple(a.value for a in node.args
                          if isinstance(a, ast.Constant) and isinstance(a.value, str) and FLAG_RE.match(a.value))
            if not names:
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            action = kw.get("action")
            action_name = action.attr if isinstance(action, ast.Attribute) else _const(action)
            if action_name == "help":
                continue
            help_text = _const(kw.get("help"))
            metavar = _const(kw.get("metavar"))
            choices = _const(kw.get("choices"))
            takes_value = action_name not in NO_VALUE_ACTIONS
            if takes_value and not isinstance(metavar, str):
                metavar = ("{" + ",".join(map(str, choices)) + "}") if isinstance(choices, (list, tuple)) \
                    else names[-1].lstrip("-").upper().replace("-", "_")
            flags.append(Flag(names=names, help=_tidy(help_text), takes_value=takes_value,
                              metavar=metavar if takes_value else ""))
        elif isinstance(node, ast.Compare) and any(isinstance(op, (ast.In, ast.NotIn, ast.Eq, ast.NotEq))
                                                   for op in node.ops):
            for side in [node.left, *node.comparators]:
                values = _const(side) if isinstance(side, (ast.Constant, ast.Tuple, ast.List, ast.Set)) else None
                for value in (values if isinstance(values, (tuple, list, set)) else [values]):
                    if isinstance(value, str) and LONG_FLAG_RE.match(value):
                        flags.append(Flag(names=(value,)))
    return flags


def _tidy(text: object) -> str:
    if not isinstance(text, str) or text == "==SUPPRESS==":
        return ""
    text = " ".join(text.replace("%%", "%").split())
    return text if len(text) <= 90 else text[:89].rstrip() + "…"


def _followed_imports(tree: ast.AST, root: Path) -> list[Path]:
    """Local modules this script imports its entry point from (``from sheets.app import main``)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 \
                and any(a.name in FOLLOW_NAMES for a in node.names):
            base = root.joinpath(*node.module.split("."))
            for candidate in (base.with_suffix(".py"), base / "__main__.py", base / "__init__.py"):
                if candidate.is_file():
                    found.append(candidate)
                    break
    return found


def discover(app: App) -> Discovery:
    result = Discovery(script=target_script(app))
    if not app.command:
        result.note = "Pick a program to see its options."
        return result
    if result.script is None:
        result.note = "This entry doesn't run a Python script, so its options can't be read."
        return result
    if result.script.suffix.lower() not in (".py", ".pyw"):
        result.note = f"{result.script.name} doesn't call a Python script, so its options can't be read."
        return result
    root = result.script.parent
    queue, seen = [result.script], set()
    by_name: dict[str, Flag] = {}
    while queue and len(seen) < MAX_FILES:
        path = queue.pop(0)
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"), filename=str(path))
        except (OSError, SyntaxError, ValueError):
            continue
        result.sources.append(path)
        for flag in _flags_in(tree):
            old = by_name.get(flag.name)
            # an add_argument() entry (help, value) beats a bare comparison of the same flag
            if old is None or (not old.help and flag.help) or len(flag.names) > len(old.names):
                by_name[flag.name] = flag
        queue += _followed_imports(tree, root)
    # spellings already covered by a fuller entry (-m listed alone and as part of -m/--mini)
    flags = list(by_name.values())
    covered = {n for f in flags if len(f.names) > 1 for n in f.names}
    result.flags = sorted((f for f in flags if len(f.names) > 1 or f.names[0] not in covered),
                          key=lambda f: f.name.lstrip("-").lower())
    if not result.flags:
        result.note = f"No options found in {result.script.name}."
    return result
