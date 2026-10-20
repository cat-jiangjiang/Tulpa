"""Bring the whole water-group stack up with one command, and keep it up.

An agent told to "go water the group" runs this and then only has to write replies.

Why this process stays in the foreground instead of detaching: on Windows a child
started with DETACHED_PROCESS still dies with the job object of the shell that
created it, and this host kills the whole tree when a command finishes. Measured
here: both DETACHED_PROCESS and CREATE_BREAKAWAY_FROM_JOB children were gone before
they could write a single line. The only thing that reliably keeps the stack alive
is a parent that never exits, so up.py *becomes* the supervisor: it ensures NapCat,
starts the children, reports, and then blocks in the watch loop.

Usage:
  up.py            bring everything up and stay resident (run it in the background)
  up.py --status   report only, touch nothing
  up.py --detach   legacy: spawn the supervisor and return (unreliable under a job)
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
HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
CONFIG = ROOT / "water-group" / "config.json"

NAPCAT_PORTS = (43000, 43001)
BACKEND_PORTS = (47860, 48777)

# Detach so the stack outlives the agent turn that started it. Only honoured by
# --detach; the default path stays resident on purpose.
DETACHED = 0x00000008 if os.name == "nt" else 0

PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")

# Same reason as supervisor: this process can be started without a console, and
# then every probe it shells out to opens one.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def strip_proxy():
    """The host proxy intercepts loopback and NapCat only answers on 127.0.0.1.

    Mutating os.environ instead of only building a child env means every module
    imported afterwards — mcp_client, session, bootstrap — inherits a clean one.
    """
    for key in list(os.environ):
        if key.upper() in PROXY_KEYS:
            os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"


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


def self_pids():
    """Our own pid plus the redirector that launched us.

    `python.exe` in the venv is a shim: it carries the same command line as the
    interpreter it starts, so a `--status` run would otherwise find and report
    itself as a running guard.
    """
    return {str(os.getpid()), str(os.getppid())}


def _match_pids(pattern, exclude=None):
    """PIDs whose command line matches, with launcher shims collapsed.

    The venv python.exe is a redirector: it shows up in WMI with the same command
    line as the interpreter it spawns, so a naive match reports one guard as two
    pids. A pid whose parent is also matched is that second layer, so it is dropped.
    """
    if os.name != "nt":
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True)
        found = [line.strip() for line in out.stdout.decode().splitlines() if line.strip()]
        return [p for p in found if p not in exclude] if exclude else found
    script = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
              f"Where-Object {{ $_.CommandLine -match '{pattern}' }} | "
              "ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                              "-Command", script], capture_output=True, timeout=30,
                             creationflags=NO_WINDOW)
    except Exception:  # noqa: BLE001
        return []
    pairs = []
    for line in out.stdout.decode("utf-8", "ignore").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            pairs.append((parts[0], parts[1]))
    matched = {pid for pid, _ in pairs}
    found = [pid for pid, parent in pairs if parent not in matched]
    return [p for p in found if p not in exclude] if exclude else found


def supervisor_pids(exclude=None):
    """PIDs of a live guard loop: either supervisor.py or this script resident."""
    return _match_pids("supervisor\\.py|daemon[\\\\/]up\\.py", exclude)


def watchdog_pids(exclude=None):
    """PIDs of the NapCat watchdog. It is what re-injects a dead instance."""
    return _match_pids("daemon[\\\\/]watchdog\\.py", exclude)


def spawn(args, log_name):
    STATE.mkdir(parents=True, exist_ok=True)
    log = open(STATE / log_name, "ab")
    return subprocess.Popen(args, cwd=str(ROOT), env=dict(os.environ),
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

    # The watchdog owns the NapCat lifecycle whenever a guard loop runs. Two
    # launchers racing to inject the same account leaves several instances sharing
    # one user-data-dir, and their GPU processes then fight until QQ dies. Wait for
    # the watchdog instead of starting a second injection.
    #
    # The check is for watchdog.py specifically, not for the guard: at cold start
    # this process *is* the guard, so asking "is a guard running" always answers yes.
    # Injection itself measures ~65 s, so the window has to clear that comfortably.
    if watchdog_pids(exclude=self_pids()):
        print("[1/3] NapCat 离线，守护进程会重新注入，等待…", flush=True)
        for _ in range(24):
            time.sleep(5)
            if all(port_open(p) for p in NAPCAT_PORTS) and login_info():
                return True, "守护已恢复"
        return False, "守护 120 秒内没恢复 NapCat（看 water-group/logs/napcat-watchdog.log）"

    print("[1/3] NapCat 未启动且守护未运行，直接注入…", flush=True)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from water_group import bootstrap
    try:
        ok, note = bootstrap.ensure_napcat()
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return ok, note


def load_supervisor():
    """Import the sibling module whether or not this file was run as a script."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import supervisor
    return supervisor


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


def replied_ids():
    """Ids reply.py has already answered.

    The collector only delivers a batch when someone mentions the bot, so a reply
    we just sent is usually absent from wg_events.jsonl. Without this ledger the
    next agent would read the report, see [待回] on an answered message, and reply
    to it a second time.
    """
    ids = set()
    path = STATE / "wg_replied.jsonl"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                continue
    except OSError:
        pass
    return ids


def replied_after(event_id, rows, ledger=None):
    """Whether the bot spoke after this message — a cheap proxy for "answered".

    The ack cursor only records what reached disk, so it cannot answer this. The
    local ledger is the primary signal; a self message appearing later in the
    stream is the fallback for replies sent before the ledger existed.
    """
    if ledger and event_id in ledger:
        return True
    seen = False
    for row in rows:
        if row.get("id") == event_id:
            seen = True
            continue
        if seen and row.get("is_self"):
            return True
    return False


def report(exclude_pids=None, resident_pid=None):
    account, nickname = (None, None)
    info = login_info()
    if info:
        account, nickname = info

    napcat = "离线"
    if all(port_open(p) for p in NAPCAT_PORTS):
        napcat = f"在线 ({account} {nickname})" if info else "端口在，OneBot 暂未响应"
    backend = "在线" if all(port_open(p) for p in BACKEND_PORTS) else "离线"
    if resident_pid:
        guard = f"运行中 pid={resident_pid}（本进程常驻）"
    else:
        pids = supervisor_pids(exclude=exclude_pids)
        guard = f"运行中 pid={','.join(pids)}" if pids else "未运行"
    age = heartbeat_age()
    if age is None:
        beat = "无心跳"
    elif age > 300:
        beat = f"{age:.0f}s 前（已卡死，supervisor 应会重启）"
    else:
        beat = f"{age:.0f}s 前（wait 周期 170s，正常）"
    rows = events()
    ledger = replied_ids()
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
            state = "已回" if replied_after(e.get("id"), rows, ledger) else "待回"
            print(f"  id={e.get('id')} {stamp} [{state}] [{wake_reason(e)}] "
                  f"{e.get('sender')}: {(e.get('content') or '')[:40]}")
    else:
        print("最近唤醒  : 无")
    print()
    print("下一步    : 对上面 id 里还没回过的，用 plan_chat_reply + send_chat_reply 回复；")
    print("            没有待回的就等 .tmp/wg_events.jsonl 新增，不要自己调 wait_chat_messages。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="一键接管水群：拉起 NapCat、后端与守护并常驻")
    parser.add_argument("--status", action="store_true", help="只报告状态，不启动任何东西")
    parser.add_argument("--detach", action="store_true",
                        help="旧行为：后台拉起 supervisor 后立即返回（沙箱内不可靠）")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    strip_proxy()

    if args.status:
        return report(exclude_pids=self_pids())

    if args.detach:
        ok_napcat, note_napcat = ensure_napcat()
        if not all(port_open(p) for p in BACKEND_PORTS):
            spawn([python_exe(), str(HERE / "supervisor.py")], "supervisor.log")
        ok = wait_ports(BACKEND_PORTS, 120)
        report()
        print(f"[结果] NapCat: {note_napcat} | 后端: {'启动成功' if ok else '未就绪'} | "
              f"守护: 已脱离（随启动它的进程一起死）")
        return 0 if (ok_napcat and ok) else 1

    ok_napcat, note_napcat = ensure_napcat()

    supervisor = load_supervisor()
    procs = supervisor.start_all()
    print("[2/3] 后端启动中…", flush=True)
    ok_backend = wait_ports(BACKEND_PORTS, 180)
    print("[3/3] 守护循环接管中…", flush=True)

    report(resident_pid=os.getpid())
    print(f"[结果] NapCat: {note_napcat} | 后端: {'启动成功' if ok_backend else '未就绪'} | "
          f"守护: 本进程 pid={os.getpid()} 常驻")
    print("[常驻] 不要关闭这个任务；关掉它等于关掉整个水群。", flush=True)

    try:
        supervisor.supervise(procs)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
