"""钉住追问窗口：回复之后还能听见群里说什么，没人接话就收回。

`mention_only` 让闲聊进不了 `wg_events.jsonl`，而追问要听的恰恰是闲聊——
所以窗口只能读 `chat_inbox`。这里用一份临时库固定：开窗参数、只看自己会话、
自己的话和系统提示推进游标但不唤醒、攒满预算 / 静默超时 / 换会话三种收窗理由，
以及 reply.py 回闲聊时要从库里拿群号和消息编号的兜底。

    .venv/Scripts/python.exe scripts/check_followup.py
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = tempfile.mkdtemp(prefix="wg_followup_")
DB = Path(TMP) / "fixture.sqlite3"
SID = "session-fixture"

os.environ["TULPA_WG_DB"] = str(DB)
os.environ["TULPA_WG_STATE"] = TMP

sys.path.insert(0, str(ROOT / "water-group" / "daemon"))

import followup  # noqa: E402

# 5 是被回复的那条；6/9 是群友接话；7 是机器人自己；8 是系统提示；
# 10 属于另一个会话；11 是稍后机器人自己补的一句。
MESSAGES = [
    (5, {"sender": "甲", "content": "@小鱼包 在吗", "timestamp": 1792500000000}),
    (6, {"sender": "乙", "content": "在的在的", "timestamp": 1792500001000}),
    (7, {"sender": "小鲸鱼", "content": "在呢", "timestamp": 1792500002000, "is_self": True}),
    (8, {"sender": "系统", "content": "提示", "timestamp": 1792500003000, "synthetic": True}),
    (9, {"sender": "丙", "content": "问作业吗", "timestamp": 1792500004000}),
    (11, {"sender": "小鲸鱼", "content": "刚那句当我没说", "timestamp": 1792500006000,
          "is_self": True}),
]
FOREIGN = (10, {"sender": "外人", "content": "别的会话", "timestamp": 1792500005000})
GROUP = "123"
ONEBOT = {5: "775", 6: "776", 7: "777", 8: "778", 9: "779", 10: "7710", 11: "7711"}


def build():
    db = sqlite3.connect(DB)
    db.execute("CREATE TABLE chat_sessions (id TEXT, conversation_id TEXT, active INTEGER)")
    db.execute("CREATE TABLE chat_inbox (id INTEGER PRIMARY KEY, session_id TEXT, payload TEXT)")
    db.execute("INSERT INTO chat_sessions VALUES (?, ?, 1)", (SID, f"111:group:{GROUP}"))
    db.execute("INSERT INTO chat_sessions VALUES ('dead-session', '100:group:999', 0)")
    for event_id, payload in MESSAGES:
        data = dict(payload)
        data["conversation_id"] = f"111:group:{GROUP}"
        data["onebot_message_id"] = ONEBOT[event_id]
        db.execute("INSERT INTO chat_inbox VALUES (?, ?, ?)",
                   (event_id, SID, json.dumps(data, ensure_ascii=False)))
    foreign = dict(FOREIGN[1])
    foreign["conversation_id"] = "100:group:999"
    foreign["onebot_message_id"] = ONEBOT[FOREIGN[0]]
    db.execute("INSERT INTO chat_inbox VALUES (?, ?, ?)",
               (FOREIGN[0], "dead-session", json.dumps(foreign, ensure_ascii=False)))
    db.commit()
    db.close()


def check_open():
    state = followup.open(5, max_messages=4, idle_seconds=60)
    assert state["after_id"] == 5, state
    assert state["session"] == SID, state
    assert state["budget"] == 4, state
    assert 59 <= state["until"] - state["opened_at"] <= 61, state
    assert followup.load() == state, "状态没落盘"
    # 不传参数时跟 config 的 followup 段走
    defaults = followup.open(5)
    assert defaults["budget"] == followup.settings()["max_messages"], defaults
    assert defaults["idle_seconds"] == followup.settings()["idle_seconds"], defaults
    print("  ok  开窗参数与默认值（config followup 段）")


def check_since():
    followup.open(5, max_messages=4, idle_seconds=60)
    rows = followup.since(5, SID)
    assert [r["id"] for r in rows] == [6, 7, 8, 9, 11], [r["id"] for r in rows]
    assert all(r["sender"] and r["text"] for r in rows), "sender/text 没取出来"
    # 别的会话的消息（id=10）不能混进来
    assert all(r["id"] != 10 for r in rows), "混进了别的会话"
    print("  ok  窗口之后的消息按 id 涨序、不跨会话")


def check_consume():
    state = followup.open(5, max_messages=4, idle_seconds=60)
    woke = followup.consume(state, followup.since(5, SID))
    assert [r["id"] for r in woke] == [6, 9], "自己说的和系统提示不该唤醒"
    assert state["after_id"] == 11, "游标要推过所有行，包括自己的话"
    assert state["budget"] == 2, state
    assert state["wakes"] == 1, state
    assert state["until"] > state["opened_at"] + 60, "有人接话要续期"
    assert followup.consume(state, followup.since(11, SID)) == [], "没有新消息时不该再唤醒"
    assert state["budget"] == 2, "空批次不该动额度"
    print("  ok  接话才唤醒、才续期；自己的话只推游标")


def check_budget():
    state = followup.open(5, max_messages=2, idle_seconds=600)
    followup.consume(state, followup.since(5, SID))
    assert state["budget"] == 0, state
    assert followup.stale(state) == "budget", followup.stale(state)
    # 一批远超额度时钳在 0，不透支成负数
    tight = followup.open(5, max_messages=1, idle_seconds=600)
    followup.consume(tight, followup.since(5, SID))
    assert tight["budget"] == 0, tight
    print("  ok  接满预算就收窗（含超额钳零）")


def check_idle():
    state = followup.open(5, idle_seconds=1)
    assert followup.stale(state) is None, followup.stale(state)
    time.sleep(1.1)
    assert followup.stale(state) == "idle", followup.stale(state)
    print("  ok  静默超时就收窗")


def check_session():
    state = followup.open(5, idle_seconds=600)
    state["session"] = "dead-session"
    followup.save(state)
    assert followup.stale(state) == "session", followup.stale(state)
    print("  ok  换会话即收窗（防 after_id 对错号）")


def check_lookup():
    followup.close()
    assert followup.load() is None, "close 之后不该还有窗口"
    assert followup.lookup(6) == (GROUP, "776"), followup.lookup(6)
    assert followup.lookup(11) == (GROUP, "7711"), followup.lookup(11)
    assert followup.lookup(9999) is None, "查无此事"
    assert followup.latest_group_id() == GROUP, followup.latest_group_id()
    print("  ok  close/开窗文件，lookup 与群号兜底")


def main():
    build()
    print("追问窗口回归：")
    check_open()
    check_since()
    check_consume()
    check_budget()
    check_idle()
    check_session()
    check_lookup()
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
