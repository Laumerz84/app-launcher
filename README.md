# App Launcher

One small window that starts my own tools with one click, or all of them at
once. Plain Python + tkinter, nothing to install. Everything lives in this
folder (`F:\ClodCode\launcher`).

## Recommended companions (optional)

The launcher works on its own, but it was made alongside two tools that are
worth having with it:

- **[UtilityBelt](https://github.com/Laumerz84/utilitybelt)**: a terminal dashboard
  for your PC and your Claude Code work (CPU, GPU, disks, sessions, costs).
- **[UI Report Tool](https://github.com/Laumerz84/ui-report-tool)**: a tray app that
  takes annotated screenshots and hands them to Claude Code (or anyone) in one paste.

To add them, clone them next to this folder, set them up once, then let the
launcher add itself the entries with the right paths for your PC:

```
cd ..
git clone https://github.com/Laumerz84/utilitybelt.git
git clone https://github.com/Laumerz84/ui-report-tool.git
pip install -r utilitybelt\requirements.txt
ui-report-tool\setup.bat
cd launcher
python add_companions.py
```

`add_companions.py` finds the two folders next to this one (or pass
`--utilitybelt FOLDER` / `--ui-report-tool FOLDER` if they live elsewhere), adds
a **UtilityBelt** and a **UI Report Tool** card, and replaces any old entries
with those names. Everything else in `apps.json` is kept. UI Report Tool's
**Stop** asks it to quit like its own tray Quit.

## Use it

Start menu > **App Launcher** (right-click it > **Pin to taskbar** to keep it on
the taskbar). Or run `pythonw launcher.pyw` from this folder.

| Key | Does |
| --- | --- |
| `1` - `9` | launch the app with that number (shown on each row) |
| `Shift+1` - `Shift+9` | **stop** that app: first press arms it, second press stops it (see "Stopping an app") |
| `A` | **Launch all** (starts every app that is not already running) |
| `Up` / `Down`, `Enter` | move a highlight over the rows and launch it |
| `Esc` | close the launcher |

Click a row to launch it. A green dot and the word "Running" mean the app is
running right now (checked every 3 seconds, and whenever the window is
clicked back into focus). **Edit list** opens `apps.json` in Notepad.

## Stopping an app

Every card whose app is running (the green dot) has a small **Stop** button.
Cards that cannot be detected as running (web pages) have none.

Stopping takes **two clicks**, so a stray click cannot end anything: the first
click turns the button into **Click again to stop** for 3 seconds (no dialog);
the second click stops the app. If you do nothing, it reverts. `Shift+digit`
works the same way from the keyboard (`Shift+2` twice stops row 2). The card
shows **Stopping...** while it works, then the status line says **Stopped X** or
**Could not stop X: reason**, and the green dot is refreshed at once.

How an app is stopped depends on whether its entry has a `stop_command`:

- **With `stop_command`** (and optional `stop_args`, written like `command` and
  `args`): the launcher runs that command (no console window) instead of ending
  anything itself, then watches the green-dot detection for up to 10 seconds. If
  the app is gone, done. If it is still there (for example it asked about unsaved
  work and you chose Cancel) the launcher does **not** force anything; it just
  shows "X is still open (it may be asking you something)". UI Report Tool works
  this way: its entry runs `launch.pyw --quit`, which asks the running copy to
  quit exactly like its tray **Quit** item.
- **Without `stop_command`:** a polite close first (a close request to the
  visible windows of the app's processes; if there are any, the launcher waits up
  to 2 seconds), then the app's processes and the processes they started are
  ended. A process that refuses (access denied) is reported, not hidden.

What can be ended is deliberately narrow: only processes that entry's own green
dot recognises, plus the processes they started (the parent/child tree), and never
the launcher itself, the programs it was started from, Explorer or other system
processes, or a process that another entry claims. So stopping UtilityBelt
never touches UtilityBelt Mini or anything else, and the other way round. When two
entries both match a process, the more specific one (more `match` texts, then
longer ones) owns it, so two entries that run the same script with different
arguments stay apart as long as one has a `match` for the difference. Two entries
that match a process *equally* well are a tie: both show as running, but neither
can stop it (the message says which entry it clashes with); give one a more
specific `match`.

Launched apps are fully independent of the launcher: closing it never closes
them. Terminal apps get their own new console window. If something cannot be
started (file moved, wrong path) a short message appears at the bottom of the
window and the launcher carries on.

The look follows the Windows light/dark app setting and changes live when you
switch it.

## Add, change or remove an app

Click **Edit list**, edit `apps.json`, save. The launcher reloads it by itself
(on save, and again when you click back into the window). If the file has a
mistake the previous list stays on screen and a red message says which line to
look at. An entry with a mistake is skipped and named in that message; the rest
still work.

To remove an app, delete its `{ ... }` block (mind the commas between blocks).
To add one, copy a block and change it. The first nine rows get number keys.

```json
{
  "name": "My tool",
  "command": "C:\\Tools\\mytool.exe",
  "args": ["--fast", "some file.txt"],
  "working_dir": "C:\\Tools",
  "icon": "C:\\Tools\\mytool.ico",
  "needs_console": false
}
```

Backslashes in JSON must be doubled (`\\`); forward slashes also work in most places.

| Field | Meaning |
| --- | --- |
| `name` | Text on the button. Required. |
| `command` | What to run. Required. See "What `command` can be" below. |
| `args` | List of arguments, one string each (`["/c", "C:\\x\\a.cmd"]`), or one string that is passed as-is. |
| `working_dir` | Folder to start in. Default: the folder that holds the command. |
| `icon` | `.ico`, `.exe` or `.dll` path, optionally `"file,index"` (like a shortcut's icon location). Default: the command's own icon if it is a normal program, else a coloured letter badge. |
| `needs_console` | `true` gives the app its own new console window (terminal programs). Default `false` (no console). |
| `window` | `"normal"` (default), `"minimized"` or `"hidden"`: how the first window is shown. |
| `launch_all` | `false` leaves the entry out of **Launch all** (its own button still works). Default `true`. |
| `match` | Text (or list of texts) that must all appear in a running process's command line for the green dot. Normally worked out for you; set it if the dot never lights or lights wrongly, or when two entries run the same script with different arguments (like UtilityBelt Mini, which looks for `belt.py` and `--mini`). |
| `stop_command` | How to ask the app to quit, used by the **Stop** button instead of ending processes. A program, `.cmd`, `.ps1`, ... like `command`; started with no console. After it runs the launcher waits up to 10 seconds for the app to disappear and never force-kills. Default: none (polite close, then end the app's processes). |
| `stop_args` | Arguments for `stop_command`, written like `args` (a list, or one string). |
| `_help`, `_note`, anything starting with `_` | Comments, ignored. |

The same explanation is at the top of `apps.json` itself (`_help`).

### What `command` can be

| You write | It is started as |
| --- | --- |
| a program (`.exe`, or a name on PATH such as `notepad`) | directly, with `args` |
| a `.cmd` / `.bat` file | `cmd.exe /s /c "file args"` |
| a `.ps1` file | `powershell.exe -ExecutionPolicy Bypass -File file args` (plus `-NoExit` if `needs_console`) |
| a `.py` / `.pyw` file | `python.exe` (with `needs_console`) or `pythonw.exe` |
| a web page / document / folder / `https://...` address | opened with its default app (an `.html` file opens in your default browser) |

`%USERPROFILE%`-style variables are expanded in `command`, `args`, `working_dir`.

Web pages cannot be detected as running, so those entries have no dot, and the
ones supplied here have `"launch_all": false` so **Launch all** does not open a
new browser tab each time.

## What is in the list now

Requested (they start exactly like the Start menu shortcuts they came from):
UI Report Tool, UtilityBelt, UtilityBelt Mini, Screener shell.

Suggested (found by looking in `F:\ClodCode`; delete freely): Fractal Flame,
Vortex Street, Claude Museum, UtilityBelt Dash. Each has a `_note` saying so.

## Files

| File | What |
| --- | --- |
| `launcher.pyw` | entry point (runs with no console window) |
| `apps.json` | the app list |
| `launcher.ico`, `make_icon.py` | the icon and the script that draws it (`python make_icon.py`) |
| `install-shortcut.ps1` | creates the one Start menu shortcut (`-Remove` deletes it) |
| `add_companions.py` | adds UtilityBelt and UI Report Tool to `apps.json` with this PC's paths (see "Recommended companions") |
| `applauncher/` | the code: `config` (apps.json), `commands` (launching), `procs` (running detection), `stop` (the Stop button's logic), `ui`, `gfx`, `theme`, `winutil`, `aumid` |
| `tests/`, `run-tests.cmd` | unit tests (`run-tests.cmd`, or `python -m unittest discover -s tests`) |
| `launcher.log` | a small rolling log (launches, problems with apps.json) |
| `.tmp/` | scratch space used by the tests |

## The Start menu shortcut

`powershell -File install-shortcut.ps1` creates `App Launcher.lnk` in the
Start menu Programs folder: target `pythonw.exe` (the real one, resolved from
`python`), argument `launcher.pyw`, start in this folder, icon `launcher.ico`.
It also stores an AppUserModelID in the shortcut, matching the one the running
window announces, so the pinned icon and the open window are one taskbar
button. Re-run it if Python is upgraded (the path to `pythonw.exe` changes) or
this folder is moved. `-Remove` deletes it.

## Notes

- The launcher opens one window; starting it again just brings the open one to
  the front.
- "Running" is best effort: it looks for a process whose command line contains
  the script the entry runs (or whose exe path is the program). It reads the
  process list in-process, so there is no flashing console or PowerShell.
- The only thing read from the registry is the light/dark app-theme setting.
- Nothing is written outside this folder except the Start menu shortcut.
- Smoke test: `pythonw launcher.pyw --smoke-test` builds the window, closes it
  after 2 seconds and exits 0 if nothing went wrong (see `launcher.log`).
