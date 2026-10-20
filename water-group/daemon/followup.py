"""追问窗口：回复之后继续听群里说什么，没人接话就收回。

`mention_only` 让 collector 只投递 @ 和关键词，所以「回复之后群里又聊了什么」
在 wg_events.jsonl 里根本不存在。但 chat_inbox 里有全部消息——后端在决定要不要
推送之前就已经把它们写进库了。追问窗口就走这条路：回复成功后开窗，watch.py 每轮
顺带查一眼窗口之后的新消息，有闲聊就地唤醒，让话题接着往下走；攒满
`max_messages` 条、或者静默超过 `idle_seconds` 秒没人接话，窗口自己关掉，回到只等
@ 和关键词的状态。

窗口期间不动 collector：它继续只投递 @，追问读的是全量库。两条唤醒路径各走各的
——@ 唤醒看 wg_events.jsonl，追问唤醒看 chat_inbox——互不覆盖，也就不会有
「collector 正卡在 170 秒长轮询里，改 mention_only 要等它返回才生效」的竞态。

状态文件（TULPA_WG_STATE 下）：
  wg_followup.json  当前窗口；文件不存在就是没开窗
字段：after_id（从这条之后开始看，通常就是刚回复的那条）、session（所属会话，
换会话即失效，防会话重建后 id 空间对不上）、opened_at、until（静默截止）、
budget（还能跟几条，自己被自己说的话不占额度）、max_messages、idle_seconds、wakes。

Usage:
  followup.py --show   打印当前窗口状态（调试用）
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STATE = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
DB = Path(os.environ.get("TULPA_WG_DB", ROOT / "data" / "mcp-access.sqlite3"))
CONFIG = ROOT / "water-group" / "config.json"
WINDOW = STATE / "wg_followup.json"

# 跟不跟、跟多少、跟多久，都可以在 config.json 的 followup 段里改。
DEFAULTS = {"enabled": True, "max_messages": 10, "idle_seconds": 180}


def settings():
    """Config 的 followup 段，缺项用代码默认值补齐。"""
    data = dict(DEFAULTS)
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        raw = cfg.get("followup") or {}
        for key in DEFAULTS:
            if key in raw:
                data[key] = raw[key]
    except (OSError, ValueError):
        pass
    return data


def enabled():
    return bool(settings().get("enabled", True))


def load():
    try:
        data = json.loads(WINDOW.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    # 写一半的脏文件可能是 list/str；watch.py 的循环没有 try 兜底，
    # 这种窗口宁可当没有，也不能把监听打死。
    return data if isinstance(data, dict) else None


def save(state):
    STATE.mkdir(parents=True, exist_ok=True)
    WINDOW.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def close():
    try:
        WINDOW.unlink()
    except OSError:
        pass


def active_session_id():
    """当前会话 id，读库不读 wg_session.txt——后者会滞后于会话重建。"""
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


def open(after_id, max_messages=None, idle_seconds=None, session=None):
    """回复成功后开窗。after_id 通常就是刚回复的那条消息。

    故意不用「当前最新消息 id」当起点：发送过程中群里照旧在说话，拿最新 id 会把
    这几句吞掉。从被回复的那条往后看，它们才算「回复之后群里说的」。
    """
    cfg = settings()
    if max_messages is None:
        max_messages = int(cfg["max_messages"])
    if idle_seconds is None:
        idle_seconds = int(cfg["idle_seconds"])
    now = time.time()
    state = {
        "after_id": int(after_id),
        "session": session or active_session_id(),
        "opened_at": now,
        "until": now + float(idle_seconds),
        "budget": int(max_messages),
        "max_messages": int(max_messages),
        "idle_seconds": int(idle_seconds),
        "wakes": 0,
    }
    save(state)
    return state


def stale(window, now=None):
    """窗口为什么该关了：None 表示还开着。"""
    if not window:
        return "gone"
    now = time.time() if now is None else now
    if window.get("session") and window["session"] != active_session_id():
        # 会话重建过，after_id 指的是旧会话的编号，接着看会对错人。
        return "session"
    if window.get("budget", 0) <= 0:
        return "budget"
    if now >= float(window.get("until") or 0):
        return "idle"
    return None


def since(after_id, session, limit=50):
    """窗口之后的全部消息，含自己说的（调用方按 is_self 过滤要不要唤醒）。

    只捞 id、payload 三列，跟 `reply.recent_chatter` 同一份库同一套字段；
    区别只在它是往前看而不是取最近 N 条。
    """
    if not session:
        return []
    try:
        db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    sql = "SELECT id, payload FROM chat_inbox WHERE session_id=? AND id>? ORDER BY id LIMIT ?"
    try:
        rows = db.execute(sql, (session, int(after_id or 0), max(1, int(limit)))).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    out = []
    for event_id, payload in rows:
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        out.append({
            "id": event_id,
            "sender": data.get("sender") or "",
            "text": " ".join((data.get("content") or "").split()),
            "ts": data.get("timestamp"),
            "self": bool(data.get("is_self")),
            "synthetic": bool(data.get("synthetic")),
            "recalled": bool(data.get("recalled")),
        })
    return out


def consume(window, rows):
    """消化一批查询结果：返回该唤醒的闲聊，并原地更新窗口。

    自己说的话和系统提示也推进 after_id——不然每轮都要重新扫过它们——
    但不占 budget、不唤醒：机器人自言自语不算群友接话。
    有人接话就续期（until 推后），接满 budget 或彻底没人说话再由 stale() 收窗。
    """
    if not rows:
        return []
    woke = [r for r in rows if not r["self"] and not r["synthetic"]]
    window["after_id"] = max(r["id"] for r in rows)
    if woke:
        # 钳在 0：背着聊天记录回来时一批可能远超额度，那是「这段时间群里都在聊」，
        # 一次唤醒给全比拆成几轮更省事；额度只是窗口的上限，不是要透支成负数。
        window["budget"] = max(0, int(window.get("budget") or 0) - len(woke))
        window["until"] = time.time() + float(window.get("idle_seconds") or 180)
        window["wakes"] = int(window.get("wakes") or 0) + 1
        save(window)
    return woke


def _group_of(payload):
    """(group_id, onebot_message_id) from an inbox payload, or None."""
    parts = str(payload.get("conversation_id") or "").split(":")
    if len(parts) != 3 or parts[1] != "group":
        return None
    message_id = payload.get("onebot_message_id")
    if not message_id:
        return None
    return parts[2], str(message_id)


def lookup(event_id):
    """任意事件的 (group_id, onebot_message_id)，闲聊也找得到。

    reply.py 以前只查 wg_events.jsonl，而追问唤醒要回的消息是被 mention_only
    滤掉的闲聊，压根不在那里面。chat_inbox 两条信息都有，拿它兜底，引用才挂得上去。
    id 是 AUTOINCREMENT 的全局 rowid，跨会话唯一，不用再按会话过滤。
    """
    try:
        db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = db.execute("SELECT payload FROM chat_inbox WHERE id=?",
                         (int(event_id),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    if not row:
        return None
    try:
        return _group_of(json.loads(row[0]))
    except ValueError:
        return None


def latest_group_id():
    """当前会话最新一条消息所在的群；没有会话时返回空串。"""
    sid = active_session_id()
    if not sid:
        return ""
    try:
        db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return ""
    try:
        row = db.execute(
            "SELECT payload FROM chat_inbox WHERE session_id=? ORDER BY id DESC LIMIT 1",
            (sid,)).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        db.close()
    if not row:
        return ""
    try:
        found = _group_of(json.loads(row[0]))
    except ValueError:
        return ""
    return found[0] if found else ""


def show():
    window = load()
    if not window:
        print("追问窗口：未开窗")
        return 0
    reason = stale(window)
    if reason:
        print(f"追问窗口：过期（{reason}），下次监听路过时关掉")
    else:
        print(f"追问窗口：进行中，从 id={window.get('after_id')} 之后看，"
              f"还可跟 {window.get('budget')} 条，静默至 "
              f"{time.strftime('%H:%M:%S', time.localtime(window.get('until') or 0))}")
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if "--show" in sys.argv[1:]:
        return show()
    print(__doc__.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
