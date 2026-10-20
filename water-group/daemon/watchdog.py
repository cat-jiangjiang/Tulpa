"""Keep the bot QQ instance online: relaunch when the injected QQ exits or hangs.

A listening-but-silent OneBot port is the failure mode seen here, so the probe is an
HTTP get_login_info call, not a TCP connect: the port stays bound while the QQ
renderer behind it is already wedged, and only the HTTP answer tells them apart.

The launcher is started through bootstrap.ensure_napcat(), which knows both the .bat
path and the direct-injection fallback for hosts where `reg query` is unavailable.
"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "water-group"))

LOG = ROOT / "water-group" / "logs" / "napcat-watchdog.log"
CONFIG = ROOT / "water-group" / "config.json"
PORT = int(os.environ.get("TULPA_ONEBOT_PORT", "43000"))

# A relaunch that keeps failing must slow down instead of hammering. Each attempt
# spawns a whole QQ process tree; when QQ dies during Chromium startup the children
# never come up, crashpad cannot finish the report because CrashSender.exe cannot be
# created, and the dead process objects stay in the table. Unthrottled, one broken
# install becomes hundreds of unreapable objects in an afternoon.
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("TULPA_WG_MAX_FAILS", "5"))
BACKOFF_BASE = int(os.environ.get("TULPA_WG_BACKOFF", "30"))
BACKOFF_MAX = 1800
ZOMBIE_LIMIT = int(os.environ.get("TULPA_WG_ZOMBIE_LIMIT", "30"))
HALT_SLEEP = 600


def onebot_token():
    token = os.environ.get("TULPA_ONEBOT_TOKEN", "").strip()
    if token:
        return token
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("onebot_token", "")
    except (OSError, ValueError):
        return ""


TOKEN = onebot_token()


def log(line):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[watchdog] {time.strftime('%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def port_open():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.8):
            return True
    except OSError:
        return False


def healthy():
    if not port_open():
        return False
    try:
        r = httpx.post(f"http://127.0.0.1:{PORT}/get_login_info",
                       headers={"Authorization": f"Bearer {TOKEN}"},
                       timeout=10, trust_env=False)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:  # noqa: BLE001
        return False


def dead_instance_count():
    """Count QQ process objects that are already dead but were never reaped.

    A process that dies before its Chromium children exist can leave an object in
    the table with zero threads and zero handles. It cannot be killed — there is
    nothing left to kill — and it disappears only once whatever holds the handle
    lets go, or after a reboot. Seeing them means the host, not the network, is
    what broke, so relaunching is pointless and actively harmful.
    """
    script = (
        "$n=0; Get-Process -Name QQ -ErrorAction SilentlyContinue | "
        "ForEach-Object { if ($_.Threads.Count -eq 0) { $n++ } }; $n"
    )
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, timeout=25)
        text = out.stdout.decode("utf-8", "ignore").strip()
        return int(text) if text.isdigit() else 0
    except Exception:  # noqa: BLE001
        return 0


def owner_pid():
    try:
        raw = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=10).stdout or b""
        out = raw.decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{PORT}") and parts[3] == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


def relaunch():
    import bootstrap

    try:
        ok, note = bootstrap.ensure_napcat()
    except Exception as exc:  # noqa: BLE001
        log(f"launch failed: {type(exc).__name__}: {exc}")
        return False
    log(f"launch -> {ok} {note}")
    return ok


def main():
    log("start")
    fails = 0
    while True:
        try:
            if healthy():
                if fails:
                    log(f"recovered after {fails} failed boot(s)")
                fails = 0
                time.sleep(10)
                continue
            time.sleep(4)
            if healthy():
                fails = 0
                continue

            dead = dead_instance_count()
            if dead >= ZOMBIE_LIMIT:
                log(f"halt: {dead} dead QQ objects never reaped, relaunching only adds more")
                time.sleep(HALT_SLEEP)
                continue
            if fails >= MAX_CONSECUTIVE_FAILURES:
                log(f"halt: {fails} consecutive boot failures, waiting for a human")
                time.sleep(HALT_SLEEP)
                continue

            pid = owner_pid()
            if pid:
                log(f"unresponsive pid={pid}, terminating")
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=20)
                time.sleep(3)
            relaunch()
            for _ in range(40):
                time.sleep(2)
                if healthy():
                    log("online")
                    fails = 0
                    break
            else:
                fails += 1
                delay = min(BACKOFF_BASE * (2 ** (fails - 1)), BACKOFF_MAX)
                log(f"boot timeout (#{fails}), backing off {delay}s")
                time.sleep(delay)
        except Exception as exc:  # noqa: BLE001
            log(f"loop error: {type(exc).__name__}: {exc}")
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
