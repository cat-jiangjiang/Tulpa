"""Start the water-group stack from outside the agent sandbox.

Why this file is a ``.pyw``, and why it has to be started from Explorer:

The WorkBuddy command sandbox injects ``tsbx.dll`` into every process it spawns,
and the injection is inherited all the way down the chain. QQ.exe does not survive
it: every injected instance dies roughly twenty seconds after launch with
0xC06D007F -- the delay-load helper's ERROR_PROC_NOT_FOUND -- and the watchdog then
re-injects forever. Measured on this host: 380 of those in a single day, one crash
dialog each, which is what the black boxes on the desktop actually were.

Explorer is not sandboxed, so anything double-clicked runs clean. The extension
matters as well: ``.pyw`` is associated with pythonw.exe, which has no console
subsystem, so starting the stack does not flash a window of its own.

Double-click this after a reboot, or whenever the bot is down. It is idempotent:
if the stack is already answering it does nothing.
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
UP = ROOT / "water-group" / "daemon" / "up.py"
LOG = ROOT / "water-group" / "logs" / "launcher.log"

# DETACHED_PROCESS keeps the child from opening a console of its own; the new
# process group keeps a stray Ctrl+C in a debugger from reaching it.
DETACHED = 0x00000008 | 0x00000200


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.8):
            return True
    except OSError:
        return False


def log(line):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"[launcher] {time.strftime('%m-%d %H:%M:%S')} {line}\n")


def main():
    if not PY.exists():
        log(f"venv python missing: {PY}")
        return 1
    if port_open(43000) and port_open(47860):
        log("already running, nothing to do")
        return 0
    # Whatever is still resident was started by the agent, so it carries the
    # injection and cannot be repaired in place. Retire it before starting clean.
    subprocess.run([str(PY), str(UP), "--stop"], cwd=str(ROOT),
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   creationflags=DETACHED, timeout=180)
    time.sleep(2)
    with open(LOG, "ab") as out:
        subprocess.Popen([str(PY), str(UP)], cwd=str(ROOT),
                         stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                         creationflags=DETACHED, close_fds=True)
    log("stack started")
    return 0


if __name__ == "__main__":
    sys.exit(main())
