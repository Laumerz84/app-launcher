"""A stand-in for a real app, used by the tests (the real apps are never started or stopped).

    dummy_app.py MARKER [args...] [options]

Writes MARKER (JSON) describing how it was started - working folder, arguments,
whether it has a console window, its pid - then behaves according to the options:

    --sleep N           stay alive N seconds after writing (default: exit straight away)
    --late-marker PATH  write PATH once the sleep is over (proves it was alive that long)
    --window close      show a (fully transparent) top-level window; WM_CLOSE makes the app exit
    --window ignore     same window, but it ignores WM_CLOSE (an app that "asks about unsaved work")
    --child             start a child process, which starts a grandchild (both just sleep); their pids
                        go into the marker. Neither has the dummy's arguments on its command line.
    --quit-file PATH    exit as soon as PATH exists (what a dummy stop_command triggers)

Every other argument is just recorded, so entries can differ only by, say, a --mini flag.
"""
import ctypes
import json
import os
import subprocess
import sys
import time

args = sys.argv[1:]
marker = args.pop(0)


def take(flag, has_value=True):
    """Remove FLAG (and its value) from args; return the value (True for bare flags), or None."""
    if flag not in args:
        return None
    i = args.index(flag)
    if not has_value:
        del args[i]
        return True
    value = args[i + 1]
    del args[i:i + 2]
    return value


late = take("--late-marker")
sleep = float(take("--sleep") or 0)
window_mode = take("--window")
want_child = take("--child", has_value=False)
quit_file = take("--quit-file")

info = {}
CREATE_NO_WINDOW = 0x08000000
if want_child:
    grand_file = marker + ".grandchild"
    child_code = (
        "import subprocess, sys, time\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'], creationflags=0x08000000)\n"
        "open(sys.argv[1], 'w').write(str(g.pid))\n"
        "time.sleep(300)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", child_code, grand_file], creationflags=CREATE_NO_WINDOW)
    end = time.time() + 15
    while not os.path.exists(grand_file) and time.time() < end:
        time.sleep(0.05)
    time.sleep(0.1)
    info["child_pid"] = child.pid
    info["grandchild_pid"] = int(open(grand_file).read())

root = None
if window_mode:
    import tkinter
    root = tkinter.Tk()
    root.overrideredirect(True)          # no title bar, no taskbar button
    root.attributes("-alpha", 0.0)       # visible to Windows (WS_VISIBLE), invisible to the eye
    root.geometry("1x1+0+0")
    root.protocol("WM_DELETE_WINDOW", root.destroy if window_mode == "close" else (lambda: None))
    root.update()

console = ctypes.windll.kernel32.GetConsoleWindow()
info.update({
    "cwd": os.getcwd(),
    "args": args,
    "console_window": int(console or 0),
    "pid": os.getpid(),
    "exe": sys.executable,
})
tmp = marker + ".part"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(info, fh)
os.replace(tmp, marker)

deadline = time.time() + sleep


def expired():
    return time.time() >= deadline or bool(quit_file and os.path.exists(quit_file))


if root is not None:
    def tick():
        if expired():
            root.destroy()
        else:
            root.after(30, tick)

    root.after(30, tick)
    root.mainloop()                      # a real event loop: WM_CLOSE reaches WM_DELETE_WINDOW through it
else:
    while not expired():
        time.sleep(0.03)
if late:
    with open(late, "w", encoding="utf-8") as fh:
        fh.write("alive")
