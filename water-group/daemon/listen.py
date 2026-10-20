"""Park until there is something to do, then exit so whoever called this wakes up.

The collector writes every message to disk but only a mention wakes anyone, so
the interesting question is what to do with the quiet stretches. This script
spends them in ``watch.py --timeout`` and treats each timeout as an opening to
digest the backlog that piled up while nobody was looking.

Exiting instead of staying resident is the whole design. A background process
that never returns is invisible: it can be digesting for an hour and the agent
that started it will never know. Returning turns "something happened" into a
completion the caller actually observes.

Exit codes:
  0  there is work -- a wake was printed, or the dossier is due
  1  the event stream is unreadable, or a child could not be started
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def python_exe():
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def run(script, *args):
    """Run a sibling daemon script with the caller's stdout, and return its code."""
    return subprocess.run([python_exe(), str(HERE / script), *args], cwd=str(ROOT)).returncode


def main():
    parser = argparse.ArgumentParser(description="等消息；闲下来就沉淀积压，有事就退出叫人")
    parser.add_argument("--timeout", type=int, default=300, help="每轮静默等待多少秒")
    parser.add_argument("--cycles", type=int, default=0, help="最多循环几轮，0 表示不限（调试用）")
    args = parser.parse_args()

    cycles = 0
    while True:
        cycles += 1

        rc = run("watch.py", "--timeout", str(args.timeout))
        if rc == 0:
            print("WAKE")
            return 0
        if rc != 2:
            # 1 is "stream unreadable"; anything else means the script itself broke.
            print(f"WATCH_FAILED rc={rc}")
            return 1

        # Timed out: the group has been quiet. Rule-based digestion is free, so it
        # runs on every quiet stretch; the model-backed pass is left to whoever
        # picks up the DOSSIER_DUE below.
        run("dossier.py", "--digest")
        if run("dossier.py", "--check") == 0:
            print("DOSSIER_DUE")
            return 0

        if args.cycles and cycles >= args.cycles:
            print("NO_WORK")
            return 0


if __name__ == "__main__":
    sys.exit(main())
