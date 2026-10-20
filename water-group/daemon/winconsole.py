"""Make a console spawned by these scripts readable, or better, not spawn one.

Two separate Windows quirks, both of which show up as the same symptom -- a black
window full of garbage where a listener was supposed to run:

1. A console-less parent that spawns a console child gets a *fresh* console
   window for it. Every subprocess call therefore needs CREATE_NO_WINDOW, or
   this loop of short-lived children puts a black box on a timer.

2. A fresh console uses the OEM code page (936 on a Chinese box). Our scripts
   write UTF-8, and UTF-8 bytes read as GBK are mojibake -- not "wrong Chinese",
   unreadable garbage. SetConsoleOutputCP(65001) makes the console read them as
   UTF-8. It is a no-op when stdout is a pipe, which is the common case when an
   agent is the one running us.

`reply.py --wait` used to call subprocess.run without the flag, which is where
the reported window came from.
"""
import ctypes
import subprocess
import sys

# 0x08000000: create the process without a console window of its own. Absent on
# POSIX, hence the getattr.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def use_utf8_console():
    """Point this process's console at UTF-8, if it has a console at all."""
    if sys.platform != "win32":
        return
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return
    try:
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001  -- a console we cannot retag is cosmetic
        pass
