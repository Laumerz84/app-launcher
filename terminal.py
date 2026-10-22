"""App Launcher, terminal edition: a console window where you type a program's name to start it.

    python terminal.py            (or the Start menu shortcut "App Launcher Terminal")

The list is apps.json (the same one the window launcher uses) plus every project folder next to
this one that has something runnable (see applauncher/discover.py), so new projects appear
without editing anything. Programs start in their own windows, exactly as the window launcher
starts them, and keep running when this window closes.
"""
from __future__ import annotations

import ctypes
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from applauncher import commands, config, discover, procs  # noqa: E402

APPS_JSON = HERE / "apps.json"
KIND_LABEL = {"listed": "apps.json", "program": "program", "app": "app", "script": "script",
              "notebook": "notebook"}

# ANSI colours; the console is switched into VT mode below.
RESET, BOLD, DIM = "\x1b[0m", "\x1b[1m", "\x1b[2m"
CYAN, GREEN, YELLOW, RED, GREY = "\x1b[36m", "\x1b[32m", "\x1b[33m", "\x1b[31m", "\x1b[90m"


def enable_vt() -> None:
    if os.name != "nt":
        return
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)   # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    kernel32.SetConsoleTitleW("App Launcher Terminal")


class Launcher:
    def __init__(self) -> None:
        self.programs: list[discover.Program] = []
        self.skipped: list[str] = []
        self.problems = ""

    def load(self) -> None:
        result = config.load_apps(APPS_JSON)
        self.problems = result.message
        listed = result.apps if result.ok else []
        root = discover.projects_root(HERE)
        self.programs, self.skipped = discover.all_programs(listed, root, exclude=[])

    def running(self) -> list:
        try:
            return procs.running_states([p.app for p in self.programs], procs.list_processes(),
                                        ignore_pid=os.getpid())
        except Exception:
            return [None] * len(self.programs)

    def show(self) -> None:
        states = self.running()
        width = max((len(p.app.name) for p in self.programs), default=10) + 2
        print(f"\n{BOLD}{CYAN}  #  Program{' ' * (width - 7)}Type{RESET}")
        for i, (p, state) in enumerate(zip(self.programs, states), 1):
            dot = f"{GREEN}●{RESET}" if state else " "
            print(f" {i:>2}  {p.app.name:<{width}}{GREY}{KIND_LABEL[p.kind]:<10}{RESET}{dot}")
        print(f"{GREY}  ● running now.  {len(self.programs)} programs", end="")
        if self.skipped:
            print(f"; nothing to run in: {', '.join(self.skipped)}", end="")
        print(RESET)
        if self.problems:
            print(f"{YELLOW}  apps.json: {self.problems}{RESET}")

    def start(self, program: discover.Program, extra: list[str]) -> None:
        app = discover.with_args(program.app, extra)
        try:
            commands.launch(commands.build_plan(app))
            print(f"{GREEN}  Started {program.app.name}{' ' + ' '.join(extra) if extra else ''}.{RESET}")
        except commands.LaunchError as exc:
            print(f"{RED}  Could not start {program.app.name}: {exc}{RESET}")

    def pick(self, text: str):
        found, candidates = discover.resolve(text, self.programs)
        if found is None:
            self.not_found(text, candidates)
        return found

    def not_found(self, text: str, candidates) -> None:
        if candidates:
            names = ", ".join(c.app.name for c in candidates)
            print(f"{YELLOW}  '{text}' could be: {names}. Type more of the name, or its number.{RESET}")
        else:
            print(f"{YELLOW}  No program called '{text}'. Type 'list' to see them.{RESET}")

    def info(self, program: discover.Program) -> None:
        try:
            plan = commands.build_plan(program.app, check=False)
            what = plan.target if plan.is_open else plan.cmdline
        except commands.LaunchError as exc:
            what = f"(cannot start: {exc})"
        print(f"  {BOLD}{program.app.name}{RESET}  {GREY}{KIND_LABEL[program.kind]}{RESET}")
        print(f"  runs:   {what}")
        if program.folder:
            print(f"  folder: {program.folder}")
        if program.app.note:
            print(f"  note:   {program.app.note}")

    def handle(self, line: str) -> bool:
        """Do what was typed. False means quit."""
        line = line.lstrip("﻿").strip()    # a byte-order mark arrives when input is piped in
        if not line:
            return True
        word, _, rest = line.partition(" ")
        cmd = word.lower()
        if cmd in ("exit", "quit", "q"):
            return False
        if cmd in ("list", "ls", "l", "dir"):
            self.show()
        elif cmd in ("refresh", "r", "reload"):
            self.load()
            print(f"{GREEN}  List refreshed.{RESET}")
            self.show()
        elif cmd in ("help", "?", "h"):
            print(HELP)
        elif cmd in ("cls", "clear"):
            print("\x1b[2J\x1b[H", end="")
        elif cmd == "edit":
            try:
                commands.open_in_editor(str(APPS_JSON))
                print(f"{GREEN}  apps.json opened in Notepad. Type 'refresh' after saving.{RESET}")
            except commands.LaunchError as exc:
                print(f"{RED}  Could not open apps.json: {exc}{RESET}")
        elif cmd in ("folder", "open", "cd") and rest.strip():
            program = self.pick(rest)
            if program:
                folder = program.folder or program.app.working_dir or os.path.dirname(program.app.command)
                try:
                    os.startfile(folder)
                except OSError as exc:
                    print(f"{RED}  Could not open the folder: {exc}{RESET}")
        elif cmd == "info" and rest.strip():
            program = self.pick(rest)
            if program:
                self.info(program)
        else:
            program, extra, candidates = discover.split_command(line, self.programs)
            if program is None:
                self.not_found(line, candidates)
            else:
                self.start(program, extra)
        return True


HELP = f"""
  {BOLD}Type a program's name or number{RESET} to start it. Spaces, capitals and punctuation do not
  matter, and the start of a name is enough when only one program fits:
      orion        rmd        3        fee displayer
  Words after the name are passed to the program:   rmd script --help

  {BOLD}list{RESET}            show the programs (● = running now)
  {BOLD}info{RESET} <name>     what a program runs and where it lives
  {BOLD}folder{RESET} <name>   open the program's folder in Explorer
  {BOLD}edit{RESET}            open apps.json in Notepad (programs outside the Python folder, or custom settings)
  {BOLD}refresh{RESET}         re-read apps.json and look for new projects
  {BOLD}cls{RESET}             clear the screen
  {BOLD}exit{RESET}            close this window (programs you started keep running)

  New projects in the Python folder appear by themselves: a dist\\*.exe, start_app.py, a Streamlit
  app.py, a main script or a notebook is enough. An apps.json entry overrides what is found.
"""


def main() -> None:
    enable_vt()
    launcher = Launcher()
    launcher.load()
    print(f"{BOLD}{CYAN}App Launcher Terminal{RESET}  {GREY}type a name to start it, 'help' for more, 'exit' to close{RESET}")
    launcher.show()
    while True:
        try:
            line = input(f"\n{CYAN}PS Launcher>{RESET} ")
        except (EOFError, KeyboardInterrupt):
            break
        if not launcher.handle(line):
            break


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        input("\nSomething went wrong (above). Press Enter to close")
