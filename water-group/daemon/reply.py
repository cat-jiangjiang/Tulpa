"""Send one reply to the group, and record that it went out.

The whole plan/send dance lives here so an agent that has just taken over only
needs to supply a message id and some text. Doing it by hand is where the failure
modes hide: a stale batch needs the batch id from updated_chat, an expired one
needs wg_state.json re-read, and a rejected send must not be retried blindly.

The ledger matters for handoff. The collector only delivers a batch when someone
@-mentions the bot, so a reply we just sent is usually not in wg_events.jsonl yet;
without a local record the next agent reads the status report, sees [待回] on a
message that was already answered, and replies to it twice.

Every successful send also opens a follow-up window (followup.py): the group can
keep talking for a while afterwards and watch.py still wakes on it, so a reply
does not fall on deaf ears the moment it lands. --no-followup turns that off.

Usage:
  reply.py <event_id> "<text>" [--intent "..."] [--no-quote] [--no-followup]
  reply.py --list
  reply.py --context 30 [--around <event_id>]
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mcp_client  # noqa: E402
import followup  # noqa: E402
import winconsole  # noqa: E402

ROOT = HERE.parents[1]
STATE = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
SID_FILE = STATE / "wg_session.txt"
STATE_FILE = STATE / "wg_state.json"
EVENTS = STATE / "wg_events.jsonl"
REPLIED = STATE / "wg_replied.jsonl"
DB = Path(os.environ.get("TULPA_WG_DB", ROOT / "data" / "mcp-access.sqlite3"))

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


def record(event_ids, text):
    """Ledger every event this one message answers.

    A burst of @-mentions usually gets one reply ("@A 和 @B 都别急"), and all of
    them have to be marked or the next agent re-answers the ones left out.
    """
    if isinstance(event_ids, int):
        event_ids = [event_ids]
    STATE.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    with open(REPLIED, "a", encoding="utf-8") as fh:
        for event_id in event_ids:
            fh.write(json.dumps({"id": event_id, "ts": now, "text": text},
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


def _config():
    try:
        return json.loads((ROOT / "water-group" / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def onebot_config():
    data = _config()
    return str(data.get("onebot_http_url") or "").rstrip("/"), str(data.get("onebot_token") or "")


def self_account():
    return str(_config().get("self_account") or "")


# 只有写在花括号里的名字才算 @：群里正文的「@时月」是普通文字，真正的 @ 是消息段，
# 混为一谈会把随口一提变成一次点名。
AT_TOKEN = re.compile(r"@\{([^{}\n]{1,24})\}")


def event_target(event_id):
    """(group_id, onebot_message_id) for an event the collector already stored."""
    for row in read_events():
        if row.get("id") != event_id:
            continue
        parts = str(row.get("conversation_id") or "").split(":")
        if len(parts) == 3 and parts[1] == "group" and row.get("onebot_message_id"):
            return parts[2], str(row["onebot_message_id"])
        return None
    # 追问唤醒要回的是被 mention_only 滤掉的闲聊，wg_events.jsonl 里没有它，
    # 只能回库拿编号——不然引用挂不上，群号也只能靠猜。
    return followup.lookup(event_id)


_ROSTER = {}


def group_members(group_id):
    """Live member list for one group: [{"qq", "card", "nickname"}].

    Memoised per process: one reply can carry several @-mentions, and the roster
    is a fat JSON list that costs a round trip each time.
    """
    if group_id in _ROSTER:
        return _ROSTER[group_id]
    base, token = onebot_config()
    if not base or not group_id:
        return []
    try:
        resp = requests.post(f"{base}/get_group_member_list",
                             json={"group_id": int(group_id)},
                             headers={"Authorization": f"Bearer {token}"}, timeout=15)
        rows = resp.json().get("data") or []
    except (requests.RequestException, ValueError) as exc:
        print(f"  member list unavailable: {type(exc).__name__}: {exc}")
        return []
    _ROSTER[group_id] = [{"qq": str(r.get("user_id") or ""),
                          "card": (r.get("card") or "").strip(),
                          "nickname": (r.get("nickname") or "").strip()} for r in rows]
    return _ROSTER[group_id]


def dossier_matches(raw):
    """(exact, loose) profiles from the memory DB; empty pair when it is absent.

    The dossier is a nice-to-have here: @-ing still works off the live roster, so
    a locked or missing database must not take the whole reply down with it.
    """
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from water_group.memory_engine import get_memory_engine

        return get_memory_engine().match_profiles(raw)
    except Exception as exc:  # noqa: BLE001
        print(f"  dossier lookup failed: {type(exc).__name__}: {exc}")
        return [], []


def resolve_member(name, group_id):
    """花名 -> (qq, label), or (None, 原因).

    A bare number is taken as the QQ itself -- the one input that cannot be
    ambiguous. Everything else is tried against the live roster first and the
    dossier second: the group card is how people actually address each other in
    here ("时之锁-肠宝盒"、"坏乖😋"), the dossier is what survives a rename and
    holds the shorthand people type ("时月"、"孩董"). Each tier is tried exact
    before loose, so a full name never loses to somebody else's substring.

    A name that fits more than one person is reported, not guessed. @-ing the
    wrong person reads as a mistake by the bot in a way that a missed @ does not.
    """
    raw = (name or "").strip().lstrip("@")
    if not raw:
        return None, "空名字"

    members = group_members(group_id)
    in_group = {m["qq"]: m for m in members}
    roster_known = bool(members)
    me = self_account()

    def label_of(qq):
        mate = in_group.get(qq)
        return ((mate["card"] or mate["nickname"]) if mate else "") or qq

    if raw.isdigit():
        if roster_known and raw not in in_group:
            # A number nobody in the group answers to is not an @, it is a typo.
            return None, f"{raw} 不在本群"
        if raw == me:
            return None, "是我自己"
        return raw, label_of(raw)

    # 档案里有、但不在本群的人：不能 @，可往往正是用户记错的那个，值得单独说一句。
    elsewhere = []

    def from_dossier(profs):
        out = []
        for p in profs:
            qq = str(p.get("user_id") or "")
            if not qq:
                continue
            if roster_known and qq not in in_group:
                elsewhere.append((qq, str(p.get("primary_name") or qq)))
                continue
            out.append((qq, label_of(qq) if qq in in_group else str(p.get("primary_name") or qq)))
        return out

    exact, loose = dossier_matches(raw)
    tiers = [("本群名片", [(m["qq"], label_of(m["qq"])) for m in members
                          if raw in (m["card"], m["nickname"])]),
             ("档案", from_dossier(exact))]
    if len(raw) >= 2:
        tiers.append(("本群名片近似", [(m["qq"], label_of(m["qq"])) for m in members
                                      if raw in m["card"] or raw in m["nickname"]]))
        tiers.append(("档案近似", from_dossier(loose)))

    for kind, cands in tiers:
        if len(cands) == 1:
            qq, label = cands[0]
            if qq == me:
                return None, "是我自己"
            return qq, label
        if len(cands) > 1:
            shown = "、".join(f"{label}({qq})" for qq, label in cands[:5])
            return None, f"{kind}里有 {len(cands)} 个：{shown}"

    if elsewhere:
        qq, label = elsewhere[0]
        return None, f"档案里是 {label}({qq})，但不在本群"
    return None, "没找到"


def _tidy(segments):
    """Fold neighbouring text runs into one segment.

    QQ renders two adjacent text segments exactly like one, so this is only about
    the payload being readable in `get_msg` output and in the logs -- which is
    where anyone debugging a missing @ will be looking.
    """
    out = []
    for seg in segments:
        if seg["type"] == "text" and out and out[-1]["type"] == "text":
            out[-1]["data"]["text"] += seg["data"]["text"]
        else:
            out.append(seg)
    return out


def build_segments(text, group_id, leading=()):
    """Turn `@{花名}` into real at segments, keeping everything else as text.

    An unresolved name is left as literal text rather than dropped: the reply is
    usually time-sensitive, and a name that only renders as "@某某" is a smaller
    loss than a message that never goes out.
    """
    segments = []
    for name in leading:
        qq, label = resolve_member(name, group_id)
        if qq:
            segments.append({"type": "at", "data": {"qq": qq}})
            segments.append({"type": "text", "data": {"text": " "}})
            print(f"  at: {name} -> {qq} ({label})")
        else:
            print(f"  at: {name} 未解析（{label}）")
            segments.append({"type": "text", "data": {"text": f"@{name} "}})

    pos = 0
    for hit in AT_TOKEN.finditer(text or ""):
        if hit.start() > pos:
            segments.append({"type": "text", "data": {"text": text[pos:hit.start()]}})
        name = hit.group(1)
        qq, label = resolve_member(name, group_id)
        if qq:
            segments.append({"type": "at", "data": {"qq": qq}})
            print(f"  at: {name} -> {qq} ({label})")
        else:
            print(f"  at: {name} 未解析（{label}），按文字发出")
            segments.append({"type": "text", "data": {"text": f"@{name}"}})
        pos = hit.end()
    if pos < len(text or ""):
        segments.append({"type": "text", "data": {"text": text[pos:]}})
    return _tidy(segments)


def onebot_send(group_id, segments, message_id=None):
    """Send raw OneBot segments, with a native quote in front when asked.

    OneBot takes the reply segment directly, which is what the plan path compiles
    down to anyway, so this lands the quote on the right message no matter how many
    batches have rolled over in between.
    """
    base, token = onebot_config()
    if not base or not token:
        return False
    payload = list(segments)
    if message_id:
        payload = [{"type": "reply", "data": {"id": message_id}}, *payload]
    try:
        resp = requests.post(f"{base}/send_group_msg",
                             json={"group_id": int(group_id), "message": payload},
                             headers={"Authorization": f"Bearer {token}"}, timeout=20)
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"  send failed: {type(exc).__name__}: {exc}")
        return False
    ok = data.get("retcode") == 0
    print(f"  send: retcode={data.get('retcode')} "
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


def active_session_id():
    """The session the collector is holding, read from the database.

    wg_session.txt can lag behind a rebuilt session, and the context query has to
    follow whichever session the messages were actually written under.
    """
    try:
        db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return ""
    try:
        row = db.execute(
            "SELECT id FROM chat_sessions WHERE active=1 ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        db.close()
    return row[0] if row else ""


def recent_chatter(limit, around=None):
    """Every message the group sent, not just the ones that woke us.

    mention_only keeps ordinary chatter out of wg_events.jsonl, so the daemon
    files cannot answer "what were they talking about just now". chat_inbox can:
    the backend stores each event before deciding whether to deliver it.
    """
    sid = active_session_id() or session_id()
    if not sid:
        return []
    try:
        db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    sql = "SELECT id, payload FROM chat_inbox WHERE session_id=?"
    params = [sid]
    if around:
        sql += " AND id<=?"
        params.append(int(around))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    try:
        rows = db.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    out = []
    for event_id, payload in reversed(rows):
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if data.get("synthetic"):
            continue
        out.append({
            "id": event_id,
            "sender": data.get("sender") or "",
            "text": " ".join((data.get("content") or "").split()),
            "ts": data.get("timestamp"),
            "self": bool(data.get("is_self")),
            "recalled": bool(data.get("recalled")),
        })
    return out


def show_context(limit, around=None):
    """Print recent chatter so a reply can follow the thread, not just the ping.

    A wake line says "someone @'d you". It does not say what the group had been
    talking about for the last twenty minutes, and replying off the ping alone is
    how a bot answers a question that was already settled three messages later.
    """
    import up

    rows = recent_chatter(limit, around)
    if not rows:
        print("读不到聊天记录：会话没建立，或数据库里还没有这批消息")
        return 1
    wakes = {e.get("id") for e in up.events() if up.wake_reason(e)}
    span = f"{around} 之前" if around else "最近"
    print(f"# {span} {len(rows)} 条（含闲聊，不只是 @）")
    for row in rows:
        stamp = time.strftime("%H:%M:%S", time.localtime((row["ts"] or 0) / 1000))
        who = "我" if row["self"] else (row["sender"] or "?")
        text = row["text"]
        if len(text) > 120:
            text = text[:120] + "…"
        marks = []
        if row["recalled"]:
            marks.append("撤回")
        if row["id"] in wakes:
            marks.append("唤醒")
        if around and row["id"] == int(around):
            marks.append("回这条")
        tail = f"  <- {'、'.join(marks)}" if marks else ""
        print(f"{row['id']} {stamp} {who}: {text}{tail}")
    return 0


def latest_group_id():
    for row in reversed(read_events()):
        parts = str(row.get("conversation_id") or "").split(":")
        if len(parts) == 3 and parts[1] == "group":
            return parts[2]
    # 一条唤醒都没有的时候（追问窗口里的闲聊不会进 wg_events），退回全量库问。
    return followup.latest_group_id()


def unanswered(answered):
    """Every wake still owed a reply, minus the ones this reply already covers.

    The forward-only check was wrong in the exact case it existed for: answer the
    newest of a burst and the earlier ones stay pending, but because their ids are
    smaller nothing warns about them -- which is how "two @s, one answer" looked
    like success. Both directions have to be checked.
    """
    import up

    rows = up.events()
    ledger = up.replied_ids()
    done = set(answered)
    return [e for e in rows
            if up.wake_reason(e)
            and (e.get("id") or 0) not in done
            and not up.replied_after(e.get("id"), rows, ledger)]


def open_followup(event_id, wanted=True):
    """发送成功后开追问窗口，让话题继续；wanted=False 或配置关了就不开。"""
    if not wanted or not followup.enabled():
        return None
    state = followup.open(event_id)
    print(f"[追问] 窗口已开：还可跟 {state['budget']} 条，"
          f"静默 {state['idle_seconds']}s 后收回")
    return state


def wait_for_next(seconds):
    """Park in the listener loop right after sending, and pass its output through.

    Re-hanging by hand leaves a window between the send and the next listen.py
    in which @-mentions land in the file with nobody watching. That window is a
    whole agent turn -- tens of seconds -- and it is exactly where a burst gets
    lost. Starting the loop here, in the process that just sent, closes it to a
    few hundred milliseconds.

    `--cycles 1 --no-digest` bounds it: listen.py otherwise loops forever, and a
    tool call that never returns cannot be told apart from a hang. The digest
    pass is skipped because it takes tens of seconds on a backlog, which would
    make "--wait 6" take a minute.
    """
    import up

    if up.listener_pids():
        print("已有监听在跑，不再重复挂")
        return 0
    print(f"等下一次唤醒（最多 {seconds}s）…", flush=True)
    cmd = [sys.executable, str(HERE / "listen.py"), "--timeout", str(seconds),
           "--cycles", "1", "--no-digest"]
    # Without this the child gets a console window of its own, and a fresh
    # console reads our UTF-8 as the OEM code page. Both were reported.
    return subprocess.run(cmd, cwd=str(ROOT),
                          creationflags=winconsole.NO_WINDOW).returncode


def main():
    parser = argparse.ArgumentParser(description="回一条群消息并记账")
    parser.add_argument("target", nargs="?", help="要回复的消息 id（必填，偷懒省略会直接报错）")
    parser.add_argument("text", nargs="?", help="回复正文；@ {花名} 会变成真正的 @")
    parser.add_argument("--intent", default="回复群友", help="简短沟通意图")
    parser.add_argument("--no-quote", action="store_true", help="不带引用")
    parser.add_argument("--at", action="append", default=[], metavar="花名",
                        help="在正文前面 @ 某人，可重复")
    parser.add_argument("--also", type=int, action="append", default=[], metavar="ID",
                        help="同一条回复顺带算作回了这些消息（连发的 @ 用一条答完）")
    parser.add_argument("--wait", type=int, default=0, metavar="秒",
                        help="发完就地等下一条唤醒，不用退出后另外重挂监听")
    parser.add_argument("--no-followup", action="store_true",
                        help="这次不开追问窗口（默认回复后会继续听群里聊一阵）")
    parser.add_argument("--who", metavar="花名", help="只解析名字到 QQ，不发送")
    parser.add_argument("--group", help="配合 --who；省略则取最近一条事件所在的群")
    parser.add_argument("--list", action="store_true", help="只列出 @ 过我的消息")
    parser.add_argument("--context", type=int, metavar="N",
                        help="列出最近 N 条聊天（含闲聊），回之前先看群里在聊什么")
    parser.add_argument("--around", type=int, metavar="ID",
                        help="配合 --context：只看这条之前的部分")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        # Line buffered even when piped: --wait parks in a child process, and if
        # the caller's timeout kills the group mid-wait, block buffering throws
        # away the SENT line along with it -- leaving no way to tell whether the
        # message went out.
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    winconsole.use_utf8_console()

    if args.context is not None:
        return show_context(args.context, args.around)
    if args.list:
        return list_wakes()
    if args.who:
        qq, label = resolve_member(args.who, args.group or latest_group_id())
        print(f"{args.who} -> {qq or '未解析'}（{label}）")
        return 0 if qq else 1
    if not args.target or not args.text:
        parser.error("需要 <event_id> 和 <text>")

    sid = session_id()
    if not sid:
        print("没有会话 id，先跑 up.py 让 collector 建立会话")
        return 1

    target = int(args.target)
    found = event_target(target)
    group_id = found[0] if found else (args.group or latest_group_id())
    if not group_id:
        print("不知道该发到哪个群：事件里没有 group 信息")
        return 1

    segments = build_segments(args.text, group_id, leading=args.at)
    has_at = any(s["type"] == "at" for s in segments)
    answered = [target, *[i for i in args.also if i != target]]
    print(f"target={target} group={group_id} session={sid}")

    # The listener is dead between the wake and the re-hang, so @-mentions that
    # land while this reply is being written are only visible in the file. Saying
    # so here is what keeps a burst from being answered by its first message
    # alone: by the time anyone looks again, those ids would read as [已回].
    # Both directions matter -- answering the *newest* of a pair leaves the older
    # one silent with no warning at all, which is the more common shape.
    missing = unanswered(answered)
    if missing:
        ids = "、".join(f"{e.get('id')}" + ("（更早）" if (e.get('id') or 0) < target else "")
                        for e in missing)
        print(f"  NOTE 还有 {len(missing)} 条没回：{ids}")
        print("       一条答得完就加 --also，答不完就先回这条再接着回")

    # Quote through OneBot first: it is the only path that can point at a message
    # from an older batch, which is the normal case once the group is chatting.
    # It is also the only path that can carry an at segment -- plan/send speaks
    # plain text, so a message with an @ must not be handed to it.
    if has_at or (not args.no_quote and found):
        message_id = None if args.no_quote else (found[1] if found else None)
        if onebot_send(group_id, segments, message_id):
            record(answered, args.text)
            print("SENT")
            open_followup(target, not args.no_followup)
            return wait_for_next(args.wait) if args.wait else 0
        if has_at:
            print("SEND_FAILED: 带 @ 的消息只能走 OneBot，计划通道会把它降级成纯文本")
            return 1
        print("  falling back to the plan path")
    elif not args.no_quote:
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
        record(answered, args.text)
        if final_target != target:
            print(f"note: batch moved, plan target was {final_target}")
        print("SENT")
        open_followup(final_target, not args.no_followup)
        return wait_for_next(args.wait) if args.wait else 0
    print("SEND_FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
