# App Launcher — updater

Spec for an update check in the App Launcher. Written 16 October 2026 for whoever builds it;
nothing here is implemented yet.

The App Launcher is installed by `git clone`, so "is there a newer version" is answerable the
same way UtilityBelt already answers it. **`utilitybelt/update_check.py` is the reference
implementation** — proven, standard-library only, and fails silent. Follow its shape rather
than inventing a new one. This spec is mostly about the two things that are different here.

State of this clone at the time of writing:

| | |
|---|---|
| Remote | `origin` → `https://github.com/Laumerz84/app-launcher.git` |
| Branch | `main`, tracking `origin/main` |
| Divergence | **ahead 2, behind 4** |

That divergence is not hypothetical. It is the current state, and it is the case the UtilityBelt
version does not handle.

---

## 1. Fix apps.json first

**`apps.json` is tracked in git and is also the live configuration.** Those two facts cannot
both stand once an updater exists: every update will try to merge the upstream list over the
user's own list, and that list is the whole point of the program.

This has already happened. The working copy silently diverged from the committed one — the tools
moved from `G:\` to the OneDrive Desktop and five entries were dropped — and nobody noticed until
the test suite was run. Commit `afa60d9` recorded that drift rather than reverting it, on the
principle that the list is meant to be whatever you actually launch.

Required before the updater ships:

1. `git rm --cached apps.json` — stop tracking it. The file stays on disk.
2. Add `apps.json` to `.gitignore`.
3. Commit `apps.example.json`: the `_help` block plus one or two illustrative entries, with no
   machine-specific absolute paths.
4. On startup, if `apps.json` is missing, copy `apps.example.json` to it. `config.ensure_file`
   already does something close; extend it rather than adding a second path.
5. Fix the tests that assert the shipped list contents (see §7).

Without this the updater is actively harmful: it will offer a pull that conflicts every time.

---

## 2. applauncher/update_check.py

Port `utilitybelt/update_check.py` with the additions below. Keep: standard library only;
`GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never` so it can never block on a credential
prompt; `stdin=subprocess.DEVNULL`; `CREATE_NO_WINDOW`; a fetch timeout; and `None` for every
"cannot tell" case (no git, not a clone, detached HEAD, no upstream, offline).

```python
@dataclass
class UpdateInfo:
    behind: int               # commits on origin this copy does not have
    ahead: int                # NEW: local commits not pushed
    titles: list[str]         # newest first, up to 5
    local_titles: list[str]   # NEW: the unpushed commits, newest first
    dirty: bool               # NEW: uncommitted changes to tracked files
    conflict_paths: list[str] # NEW: files changed both locally and upstream
    folder: str

    @property
    def available(self) -> bool: ...     # behind > 0
    @property
    def clean_pull(self) -> bool: ...    # ahead == 0 and not dirty and not conflict_paths
    def commands(self) -> list[str]: ...
```

- `ahead` — `git rev-list --count {upstream}..HEAD`
- `dirty` — `git status --porcelain` limited to tracked files, non-empty
- `conflict_paths` — intersection of `git diff --name-only HEAD...{upstream}` with the paths
  touched by the unpushed commits (`git diff --name-only {upstream}...HEAD`). This is a
  prediction, not a guarantee; git may still merge them cleanly. Word it as "may conflict".

`commands()` returns `git -C "<folder>" pull` only when `clean_pull`. Otherwise it returns
nothing, and the UI explains why rather than offering a command that would leave a half-merged
working tree in front of someone who did not ask for one. There is no `requirements.txt` here —
the launcher is standard library only — so drop the `deps_changed` / pip branch.

---

## 3. In the window

The launcher is a tkinter window of app cards (`applauncher/ui.py`, `LauncherApp`). Follow what
is already there; do not introduce a second visual language.

- **Quiet when there is nothing to say.** No row, no banner, nothing, when up to date or when
  the check returned `None`. Same rule as the sounds: a notification that fires every time stops
  being read.
- **When behind:** one line under the app list, in the existing status-line position —
  `4 new changes · u for details`. Use the theme accent, not a new colour.
- **An update view**, reached by `u` or by clicking that line: the commit titles, the
  ahead/behind counts, and either the pull command with a Copy button (`clean_pull`) or an
  explanation of what is in the way. Keys: `Esc` closes, `c` copies. The existing footer hint
  spells its keys out; match it.
- **Never update by itself.** It reports, and it copies a command to the clipboard. The user
  runs the pull. UtilityBelt makes the same promise in its README.

Checking:

- in a daemon thread, never on the UI thread — a fetch can take seconds
- once at startup, then every 6 hours, matching UtilityBelt
- a failed check keeps the previous answer rather than clearing it

### When the pull is not clean

This is the current state of this clone and the likely state for anyone who has edited their own
copy, so it is the case to get right. Show, in plain words:

- `2 local changes not on GitHub`, and their titles
- which files may conflict — today, whatever the two unpushed commits touched
- that `git pull` will attempt a merge, and that resolving it belongs in a terminal, not in this
  window

Do not offer a one-click pull in this state. Do not offer `--force`, `reset --hard`, or `stash`
anywhere in this feature; a wrong guess there costs real work.

---

## 4. Entry point

`check_updates.py` in the repo root: double-clickable, prints the same report as
`python -m applauncher.update_check`, and ends with `input("Press Enter to close")`.

Not a `.cmd` and not a `.ps1`. Group Policy on this machine disables `cmd.exe` and blocks
unsigned PowerShell — which is why `run-tests.cmd` and `install-shortcut.ps1` cannot be run here,
and why `apps.json` entries call `pythonw.exe` directly. Any new entry point must be `.py`.

---

## 5. Failure modes

Each of these ends with the launcher behaving exactly as it does today, minus the update line.
None may raise, block the UI, or write to the window.

| Situation | Result |
|---|---|
| `git` not on PATH | `None` |
| not a git clone (ZIP download) | `None` |
| detached HEAD, or branch tracks nothing | `None` |
| offline, or fetch times out | `None`, keep the previous answer |
| repo went private / auth required | `None` — must not prompt |
| `origin` reachable but branch deleted | `None` |
| up to date | `behind == 0`, nothing shown |

---

## 6. Logging

The launcher already writes `launcher.log` (gitignored). One line per check at most: the outcome
and the counts. Never the remote URL with credentials in it, and no traceback for the ordinary
offline case.

---

## 7. Tests

`tests/test_update_check.py`, in the existing unittest style, run by
`python -m unittest discover -s tests`. Build real throwaway git repos under `TMP_ROOT` with
`git init` and local commits, using a second local repo as `origin` — no network, and no mocking
of `subprocess`.

Cover: up to date; behind only; ahead only; ahead and behind with an overlapping file
(`conflict_paths` non-empty, `clean_pull` false); dirty working tree; no upstream; not a repo;
`git` missing; fetch timeout. Assert `commands()` is empty whenever `clean_pull` is false.

While you are in the tests: the suite currently reports **16 failures and 11 errors out of 283**,
all from tests asserting that the shipped `apps.json` holds particular entries at particular
`G:\` paths. Once `apps.json` is untracked (§1) those assertions are meaningless. Replace them
with shape checks — the file parses, loads with no warnings, every entry has the required keys
and no unknown ones — and move the behaviour tests onto their own temp fixtures. Verified on
5 October that the Sound Settings entry causes none of those 27; they predate it.

---

## 8. Done when

- Up-to-date clone: the launcher looks exactly as it does now.
- Clone 4 behind with nothing local: the line appears, `u` shows four titles and a working pull
  command, and Copy puts it on the clipboard.
- **This clone as it stands (ahead 2, behind 4):** the line appears, the view names the two
  unpushed commits and the files that may conflict, and no pull command is offered.
- Network cable out: no line, no error, no hang.
- `python check_updates.py` prints the same facts and waits for Enter.
- `python -m unittest discover -s tests` has no failures attributable to this work.

## 9. Out of scope

Self-updating, downloading anything, writing to the repo, touching `apps.json`, a settings screen
for the check interval, and updating any program *listed in* the launcher. This feature concerns
the launcher's own copy and nothing else.
