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
    while True:
        try:
            if healthy():
                time.sleep(10)
                continue
            time.sleep(4)
            if healthy():
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
                    break
            else:
                log("boot timeout")
        except Exception as exc:  # noqa: BLE001
            log(f"loop error: {type(exc).__name__}: {exc}")
            time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
