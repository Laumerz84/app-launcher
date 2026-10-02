"""A stand-in for an app's "stop_command" (the real ones are never called by the tests).

    dummy_stop.py QUITFILE [--noop] [--exit N]

Asks a dummy app (see dummy_app.py --quit-file) to quit by creating QUITFILE, then exits with
code N (default 0). With --noop it does not create the file, like an app that shrugs the request off.
"""
import sys

args = sys.argv[1:]
quit_file = args.pop(0)
code = 0
if "--exit" in args:
    i = args.index("--exit")
    code = int(args[i + 1])
    del args[i:i + 2]
if "--noop" not in args:
    with open(quit_file, "w", encoding="utf-8") as fh:
        fh.write("quit")
sys.exit(code)
