"""Bring the whole water-group stack up with one command.

An agent that is told to "go water the group" runs this first and then only has
to write replies. It is idempotent: healthy stages are reported, missing ones are
started in dependency order — NapCat binds the OneBot ports, the backend reads
from them, the supervisor keeps both alive and restarts whatever dies.

Run with `--status` to look without touching anything.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
CONFIG = ROOT / "water-group" / "config.json"

NAPCAT_PORTS = (43000, 43001)
BACKEND_PORTS = (47860, 48777)

# Detach so the stack outlives the agent turn that started it.
DETACHED = 0x00000008 if os.name == "nt" else 0

# The host proxy intercepts loopback calls — both httpx and urllib honour it — and
# NapCat only answers on 127.0.0.1. Strip it for everything started from here.
CHILD_ENV = {k: v for k, v in os.environ.items()
             if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")}
CHILD_ENV["NO_PROXY"] = "127.0.0.1,localhost"


def python_exe():
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.8):
            return True
    except OSError:
        return False


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def onebot_token():
    return config().get("onebot_token", "")


def login_info():
    """(account, nickname) from OneBot, or None when NapCat is not answering.

    Port open is not enough: the port stays bound while the renderer behind it is
    already wedged, so only an HTTP answer proves the bridge is alive.
    """
    body = json.dumps({}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:43000/get_login_info", data=body,
        headers={"Authorization": f"Bearer {onebot_token()}",
                 "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if data.get("status") != "ok":
        return None
    info = data.get("data") or {}
    return info.get("user_id"), info.get("nickname")


def supervisor_pids():
    """PIDs of a live supervisor. tasklist cannot show command lines, WMI can."""
    if os.name != "nt":
        out = subprocess.run(["pgrep", "-f", "daemon/supervisor.py"],
                             capture_output=True)
        return [line.strip() for line in out.stdout.decode().splitlines() if line.strip()]
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              "Where-Object { $_.CommandLine -like '*supervisor.py*' } | "
              "ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                              "-Command", script], capture_output=True, timeout=30)
        return [line.strip() for line in out.stdout.decode("utf-8", "ignore").splitlines()
                if line.strip().isdigit()]
    except Exception:  # noqa: BLE001
        return []


def spawn(args, log_name):
    STATE.mkdir(parents=True, exist_ok=True)
    log = open(STATE / log_name, "ab")
    return subprocess.Popen(args, cwd=str(ROOT), env=CHILD_ENV,
                            stdout=log, stderr=subprocess.STDOUT,
                            creationflags=DETACHED)


def wait_ports(ports, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if all(port_open(p) for p in ports):
            return True
        time.sleep(2)
    return all(port_open(p) for p in ports)


def ensure_napcat():
    if all(port_open(p) for p in NAPCAT_PORTS):
        # Ports bound but OneBot silent almost always means NapCat is mid-push or
        # mid-reconnect, not dead. Re-injecting here would cost the session and the
        # cursor, so wait it out before concluding anything.
        for _ in range(3):
            if login_info():
                return True, "已在线"
            time.sleep(5)
        return False, "端口在但 OneBot 无响应（正在重连，等 30 秒再跑一次）"

    # The watchdog owns the NapCat lifecycle whenever it runs. Two launchers racing
    # to inject the same account leaves several instances sharing one user-data-dir,
    # and their GPU processes then fight until QQ dies. Wait for the watchdog to
    # finish instead of starting a second injection.
    if supervisor_pids():
        print("[1/3] NapCat 离线，守护进程会重新注入，等待…", flush=True)
        for _ in range(12):
            time.sleep(5)
            if all(port_open(p) for p in NAPCAT_PORTS) and login_info():
                return True, "守护已恢复"
        return False, "守护 60 秒内没恢复 NapCat（看 water-group/logs/napcat-watchdog.log）"

    print("[1/3] NapCat 未启动且守护未运行，直接注入…", flush=True)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from water_group import bootstrap
    try:
        ok, note = bootstrap.ensure_napcat()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return ok, note


def ensure_backend():
    if all(port_open(p) for p in BACKEND_PORTS):
        return True, "已在线"
    print("[2/3] 后端未就绪，启动 app.py…", flush=True)
    spawn([python_exe(), "app.py"], "app.log")
    ok = wait_ports(BACKEND_PORTS, 120)
    return ok, "启动成功" if ok else "端口未开放（见 .tmp/app.log）"


def ensure_supervisor():
    pids = supervisor_pids()
    if pids:
        return True, f"运行中 pid={','.join(pids)}"
    print("[3/3] 守护未运行，启动 supervisor…", flush=True)
    spawn([python_exe(), str(ROOT / "water-group" / "daemon" / "supervisor.py")],
          "supervisor.log")
    time.sleep(8)
    pids = supervisor_pids()
    return bool(pids), (f"pid={','.join(pids)}" if pids else "启动失败（见 .tmp/supervisor.log）")


def events():
    path = STATE / "wg_events.jsonl"
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def self_account():
    return str(config().get("self_account") or "")


def wake_words():
    words = config().get("self_names") or []
    return [str(w) for w in words if w]


def wake_reason(event):
    """Why this message should wake an agent: an explicit @, or our name in the text.

    The server already applies the same three rules (at / reply-to-self / keyword)
    before it returns a batch; repeating them here lets the report say which
    messages still deserve a reply without a second round trip.
    """
    if event.get("is_self"):
        return ""
    account = self_account()
    for seg in event.get("segments") or []:
        if seg.get("type") == "at" and str(seg.get("qq")) == account:
            return "at"
    text = event.get("content") or ""
    for word in wake_words():
        if word in text:
            return f"keyword:{word}"
    return ""


def heartbeat_age():
    raw = read_text(STATE / "wg_heartbeat")
    try:
        return time.time() - int(raw)
    except ValueError:
        return None


def replied_after(event_id, rows):
    """Whether the bot spoke after this message — a cheap proxy for "answered".

    The ack cursor only records what reached disk, so it cannot answer this. A
    self message appearing after the wake is the next best signal, and it keeps
    the report honest without asking the agent to maintain another file.
    """
    seen = False
    for row in rows:
        if row.get("id") == event_id:
            seen = True
            continue
        if seen and row.get("is_self"):
            return True
    return False


def report():
    account, nickname = (None, None)
    info = login_info()
    if info:
        account, nickname = info

    napcat = "离线"
    if all(port_open(p) for p in NAPCAT_PORTS):
        napcat = f"在线 ({account} {nickname})" if info else "端口在，OneBot 暂未响应"
    backend = "在线" if all(port_open(p) for p in BACKEND_PORTS) else "离线"
    pids = supervisor_pids()
    guard = f"运行中 pid={','.join(pids)}" if pids else "未运行"
    age = heartbeat_age()
    if age is None:
        beat = "无心跳"
    elif age > 300:
        beat = f"{age:.0f}s 前（已卡死，supervisor 应会重启）"
    else:
        beat = f"{age:.0f}s 前（wait 周期 170s，正常）"
    rows = events()
    wakes = [e for e in rows if wake_reason(e)][-6:]

    print()
    print(f"NapCat    : {napcat}")
    print(f"后端      : {backend}  (47860 / 48777)")
    print(f"守护      : {guard}  心跳 {beat}")
    print(f"会话      : {read_text(STATE / 'wg_session.txt') or '（尚未建立）'}")
    print(f"游标      : {read_text(STATE / 'wg_ack.txt') or '0'}   事件 {len(rows)} 条")
    if wakes:
        print("最近唤醒  :")
        for e in wakes:
            stamp = time.strftime("%H:%M:%S", time.localtime((e.get("timestamp") or 0) / 1000))
            state = "已回" if replied_after(e.get("id"), rows) else "待回"
            print(f"  id={e.get('id')} {stamp} [{state}] [{wake_reason(e)}] "
                  f"{e.get('sender')}: {(e.get('content') or '')[:40]}")
    else:
        print("最近唤醒  : 无")
    print()
    print("下一步    : 对上面 id 里还没回过的，用 plan_chat_reply + send_chat_reply 回复；")
    print("            没有待回的就等 .tmp/wg_events.jsonl 新增，不要自己调 wait_chat_messages。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="一键接管水群：拉起 NapCat、后端与守护")
    parser.add_argument("--status", action="store_true", help="只报告状态，不启动任何东西")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.status:
        return report()

    ok_napcat, note_napcat = ensure_napcat()
    ok_backend, note_backend = ensure_backend()
    ok_guard, note_guard = ensure_supervisor()

    report()
    print(f"[结果] NapCat: {note_napcat} | 后端: {note_backend} | 守护: {note_guard}")
    return 0 if (ok_napcat and ok_backend and ok_guard) else 1


if __name__ == "__main__":
    sys.exit(main())
