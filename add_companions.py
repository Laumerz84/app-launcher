"""Add the recommended companion tools to apps.json: UtilityBelt, UI Report Tool, Ekxel,
Codebase Dashboard and GitHub Updater.

All are optional. Clone them next to this folder (or anywhere, and pass the paths):

    git clone https://github.com/Laumerz84/utilitybelt.git
    git clone https://github.com/Laumerz84/ui-report-tool.git
    git clone https://github.com/Laumerz84/sheets.git
    git clone https://github.com/Laumerz84/codebase-dashboard.git
    git clone https://github.com/Laumerz84/updater.git
    python add_companions.py
    python add_companions.py --utilitybelt D:\\tools\\utilitybelt --ui-report-tool D:\\tools\\ui-report-tool

An entry with the same name is replaced (so a copied apps.json with someone else's paths
gets fixed); everything else in apps.json is kept. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from applauncher import config

HERE = Path(__file__).resolve().parent
UTILITYBELT_NAMES = ("utilitybelt",)
UI_REPORT_NAMES = ("ui-report-tool", "UI Report Tool", "ui_report_tool")
EKXEL_NAMES = ("sheets", "Sheets", "ekxel", "Ekxel")
EKXEL_MARKER = "sheets/app.py"
DASHBOARD_NAMES = ("codebase-dashboard", "Codebase Dashboard", "codebase_dashboard")
UPDATER_NAMES = ("updater", "Updater", "github-updater")
EKXEL_NAME = "Macrosoft Ekxel® 2003 Private Reserve Special Cuvée"


def find_sibling(names: tuple[str, ...], marker: str, base: Path) -> Path | None:
    """The first folder next to `base` with one of `names` that contains `marker`."""
    for name in names:
        folder = base.parent / name
        if (folder / marker).is_file():
            return folder
    return None


def utilitybelt_entry(folder: Path) -> dict:
    script = str(folder / "belt.py")
    return {
        "name": "UtilityBelt",
        "command": script,
        "working_dir": str(folder),
        "icon": r"C:\Windows\System32\imageres.dll,144",
        "needs_console": True,
        "match": [script],
        "_note": "Recommended companion (github.com/Laumerz84/utilitybelt). Added by add_companions.py; "
                 "needs its packages once: pip install -r requirements.txt in its folder. Runs belt.py in "
                 "its own console rather than belt.cmd, which needs cmd.exe (blocked on some PCs).",
    }


def ui_report_entry(folder: Path) -> dict:
    pythonw = str(folder / ".venv" / "Scripts" / "pythonw.exe")
    launch = str(folder / "launch.pyw")
    return {
        "name": "UI Report Tool",
        "command": pythonw,
        "args": [launch],
        "working_dir": str(folder),
        "icon": str(folder / "UIReportTool.ico"),
        "needs_console": False,
        "match": [launch],
        "stop_command": pythonw,
        "stop_args": [launch, "--quit"],
        "_note": "Recommended companion (github.com/Laumerz84/ui-report-tool). Added by add_companions.py. "
                 "Stop asks it to quit like its tray Quit.",
    }


def ekxel_entry(folder: Path) -> dict:
    pythonw = str(folder / ".venv" / "Scripts" / "pythonw.exe")
    launch = str(folder / "launch.pyw")
    return {
        "name": EKXEL_NAME,
        "command": pythonw,
        "args": [launch],
        "working_dir": str(folder),
        "icon": str(folder / "sheets.ico"),
        "needs_console": False,
        "match": [str(Path(folder.name) / "launch.pyw")],
        "stop_command": pythonw,
        "stop_args": [launch, "--quit"],
        "_note": "Recommended companion (github.com/Laumerz84/sheets): Excel-style spreadsheet with a Claude chat "
                 "panel. Added by add_companions.py; needs setup.bat once. Stop runs launch.pyw --quit, which "
                 "closes each window normally so unsaved work still asks to be saved.",
    }


def dashboard_entry(folder: Path) -> dict:
    here = Path(sys.executable)
    pythonw = here.with_name("pythonw.exe")
    script = str(folder / "dashboard.py")
    return {
        "name": "Codebase Dashboard",
        "command": str(pythonw if pythonw.is_file() else here),
        "args": [script],
        "working_dir": str(folder),
        "needs_console": False,
        "match": [str(Path(folder.name) / "dashboard.py")],
        "_note": "Recommended companion (github.com/Laumerz84/codebase-dashboard): code stats for a folder in "
                 "your browser (http://127.0.0.1:8765). Added by add_companions.py; standard library only. "
                 "Runs as a hidden server: starting it again just reopens the page, Stop ends it.",
    }


def updater_entry(folder: Path) -> dict:
    here = Path(sys.executable)
    pythonw = here.with_name("pythonw.exe")
    script = str(folder / "updater.py")
    return {
        "name": "GitHub Updater",
        "command": str(pythonw if pythonw.is_file() else here),
        "args": [script, "--window"],
        "working_dir": str(folder),
        "needs_console": False,
        "match": [str(Path(folder.name) / "updater.py"), "--window"],
        "_note": "Recommended companion (github.com/Laumerz84/updater): checks every GitHub clone for new commits "
                 "and offers to install them. Added by add_companions.py; standard library only. The match "
                 "includes --window so the daily checker that runs at login (updater.py --background) never "
                 "lights the dot and Stop never ends it.",
    }


def clipper_entry(folder: Path) -> dict:
    """Clipper's card. Used by the GitHub Updater's installer (Clipper is in its catalog), not by main()."""
    pythonw = str(folder / ".venv" / "Scripts" / "pythonw.exe")
    launch = str(folder / "clipper.pyw")
    return {
        "name": "Clipper",
        "command": pythonw,
        "args": [launch],
        "working_dir": str(folder),
        "icon": str(folder / "clipper.ico"),
        "needs_console": False,
        "match": ["clipper.pyw"],
        "stop_command": pythonw,
        "stop_args": [launch, "--quit"],
        "_note": "Recommended companion (github.com/Laumerz84/clipper): Medal-style instant replay, tray app; "
                 "Alt+F10 saves the last 60 s (+ a Discord-sized copy). AMD graphics cards only. Stop = --quit.",
    }


def add_entries(apps_json: Path, entries: list[dict]) -> list[str]:
    """Put `entries` into apps.json, replacing any entry with the same name. Returns what changed.
    No apps.json yet: it starts as a copy of apps.example.json, like the launcher's first start."""
    text = apps_json.read_text(encoding="utf-8-sig") if apps_json.exists() else config.skeleton_json()
    data = json.loads(text)
    apps = data.setdefault("apps", [])
    changes = []
    for entry in entries:
        same = [i for i, a in enumerate(apps) if isinstance(a, dict) and a.get("name") == entry["name"]]
        if same:
            apps[same[0]] = entry
            changes.append(f"updated {entry['name']}")
        else:
            apps.append(entry)
            changes.append(f"added {entry['name']}")
    tmp = apps_json.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, apps_json)
    return changes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Add UtilityBelt, UI Report Tool, Ekxel, Codebase Dashboard and "
                                             "GitHub Updater to the App Launcher.")
    ap.add_argument("--utilitybelt", metavar="FOLDER", help="where UtilityBelt is cloned")
    ap.add_argument("--ui-report-tool", metavar="FOLDER", help="where UI Report Tool is cloned")
    ap.add_argument("--ekxel", metavar="FOLDER", help="where Ekxel (the sheets repo) is cloned")
    ap.add_argument("--codebase-dashboard", metavar="FOLDER", help="where Codebase Dashboard is cloned")
    ap.add_argument("--updater", metavar="FOLDER", help="where GitHub Updater is cloned")
    ap.add_argument("--apps-json", metavar="FILE", default=str(HERE / "apps.json"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    belt = Path(args.utilitybelt) if args.utilitybelt else find_sibling(UTILITYBELT_NAMES, "belt.py", HERE)
    report = Path(args.ui_report_tool) if args.ui_report_tool else find_sibling(UI_REPORT_NAMES, "launch.pyw", HERE)
    ekxel = Path(args.ekxel) if args.ekxel else find_sibling(EKXEL_NAMES, EKXEL_MARKER, HERE)
    dash = Path(args.codebase_dashboard) if args.codebase_dashboard \
        else find_sibling(DASHBOARD_NAMES, "dashboard.py", HERE)
    updater = Path(args.updater) if args.updater else find_sibling(UPDATER_NAMES, "updater.py", HERE)
    entries, notes = [], []
    if belt and (belt / "belt.py").is_file():
        entries.append(utilitybelt_entry(belt.resolve()))
    else:
        notes.append("UtilityBelt not found - clone https://github.com/Laumerz84/utilitybelt.git next to this "
                     "folder or pass --utilitybelt FOLDER.")
    if report and (report / "launch.pyw").is_file():
        report = report.resolve()
        entries.append(ui_report_entry(report))
        if not (report / ".venv" / "Scripts" / "pythonw.exe").is_file():
            notes.append(f"UI Report Tool is not set up yet: double-click setup.bat in {report} once.")
    else:
        notes.append("UI Report Tool not found - clone https://github.com/Laumerz84/ui-report-tool.git next to "
                     "this folder or pass --ui-report-tool FOLDER.")

    if ekxel and (ekxel / EKXEL_MARKER).is_file():
        ekxel = ekxel.resolve()
        entries.append(ekxel_entry(ekxel))
        if not (ekxel / ".venv" / "Scripts" / "pythonw.exe").is_file():
            notes.append(f"Ekxel is not set up yet: run setup.bat in {ekxel} once.")
    else:
        notes.append("Ekxel not found - clone https://github.com/Laumerz84/sheets.git next to this folder "
                     "or pass --ekxel FOLDER.")

    if dash and (dash / "dashboard.py").is_file():
        entries.append(dashboard_entry(dash.resolve()))
    else:
        notes.append("Codebase Dashboard not found - clone https://github.com/Laumerz84/codebase-dashboard.git "
                     "next to this folder or pass --codebase-dashboard FOLDER.")

    if updater and (updater / "updater.py").is_file():
        entries.append(updater_entry(updater.resolve()))
    else:
        notes.append("GitHub Updater not found - clone https://github.com/Laumerz84/updater.git next to this "
                     "folder or pass --updater FOLDER.")

    if entries:
        for change in add_entries(Path(args.apps_json), entries):
            print(change)
    for note in notes:
        print(note)
    return 0 if entries else 1


if __name__ == "__main__":
    sys.exit(main())
