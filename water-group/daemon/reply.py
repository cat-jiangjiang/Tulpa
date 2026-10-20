"""Send one reply to the group, and record that it went out.

The whole plan/send dance lives here so an agent that has just taken over only
needs to supply a message id and some text. Doing it by hand is where the failure
modes hide: a stale batch needs the batch id from updated_chat, an expired one
needs wg_state.json re-read, and a rejected send must not be retried blindly.

The ledger matters for handoff. The collector only delivers a batch when someone
@-mentions the bot, so a reply we just sent is usually not in wg_events.jsonl yet;
without a local record the next agent reads the status report, sees [待回] on a
message that was already answered, and replies to it twice.

Usage:
  reply.py <event_id> "<text>" [--intent "..."] [--no-quote]
  reply.py --list
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mcp_client  # noqa: E402

ROOT = HERE.parents[1]
STATE = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
SID_FILE = STATE / "wg_session.txt"
STATE_FILE = STATE / "wg_state.json"
EVENTS = STATE / "wg_events.jsonl"
REPLIED = STATE / "wg_replied.jsonl"

RETRY_SLEEP = 3
SOURCE_WAIT = 30


def session_id():
    try:
        return SID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def batch_id():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return (data.get("input_batch") or {}).get("id") or ""


def read_events():
    rows = []
    try:
        for line in EVENTS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def replied_ids():
    ids = set()
    try:
        for line in REPLIED.read_text(encoding="utf-8").splitlines():
            try:
                ids.add(json.loads(line)["id"])
            except (ValueError, KeyError):
                continue
    except OSError:
        pass
    return ids


def record(event_id, text):
    STATE.mkdir(parents=True, exist_ok=True)
    with open(REPLIED, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": event_id, "ts": int(time.time()), "text": text},
                            ensure_ascii=False) + "\n")


def plan(sid, batch, target, intent, quote):
    """Returns (plan_id, final_target); plan_id is None when planning failed.

    The final target can differ from the requested one when the batch rotated
    mid-compose. The caller needs it to decide whether quoting still makes sense:
    quoting a stranger's message to answer someone else reads as a mistake.
    """
    for attempt in range(8):
        args = {"session_id": sid, "batch_id": batch, "action": "reply",
                "expressions": ["text"], "intent": intent}
        if target:
            args["target_event_id"] = target
        data = mcp_client.call_tool("plan_chat_reply", args, 40)
        state = data.get("state")
        code = data.get("error_code") or ""
        print(f"  plan {attempt + 1}: state={state} code={code or '-'}")
        if state == "READY":
            return data.get("plan_id"), target

        if state == "SUCCEEDED":
            # Planning is keyed on (session, batch, target, intent): asking again
            # for a combination that already finished replays the old plan instead
            # of issuing a new one. That bites when the batch rotated and an earlier
            # attempt already consumed this target, and it looks like a send that
            # never happened -- the group stayed silent while this returned
            # SUCCEEDED. Nudge the intent so the server plans afresh.
            intent = f"{intent}·{attempt + 2}"
            continue

        if code in ("stale", "needs_refresh"):
            updated = data.get("updated_chat") or {}
            new_batch = (updated.get("input_batch") or {}).get("id")
            if new_batch:
                batch = new_batch
                continue
            print("  no replacement batch in updated_chat")
            return None, target
        if code == "target_not_in_batch":
            # The batch rotated while we were composing: every new message forms a
            # new batch, and only its own events are valid targets. The response
            # names the ones that are, so adopt the newest and retry the same batch
            # instead of re-reading the state file and losing another round.
            allowed = data.get("valid_event_ids") or []
            if allowed:
                new_target = allowed[-1]
                if new_target != target:
                    print(f"  batch rotated, retargeting {target} -> {new_target}")
                    target = new_target
                    continue
            print("  no valid target in this batch")
            return None, target
        if code == "batch_expired":
            fresh = batch_id()
            if fresh and fresh != batch:
                batch = fresh
                continue
            print("  batch expired and no newer one is on disk; wait for the collector")
            return None, target
        if code == "source_unavailable":
            print(f"  NapCat is reconnecting, waiting {SOURCE_WAIT}s")
            time.sleep(SOURCE_WAIT)
            continue
        if code == "chat_rejected":
            print("  the session is held by another waiter; check `up.py --status`")
            return None, target
        print("  unhandled:", json.dumps(data, ensure_ascii=False)[:300])
        return None, target
    return None, target


def send(sid, plan_id, text, quote):
    bubble = {"kind": "text", "text": text}
    if quote:
        bubble["quote"] = True
    data = mcp_client.call_tool("send_chat_reply",
                                {"session_id": sid, "plan_id": plan_id,
                                 "bubbles": [bubble], "expression_ids": []}, 45)
    state = data.get("state")
    print(f"  send: state={state} reason={data.get('reason') or '-'}")
    for b in data.get("bubbles") or []:
        print(f"    bubble {b.get('index')} {b.get('state')} "
              f"{(b.get('expression') or {}).get('text', '')}")
    return state == "SUCCEEDED"


def onebot_config():
    try:
        data = json.loads((ROOT / "water-group" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    return str(data.get("onebot_http_url") or "").rstrip("/"), str(data.get("onebot_token") or "")


def event_target(event_id):
    """(group_id, onebot_message_id) for an event the collector already stored."""
    for row in read_events():
        if row.get("id") != event_id:
            continue
        parts = str(row.get("conversation_id") or "").split(":")
        if len(parts) == 3 and parts[1] == "group" and row.get("onebot_message_id"):
            return parts[2], str(row["onebot_message_id"])
        return None
    return None


def quote_send(group_id, message_id, text):
    """Send with a native QQ quote straight through OneBot.

    The plan path cannot quote across batches. plan_chat_reply only accepts a
    target listed in the current batch, and every new group message opens a new
    batch -- so by the time a reply is composed, the message that woke us up
    usually belongs to a batch that is already gone. Quoting through the plan
    then either fails outright or, worse, points at whoever spoke last.

    OneBot takes a raw reply segment, which is exactly what the plan path
    compiles down to anyway, so this lands the quote on the right message no
    matter how many batches have rolled over in between.
    """
    base, token = onebot_config()
    if not base or not token:
        return False
    segments = [{"type": "reply", "data": {"id": message_id}},
                {"type": "text", "data": {"text": text}}]
    try:
        resp = requests.post(f"{base}/send_group_msg",
                             json={"group_id": int(group_id), "message": segments},
                             headers={"Authorization": f"Bearer {token}"}, timeout=20)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  quote send failed: {type(exc).__name__}: {exc}")
        return False
    ok = data.get("retcode") == 0
    print(f"  quote send: retcode={data.get('retcode')} "
          f"message_id={(data.get('data') or {}).get('message_id')}")
    return ok


def list_wakes():
    """Same wake rules and same answered-check as `up.py --status`.

    Importing up keeps one source of truth for "who woke us" and "did we answer".
    """
    import up

    rows = up.events()
    ledger = up.replied_ids()
    shown = 0
    for e in rows:
        reason = up.wake_reason(e)
        if not reason:
            continue
        mark = "已回" if up.replied_after(e.get("id"), rows, ledger) else "待回"
        stamp = time.strftime("%H:%M:%S", time.localtime((e.get("timestamp") or 0) / 1000))
        print(f"id={e.get('id')} {stamp} [{mark}] [{reason}] {e.get('sender')}: "
              f"{(e.get('content') or '')[:50]}")
        shown += 1
    if not shown:
        print("没有唤醒记录")
    return 0


def main():
    parser = argparse.ArgumentParser(description="回一条群消息并记账")
    parser.add_argument("target", nargs="?", help="要回复的消息 id；省略则回最新一条待回")
    parser.add_argument("text", nargs="?", help="回复正文")
    parser.add_argument("--intent", default="回复群友", help="简短沟通意图")
    parser.add_argument("--no-quote", action="store_true", help="不带引用")
    parser.add_argument("--list", action="store_true", help="只列出 @ 过我的消息")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.list:
        return list_wakes()
    if not args.target or not args.text:
        parser.error("需要 <event_id> 和 <text>")

    sid = session_id()
    if not sid:
        print("没有会话 id，先跑 up.py 让 collector 建立会话")
        return 1

    target = int(args.target)
    print(f"target={target} session={sid}")

    # Quote through OneBot first: it is the only path that can point at a message
    # from an older batch, which is the normal case once the group is chatting.
    if not args.no_quote:
        found = event_target(target)
        if found:
            if quote_send(found[0], found[1], args.text):
                record(target, args.text)
                print("SENT")
                return 0
            print("  falling back to the plan path")
        else:
            print("  no OneBot id on record for this event; using the plan path")

    batch = batch_id()
    if not batch:
        print("没有可用批次，等 collector 收下一批")
        return 1
    plan_id, final_target = plan(sid, batch, target, args.intent, not args.no_quote)
    if not plan_id:
        print("PLAN_FAILED")
        return 1
    quote = (not args.no_quote) and final_target == target
    if send(sid, plan_id, args.text, quote):
        record(target, args.text)
        if final_target != target:
            print(f"note: batch moved, plan target was {final_target}")
        print("SENT")
        return 0
    print("SEND_FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
