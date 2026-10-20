"""Supervise every long-lived piece of the water-group stack.

Children:
  db_keeper.py  holds SQLite WAL connections open so -shm is never yanked mid-read
  app.py        the Tulpa backend: Web UI on 47860, MCP endpoint on 48777
  collector.py  holds the MCP long-poll and persists every delivered message
  watchdog.py   keeps the injected QQ/NapCat instance answering

Everything runs as a child of one process so a single job object can own the whole
tree. The process that calls main() never returns, which is exactly what makes the
tree survive: on Windows a detached spawn still dies with the job of the shell that
created it, so the only reliable keeper is a parent that stays alive.

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

# app.py is started first in spirit, but db_keeper has no dependency and opening it
# early means the WAL sidecar is already pinned by the time the backend touches it.
CHILDREN = {
    "db_keeper": HERE / "db_keeper.py",
    "app": ROOT / "app.py",
    "collector": HERE / "collector.py",
    "watchdog": HERE / "watchdog.py",
}


def sanitized_env():
    """Drop the host proxy: it intercepts 127.0.0.1 and NapCat only answers there."""
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


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


def spawn(name, script):
    try:
        return subprocess.Popen(
            [PY, str(script)], cwd=str(ROOT), env=sanitized_env(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"{name} spawn failed: {exc}")
        return None


def start_all(procs=None):
    """Launch every child once, in dependency order, and hand back the pid map."""
    procs = {} if procs is None else procs
    for name, script in CHILDREN.items():
        if procs.get(name) is not None and procs[name].poll() is None:
            continue
        log(f"{name} start")
        procs[name] = spawn(name, script)
    return procs


def supervise(procs):
    """Blocking watch loop. Returns only if something catastrophic happens."""
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
            procs[name] = spawn(name, script)
        time.sleep(15)


def main():
    log(f"supervisor start, pid={os.getpid()}")
    return supervise(start_all())


if __name__ == "__main__":
    sys.exit(main())
