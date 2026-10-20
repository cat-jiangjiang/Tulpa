"""Always-on waiter for the water-group session.

Holds wait_chat_messages continuously so @ mentions are captured the moment they
arrive instead of waiting for an agent to re-arm a 180 s tool call. Every delivered
message is appended to wg_events.jsonl and the newest payload lands in wg_state.json,
so a disconnected agent can replay whatever it missed.

The loop body is fully guarded: a bad payload, a write race, an MCP timeout or a
revoked grant must never kill the listener, because the agent is not what keeps it
alive. The session id lives in wg_session.txt and is rebuilt by session.ensure()
whenever the grant behind it rotates.

Control files, all inside the state directory:
  wg_ack.txt      cursor the agent has finished handling; the collector acks it next loop
  wg_pause        present while the agent is planning/sending, so the batch stays stable
  wg_heartbeat    unix time of the last completed loop, watched by supervisor.py
  wg_session.txt  active session id
"""
import json
import os
import sys
import time
from pathlib import Path

import mcp_client
import session

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
ACK = STATE_DIR / "wg_ack.txt"
PAUSE = STATE_DIR / "wg_pause"
STATE = STATE_DIR / "wg_state.json"
EVENTS = STATE_DIR / "wg_events.jsonl"
LOG = STATE_DIR / "wg_collector.log"
HEARTBEAT = STATE_DIR / "wg_heartbeat"

SESSION_ERRORS = ("denied", "不存在", "未连接", "source_unavailable")


def read_ack():
    try:
        return int(ACK.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def log(line):
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def beat():
    try:
        HEARTBEAT.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        pass


def load_seen():
    seen = set()
    if EVENTS.exists():
        for raw in EVENTS.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(raw)["id"])
            except (ValueError, KeyError):
                continue
    return seen


def bootstrap_session():
    sid = ""
    while not sid:
        try:
            sid = session.ensure()
        except Exception as exc:  # noqa: BLE001
            log(f"session bootstrap failed: {type(exc).__name__}: {exc}")
        if not sid:
            time.sleep(10)
    return sid


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    seen = load_seen()
    log(f"collector start, {len(seen)} known events, ack={read_ack()}")
    beat()
    sid = bootstrap_session()
    log(f"session {sid}")
    beat()

    while True:
        try:
            if PAUSE.exists():
                time.sleep(2)
                continue
            ack = read_ack()
            try:
                data = mcp_client.call_tool(
                    "wait_chat_messages",
                    {"session_id": sid, "timeout_seconds": 170,
                     "acknowledge_through_id": ack, "mention_only": True},
                    timeout=200,
                )
            except Exception as exc:  # noqa: BLE001
                log(f"wait failed: {type(exc).__name__}: {exc}")
                beat()
                time.sleep(5)
                continue

            error = str(data.get("error") or "")
            if error:
                log(f"wait error: {error} {data.get('error_code', '')}")
                if "read_through_id" in error:
                    # The cursor is per session: a rebuilt session starts at zero.
                    ACK.write_text("0", encoding="utf-8")
                    log("ack cursor reset for the new session")
                    beat()
                    time.sleep(2)
                    continue
                if any(token in error for token in SESSION_ERRORS):
                    try:
                        fresh = session.ensure()
                    except Exception as exc:  # noqa: BLE001
                        log(f"session rebuild failed: {type(exc).__name__}: {exc}")
                        fresh = ""
                    if fresh and fresh != sid:
                        sid = fresh
                        log(f"session switched to {sid}")
                beat()
                time.sleep(5)
                continue

            STATE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            msgs = data.get("messages") or []
            fresh = [m for m in msgs if m.get("id") not in seen]
            if fresh:
                with open(EVENTS, "a", encoding="utf-8") as fh:
                    for m in fresh:
                        seen.add(m["id"])
                        fh.write(json.dumps(m, ensure_ascii=False) + "\n")
                log(f"captured {len(fresh)} new events, read_through={data.get('read_through_id')}")
            beat()

            if msgs:
                # Keep the delivered batch stable until the agent acknowledges it.
                deadline = time.time() + 120
                while time.time() < deadline:
                    if read_ack() > ack:
                        break
                    time.sleep(2)
                beat()
            else:
                time.sleep(2)
        except Exception as exc:  # noqa: BLE001
            log(f"loop error: {type(exc).__name__}: {exc}")
            beat()
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
