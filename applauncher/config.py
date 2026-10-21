"""Loading and validating apps.json.

The file is meant to be edited by hand in Notepad, so the loader is forgiving
about what it can be and strict about telling you what is wrong:

* a broken file (bad JSON, unreadable) yields ``ok=False`` and a readable
  message - the caller keeps showing the previous list;
* a bad *entry* is skipped with a message naming it, the rest still load;
* keys that start with an underscore (``_help``, ``_note``) are comments.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WINDOW_MODES = ("normal", "minimized", "hidden")
THEMES = ("system", "light", "dark")

# Keys an entry may carry. Anything else (not starting with "_") earns a warning,
# which catches typos such as "needsconsole".
KNOWN_KEYS = {
    "name", "command", "args", "working_dir", "cwd", "icon", "needs_console",
    "window", "launch_all", "match", "stop_command", "stop_args", "options",
}

HELP_TEXT: dict[str, Any] = {
    "about": "One entry per app in the 'apps' list below. Save the file and the launcher reloads it by itself. "
             "Keys starting with an underscore (like this _help) are comments and are ignored.",
    "layout": "Optional, next to \"apps\": the window's size, e.g. {\"width\": 700, \"height\": 500}. The "
              "window saves it when you resize it; cards flow into as many columns as fit the width.",
    "theme": "Optional, next to \"apps\" (not inside an entry): \"system\" (default, follows the Windows "
             "light/dark app setting), \"light\" or \"dark\".",
    "name": "Text shown on the button. Required.",
    "command": "What to run. Required. An .exe / .cmd / .bat / .ps1 / .py / .pyw file, a program on PATH, "
               "or a document / web page to open with its default app (.html opens in your default browser, "
               "so does an https:// address). Environment variables like %USERPROFILE% are expanded.",
    "args": "Optional. A list of arguments, one string per argument: [\"/c\", \"C:\\\\tools\\\\thing.cmd\"]. "
            "A single string is also accepted and passed as-is. Remember to double every backslash in JSON.",
    "working_dir": "Optional. Folder the app starts in. If left out, the folder that holds the command is used.",
    "icon": "Optional. A .ico / .exe / .dll file, or \"file,index\" to pick an icon inside an exe or dll "
            "(like Windows shortcuts do). If left out, the command's own icon is used when it is a "
            "normal program, otherwise a coloured letter badge is drawn.",
    "needs_console": "Optional, true or false (default false). true gives the app its own new console window "
                     "(for terminal programs). false starts it with no console.",
    "window": "Optional: \"normal\" (default), \"minimized\" or \"hidden\". Only affects how the new process's "
              "first window is shown.",
    "launch_all": "No longer used (the Launch all button was removed); still accepted.",
    "stop_command": "Optional. How to ask this app to quit, in place of the launcher ending it itself. "
                    "Same kinds of thing as command (a program, .cmd, .ps1, ...), started with no console. "
                    "When the running dot shows and you click Stop twice, the launcher runs this, then waits up to "
                    "10 seconds for the app to disappear. It never force-kills afterwards: if the app is still "
                    "there (perhaps asking about unsaved work) the launcher just says so. Without stop_command, "
                    "Stop closes the app's windows politely, waits 2 seconds, then ends that app's processes and "
                    "the processes they started (only the ones this entry's running dot recognises).",
    "stop_args": "Optional. Arguments for stop_command, written the same way as args.",
    "options": "Optional. Launch options: text added to the end of the command line every time the app is "
               "opened, e.g. \"--mini\". Easiest to set by right-clicking the app > Properties, which lists "
               "the options the app understands.",
    "match": "Optional. Text (or a list of texts) that must all appear in a running process's command line "
             "for the running dot to light up, for example [\"belt.py\", \"--mini\"]. Normally worked out "
             "automatically from the command; set it when the dot never lights or lights wrongly.",
    "_note": "Free text, ignored. Handy for reminding yourself why an entry is there.",
}


@dataclass(frozen=True)
class App:
    name: str
    command: str
    args: tuple[str, ...] | str = ()
    working_dir: str = ""
    icon: str = ""
    needs_console: bool = False
    window: str = "normal"
    launch_all: bool = True             # no longer used by the window (Launch all was removed); still accepted
    match: tuple[str, ...] = ()
    note: str = ""
    stop_command: str = ""
    stop_args: tuple[str, ...] | str = ()
    options: str = ""                   # launch options, appended to the command line as typed


@dataclass
class LoadResult:
    apps: list[App] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)    # things that were skipped or wrong
    warnings: list[str] = field(default_factory=list)  # things that were tolerated
    ok: bool = True                                    # False: file unusable, keep the old list
    theme: str = "system"                              # "system" | "light" | "dark"
    layout: dict = field(default_factory=dict)         # window size: {"width": px, "height": px} (100% scale)

    @property
    def message(self) -> str:
        """One short line for the window's status area ('' if all is well)."""
        if self.errors:
            extra = f" (+{len(self.errors) - 1} more)" if len(self.errors) > 1 else ""
            return self.errors[0] + extra
        if self.warnings:
            return self.warnings[0]
        return ""


def _describe(index: int, raw: Any) -> str:
    name = raw.get("name") if isinstance(raw, dict) else None
    return f"entry {index + 1}" + (f" ({name!r})" if isinstance(name, str) and name.strip() else "")


def _parse_entry(index: int, raw: Any, warnings: list[str]) -> App:
    """Return the App, or raise ValueError with a readable reason."""
    if not isinstance(raw, dict):
        raise ValueError("must be an object like {\"name\": ..., \"command\": ...}")
    where = _describe(index, raw)

    def text(key: str, required: bool = False, default: str = "") -> str:
        value = raw.get(key, default)
        if value is None:
            value = default
        if not isinstance(value, str):
            raise ValueError(f"'{key}' must be text")
        value = value.strip()
        if required and not value:
            raise ValueError(f"'{key}' is required")
        return value

    def boolean(key: str, default: bool) -> bool:
        value = raw.get(key, default)
        if not isinstance(value, bool):
            raise ValueError(f"'{key}' must be true or false (no quotes)")
        return value

    name = text("name", required=True)
    command = text("command", required=True)

    def arg_list(key: str) -> tuple[str, ...] | str:
        value = raw.get(key, [])
        if value is None:
            value = []
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            for n, item in enumerate(value):
                if not isinstance(item, str):
                    raise ValueError(f"{key} item {n + 1} must be text")
            return tuple(value)
        raise ValueError(f"'{key}' must be a list of text (or one text)")

    args = arg_list("args")
    stop_command = text("stop_command")
    stop_args = arg_list("stop_args")
    if stop_args and not stop_command:
        warnings.append(f"{where}: stop_args is ignored without stop_command")

    working_dir = text("working_dir") or text("cwd")
    window = text("window", default="normal").lower() or "normal"
    if window not in WINDOW_MODES:
        raise ValueError(f"'window' must be one of {', '.join(WINDOW_MODES)}")

    match_raw = raw.get("match", [])
    if match_raw is None:
        match_raw = []
    if isinstance(match_raw, str):
        match_raw = [match_raw]
    if not isinstance(match_raw, list) or not all(isinstance(m, str) for m in match_raw):
        raise ValueError("'match' must be text or a list of text")
    match = tuple(m for m in (s.strip() for s in match_raw) if m)

    for key in raw:
        if not key.startswith("_") and key not in KNOWN_KEYS:
            warnings.append(f"{where}: unknown key '{key}' ignored")

    note = raw.get("_note", "")
    return App(
        name=name,
        command=command,
        args=args,
        working_dir=working_dir,
        icon=text("icon"),
        needs_console=boolean("needs_console", False),
        window=window,
        launch_all=boolean("launch_all", True),
        match=match,
        note=note if isinstance(note, str) else "",
        stop_command=stop_command,
        stop_args=stop_args,
        options=text("options"),
    )


def parse_apps(data: Any) -> LoadResult:
    """Validate already-parsed JSON."""
    result = LoadResult()
    if isinstance(data, dict):
        entries = data.get("apps")
        theme = data.get("theme", "system")
        if isinstance(theme, str) and theme.strip().lower() in THEMES:
            result.theme = theme.strip().lower()
        else:
            result.warnings.append(f"\"theme\" must be one of {', '.join(THEMES)} - following Windows")
        layout = data.get("layout", {})
        if isinstance(layout, dict):
            result.layout = {k: v for k, v in layout.items()
                             if k in ("width", "height") and isinstance(v, int) and not isinstance(v, bool) and v > 0}
        else:
            result.warnings.append('"layout" must be like {"width": 700, "height": 500} - ignored')
        if entries is None:
            result.ok = False
            result.errors.append("apps.json has no \"apps\" list")
            return result
    elif isinstance(data, list):
        entries = data
    else:
        result.ok = False
        result.errors.append("apps.json must be an object with an \"apps\" list")
        return result
    if not isinstance(entries, list):
        result.ok = False
        result.errors.append("\"apps\" must be a list [ ... ]")
        return result

    for index, raw in enumerate(entries):
        try:
            result.apps.append(_parse_entry(index, raw, result.warnings))
        except ValueError as exc:
            result.errors.append(f"{_describe(index, raw)}: {exc}")
    return result


def parse_text(text: str) -> LoadResult:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        result = LoadResult(ok=False)
        result.errors.append(f"apps.json is not valid JSON - line {exc.lineno}, column {exc.colno}: {exc.msg}")
        return result
    return parse_apps(data)


def load_apps(path: str | os.PathLike[str]) -> LoadResult:
    """Read and validate the file. Never raises."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")  # tolerate a BOM from older Notepad
    except FileNotFoundError:
        result = LoadResult(ok=False)
        result.errors.append(f"{path.name} not found")
        return result
    except (OSError, UnicodeDecodeError) as exc:
        result = LoadResult(ok=False)
        result.errors.append(f"Could not read {path.name}: {exc}")
        return result
    return parse_text(text)


# ----------------------------------------------------------------------------- changing the file from the window
# The window edits apps.json for Add app / Properties / Remove / Layout. Each change re-reads the file
# first, so an edit made in Notepad meanwhile is kept. Errors are ValueError / OSError with a readable message.

def _read_doc(path: Path) -> tuple[Any, list]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, list):              # old style: a bare list of apps
        data = {"apps": data}
    entries = data.get("apps") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path.name} has no \"apps\" list")
    return data, entries


def _write_doc(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _find(entries: list, index: int, name: str, path: Path) -> int:
    """The entry is found by position and checked by name, so the wrong app is never changed."""
    if 0 <= index < len(entries) and isinstance(entries[index], dict) \
            and str(entries[index].get("name", "")).strip() == name:
        return index
    named = [i for i, e in enumerate(entries) if isinstance(e, dict) and str(e.get("name", "")).strip() == name]
    if len(named) == 1:
        return named[0]
    raise ValueError(f"'{name}' was not found in {path.name} - was it renamed?")


def update_entry(path: str | os.PathLike[str], index: int, name: str, changes: dict[str, Any]) -> None:
    """Set keys of one entry; a value of None or "" removes that key."""
    path = Path(path)
    data, entries = _read_doc(path)
    entry = entries[_find(entries, index, name, path)]
    for key, value in changes.items():
        if value is None or value == "" or value == []:
            entry.pop(key, None)
        else:
            entry[key] = value
    _write_doc(path, data)


def set_options(path: str | os.PathLike[str], index: int, name: str, options: str) -> None:
    update_entry(path, index, name, {"options": options.strip()})


def add_entry(path: str | os.PathLike[str], entry: dict[str, Any]) -> None:
    path = Path(path)
    data, entries = _read_doc(path)
    entries.append({k: v for k, v in entry.items() if v not in (None, "", [])})
    _write_doc(path, data)


def remove_entry(path: str | os.PathLike[str], index: int, name: str) -> None:
    path = Path(path)
    data, entries = _read_doc(path)
    del entries[_find(entries, index, name, path)]
    _write_doc(path, data)


def set_top_level(path: str | os.PathLike[str], key: str, value: Any) -> None:
    """Set a setting next to "apps" (theme, layout ...); None removes it."""
    path = Path(path)
    data, _ = _read_doc(path)
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    _write_doc(path, data)


EXAMPLE_FILE = Path(__file__).resolve().parent.parent / "apps.example.json"


def skeleton_json() -> str:
    """Text for a fresh apps.json: a copy of apps.example.json (shipped in git; apps.json itself is
    each person's own and never committed), or a built-in starter if the example is missing or broken."""
    try:
        text = EXAMPLE_FILE.read_text(encoding="utf-8-sig")
        if parse_text(text).ok:
            return text
    except (OSError, UnicodeDecodeError):
        pass
    data = {
        "_help": HELP_TEXT,
        "theme": "system",
        "layout": {},
        "apps": [
            {
                "name": "Notepad",
                "command": "C:\\Windows\\System32\\notepad.exe",
                "_note": "Example entry - replace or delete it.",
            }
        ],
    }
    return json.dumps(data, indent=2) + "\n"


def ensure_file(path: str | os.PathLike[str]) -> bool:
    """Create a starter apps.json (from apps.example.json) if there is none. True if it was created."""
    path = Path(path)
    if path.exists():
        return False
    try:
        path.write_text(skeleton_json(), encoding="utf-8")
        return True
    except OSError:
        return False
