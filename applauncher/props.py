"""The small Add app / Properties window: name, program, arguments and launch options.

Launch options are added to the end of the command line every time the app is opened (like a
game's launch options). The Options dropdown lists what the app's own script understands, found
by ``flags.discover`` without running anything.
"""
from __future__ import annotations

import os
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog
from typing import Callable, Optional

from . import commands, flags, winutil
from .commands import LaunchError
from .config import App
from .theme import Palette

FILE_TYPES = [("Programs and scripts", "*.exe *.cmd *.bat *.ps1 *.py *.pyw *.lnk *.html *.htm"),
              ("All files", "*.*")]


def args_text(args: tuple[str, ...] | str) -> str:
    return args if isinstance(args, str) else subprocess.list2cmdline(list(args))


class AppDialog:
    """Add (``app=None``) or edit one entry. ``on_save(fields)`` gets a dict of apps.json keys and
    returns an error message, or "" when it was saved (the dialog then closes)."""

    def __init__(self, parent: tk.Misc, pal: Palette, scale: float, *, app: Optional[App],
                 on_save: Callable[[dict], str], focus_options: bool = False) -> None:
        self.pal, self.s, self.app, self.on_save = pal, scale, app, on_save
        self._orig_args = args_text(app.args) if app else ""
        self._found: Optional[flags.Discovery] = None
        self._found_key: Optional[tuple] = None

        top = self.top = tk.Toplevel(parent)
        top.withdraw()
        top.title(f"{app.name} - Properties" if app else "Add app")
        top.configure(bg=pal.window)
        top.resizable(True, False)
        top.transient(parent)

        base = tkfont.nametofont("TkDefaultFont").actual("family")
        fam = "Segoe UI" if "Segoe UI" in tkfont.families(top) else base
        self.f = tkfont.Font(root=top, family=fam, size=9)
        self.f_label = tkfont.Font(root=top, family=fam, size=9, weight="bold")
        self.f_small = tkfont.Font(root=top, family=fam, size=8)
        self.f_mono = tkfont.Font(root=top, family="Consolas", size=8)

        pad = self.px(16)
        body = tk.Frame(top, bg=pal.window, padx=pad, pady=pad)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        self.name = self._field(body, 0, "Name", app.name if app else "")
        self.command = self._field(body, 2, "Program, script, file or web address", app.command if app else "",
                                   button=("Browse…", self._browse))
        self.args = self._field(body, 4, "Arguments", self._orig_args)
        self.options = self._field(body, 6, "Launch options", app.options if app else "",
                                   button=("Options ▾", None))
        self.opt_note = tk.Label(body, text="", bg=pal.window, fg=pal.muted, font=self.f_small, anchor="w",
                                 justify="left")
        self.opt_note.grid(row=8, column=0, columnspan=2, sticky="we", pady=(self.px(4), 0))

        self.console = tk.BooleanVar(value=bool(app.needs_console) if app else False)
        tk.Checkbutton(body, text="Open in its own console window (for terminal programs)", variable=self.console,
                       bg=pal.window, fg=pal.text, activebackground=pal.window, activeforeground=pal.text,
                       selectcolor=pal.card, font=self.f, anchor="w", bd=0, highlightthickness=0,
                       command=self._update_preview).grid(row=9, column=0, columnspan=2, sticky="w",
                                                          pady=(self.px(10), 0))

        tk.Label(body, text="Runs", bg=pal.window, fg=pal.text, font=self.f_label, anchor="w") \
            .grid(row=10, column=0, columnspan=2, sticky="we", pady=(self.px(12), self.px(2)))
        self.preview = tk.Label(body, text="", bg=pal.card, fg=pal.muted, font=self.f_mono, anchor="w",
                                justify="left", padx=self.px(8), pady=self.px(6), wraplength=self.px(470))
        self.preview.grid(row=11, column=0, columnspan=2, sticky="we")

        self.error = tk.Label(body, text="", bg=pal.window, fg=pal.error, font=self.f_small, anchor="w",
                              justify="left", wraplength=self.px(470))
        self.error.grid(row=12, column=0, columnspan=2, sticky="we", pady=(self.px(6), 0))

        buttons = tk.Frame(body, bg=pal.window)
        buttons.grid(row=13, column=0, columnspan=2, sticky="e", pady=(self.px(8), 0))
        self._button(buttons, "Cancel", self.close, primary=False).pack(side="right")
        self._button(buttons, "Save" if app else "Add", self.save, primary=True) \
            .pack(side="right", padx=(0, self.px(8)))

        self.opt_button.configure(command=self._show_options)
        for var in (self.command, self.args, self.options):
            var.trace_add("write", lambda *_: self._update_preview())
        self.command.trace_add("write", lambda *_: self._refresh_found())
        self.args.trace_add("write", lambda *_: self._refresh_found())
        top.bind("<Return>", lambda e: self.save())
        top.bind("<Escape>", lambda e: self.close())
        top.protocol("WM_DELETE_WINDOW", self.close)

        self._refresh_found()
        self._update_preview()
        top.update_idletasks()
        w, h = max(self.px(520), top.winfo_reqwidth()), top.winfo_reqheight()
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + self.px(40)
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        top.geometry(f"{w}x{h}+{max(0, min(x, sw - w))}+{max(0, min(y, sh - h - 40))}")
        top.deiconify()
        try:
            winutil.set_dark_title_bar(winutil.toplevel_hwnd(top), pal.dark)
        except Exception:
            pass
        top.grab_set()
        target = self.options_entry if focus_options else self.name_entry
        target.focus_set()
        target.icursor("end")

    # ------------------------------------------------------------------ building blocks
    def px(self, n: float) -> int:
        return int(round(n * self.s))

    def _field(self, body: tk.Frame, row: int, label: str, value: str,
               button: Optional[tuple[str, Optional[Callable]]] = None) -> tk.StringVar:
        p = self.pal
        tk.Label(body, text=label, bg=p.window, fg=p.text, font=self.f_label, anchor="w") \
            .grid(row=row, column=0, columnspan=2, sticky="we", pady=(self.px(10) if row else 0, self.px(3)))
        var = tk.StringVar(value=value)
        entry = tk.Entry(body, textvariable=var, bg=p.card, fg=p.text, insertbackground=p.text, relief="flat",
                         font=self.f, highlightthickness=1, highlightbackground=p.card_border,
                         highlightcolor=p.accent, selectbackground=p.accent, selectforeground=p.on_accent)
        entry.grid(row=row + 1, column=0, columnspan=1 if button else 2, sticky="we", ipady=self.px(5))
        if label == "Name":
            self.name_entry = entry
        if label == "Launch options":
            self.options_entry = entry
        if button:
            text, fn = button
            b = self._button(body, text, fn or (lambda: None), primary=False)
            b.grid(row=row + 1, column=1, sticky="ns", padx=(self.px(8), 0))
            if label == "Launch options":
                self.opt_button = b
        return var

    def _button(self, parent: tk.Misc, text: str, fn: Callable, primary: bool) -> tk.Button:
        p = self.pal
        bg, fg, active = (p.accent, p.on_accent, p.accent_hover) if primary else (p.secondary, p.text,
                                                                                   p.secondary_hover)
        return tk.Button(parent, text=text, command=fn, bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                         relief="flat", bd=0, font=self.f, padx=self.px(14), pady=self.px(5), cursor="hand2",
                         highlightthickness=0 if primary else 1, highlightbackground=p.card_border)

    # ------------------------------------------------------------------ behaviour
    def _current(self) -> App:
        return App(name=self.name.get().strip() or "app", command=self.command.get().strip(),
                   args=self.args.get().strip(), working_dir=self.app.working_dir if self.app else "",
                   needs_console=self.console.get(), options=self.options.get().strip())

    def _browse(self) -> None:
        start = os.path.dirname(os.path.expandvars(self.command.get().strip())) or os.path.expanduser("~")
        path = filedialog.askopenfilename(parent=self.top, title="Pick a program or file", filetypes=FILE_TYPES,
                                          initialdir=start if os.path.isdir(start) else None)
        if not path:
            return
        path = os.path.normpath(path)
        self.command.set(path)
        if not self.name.get().strip():
            self.name.set(os.path.splitext(os.path.basename(path))[0].replace("_", " ").replace("-", " ").title())

    def _refresh_found(self) -> None:
        key = (self.command.get().strip(), self.args.get().strip())
        if key == self._found_key:
            return
        self._found_key = key
        try:
            self._found = flags.discover(self._current())
        except Exception as exc:   # a strange script must never break the dialog
            self._found = flags.Discovery(note=f"Couldn't read the options: {exc}")
        found = self._found
        if found.flags:
            where = found.sources[-1].name if found.sources else "the script"
            text = f"{len(found.flags)} option{'s' if len(found.flags) != 1 else ''} found in {where}. " \
                   "Added to the end of the command each time the app opens."
        else:
            text = (found.note or "No options found.") + " You can still type your own."
        self.opt_note.configure(text=text)
        self.opt_button.configure(state="normal" if found.flags else "disabled")

    def _show_options(self) -> None:
        found = self._found
        if not found or not found.flags:
            return
        p = self.pal
        menu = tk.Menu(self.top, tearoff=0, bg=p.card, fg=p.text, activebackground=p.accent,
                       activeforeground=p.on_accent, font=self.f)
        present = self.options.get().split()
        for flag in found.flags:
            mark = "✓  " if any(n in present for n in flag.names) else "     "
            menu.add_command(label=mark + flag.label(), command=lambda f=flag: self._toggle(f))
        b = self.opt_button
        menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height())

    def _toggle(self, flag: flags.Flag) -> None:
        """Pick an option once to add it, again to take it out."""
        words = self.options.get().split()
        if any(n in words for n in flag.names):
            out, skip = [], False
            for i, w in enumerate(words):
                if skip:
                    skip = False
                    continue
                if w in flag.names:
                    skip = flag.takes_value and i + 1 < len(words) and not words[i + 1].startswith("-")
                    continue
                out.append(w)
            self.options.set(" ".join(out))
        else:
            text = self.options.get().rstrip()
            self.options.set((text + " " if text else "") + flag.name + (" " if flag.takes_value else ""))
        self.options_entry.focus_set()
        self.options_entry.icursor("end")

    def _update_preview(self) -> None:
        app = self._current()
        if not app.command:
            self.preview.configure(text="(pick a program first)")
            return
        try:
            plan = commands.build_plan(app, check=False)
            text = plan.target + (f"  {plan.open_args}" if plan.open_args else "") if plan.is_open else plan.cmdline
        except LaunchError as exc:
            text = f"(can't start this: {exc})"
        self.preview.configure(text=text)

    def save(self) -> None:
        name, command = self.name.get().strip(), self.command.get().strip()
        if not command:
            self.error.configure(text="Pick a program, script, file or web address first.")
            return
        if not name:
            self.error.configure(text="Give it a name - that's the text on its card.")
            return
        fields: dict = {"name": name, "command": command, "options": self.options.get().strip(),
                        "needs_console": self.console.get() or None}
        args = self.args.get().strip()
        if not self.app or args != self._orig_args:      # untouched arguments keep their original list form
            fields["args"] = args
        message = self.on_save(fields)
        if message:
            self.error.configure(text=message)
        else:
            self.close()

    def close(self) -> None:
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()
