"""Supervise the water-group daemons so no single crash ends the watch.

Children:
  collector.py  holds the MCP long-poll and persists every delivered message
  watchdog.py   keeps the injected QQ/NapCat instance answering

The collector is also checked through its heartbeat file: a hung long-poll is not a
dead process, so process liveness alone would hide exactly the failure this guards
against.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE_DIR = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
HEARTBEAT = STATE_DIR / "wg_heartbeat"
LOG = ROOT / "water-group" / "logs" / "wg-supervisor.log"
STALE_AFTER = int(os.environ.get("TULPA_WG_STALE", "300"))
PY = sys.executable

CHILDREN = {
    "collector": HERE / "collector.py",
    "watchdog": HERE / "watchdog.py",
}


def log(line):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def heartbeat_age():
    try:
        return time.time() - int(HEARTBEAT.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 10 ** 9


def main():
    procs = {}
    log(f"supervisor start, pid={os.getpid()}")
    while True:
        for name, script in CHILDREN.items():
            proc = procs.get(name)
            dead = proc is None or proc.poll() is not None
            hung = name == "collector" and not dead and heartbeat_age() > STALE_AFTER
            if not (dead or hung):
                continue
            reason = "exited" if dead else f"heartbeat stale {heartbeat_age():.0f}s"
            if proc is not None and proc.poll() is None:
                proc.kill()
                time.sleep(2)
            log(f"{name} restart ({reason})")
            try:
                procs[name] = subprocess.Popen(
                    [PY, str(script)], cwd=str(ROOT),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"{name} spawn failed: {exc}")
                procs[name] = None
        time.sleep(15)


if __name__ == "__main__":
    sys.exit(main())
