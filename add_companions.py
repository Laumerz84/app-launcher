"""Add the recommended companion tools to apps.json: UtilityBelt and UI Report Tool.

Both are optional. Clone them next to this folder (or anywhere, and pass the paths):

    git clone https://github.com/Laumerz84/utilitybelt.git
    git clone https://github.com/Laumerz84/ui-report-tool.git
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

HERE = Path(__file__).resolve().parent
UTILITYBELT_NAMES = ("utilitybelt",)
UI_REPORT_NAMES = ("ui-report-tool", "UI Report Tool", "ui_report_tool")


def find_sibling(names: tuple[str, ...], marker: str, base: Path) -> Path | None:
    """The first folder next to `base` with one of `names` that contains `marker`."""
    for name in names:
        folder = base.parent / name
        if (folder / marker).is_file():
            return folder
    return None


def utilitybelt_entry(folder: Path) -> dict:
    f = str(folder)
    return {
        "name": "UtilityBelt",
        "command": os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"),
        "args": ["/c", str(folder / "belt.cmd")],
        "working_dir": f,
        "icon": r"C:\Windows\System32\imageres.dll,144",
        "needs_console": True,
        "match": [str(folder / "belt.py")],
        "_note": "Recommended companion (github.com/Laumerz84/utilitybelt). Added by add_companions.py; "
                 "needs its packages once: pip install -r requirements.txt in its folder.",
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


def add_entries(apps_json: Path, entries: list[dict]) -> list[str]:
    """Put `entries` into apps.json, replacing any entry with the same name. Returns what changed."""
    data = json.loads(apps_json.read_text(encoding="utf-8")) if apps_json.exists() else {"apps": []}
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
    ap = argparse.ArgumentParser(description="Add UtilityBelt and UI Report Tool to the App Launcher.")
    ap.add_argument("--utilitybelt", metavar="FOLDER", help="where UtilityBelt is cloned")
    ap.add_argument("--ui-report-tool", metavar="FOLDER", help="where UI Report Tool is cloned")
    ap.add_argument("--apps-json", metavar="FILE", default=str(HERE / "apps.json"), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    belt = Path(args.utilitybelt) if args.utilitybelt else find_sibling(UTILITYBELT_NAMES, "belt.cmd", HERE)
    report = Path(args.ui_report_tool) if args.ui_report_tool else find_sibling(UI_REPORT_NAMES, "launch.pyw", HERE)
    entries, notes = [], []
    if belt and (belt / "belt.cmd").is_file():
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

    if entries:
        for change in add_entries(Path(args.apps_json), entries):
            print(change)
    for note in notes:
        print(note)
    return 0 if entries else 1


if __name__ == "__main__":
    sys.exit(main())
