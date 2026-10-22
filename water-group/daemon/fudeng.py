"""Join in when the group repeats itself.

``mention_only`` never delivers ordinary chatter to ``wg_events.jsonl``, so a
wake-based agent has no way to notice the one social cue it can act on without
being addressed: two people sending the same line. This daemon reads
``chat_inbox`` directly, per group, and echoes the line back once.

Guards: two echoes per group are at least COOLDOWN_GROUP seconds apart, the same
line is not echoed twice within COOLDOWN_TEXT, long text is left alone, and
nothing is echoed while the mute file exists (admin "stop replying"). A group is
also skipped while its collector holds ``wg_pause``, and a line the agent just
answered (``wg_replied.jsonl``) is not echoed as if it were fresh chatter. An
echo is pure banter: it does not open a follow-up window or enter the ledger.

Groups come from the state directory layout (``state_dirs``), so this daemon
needs no group list of its own: one collector per ``g<group id>`` directory is
already the source of truth for what is being watched.
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import state_dirs  # noqa: E402

DB = Path(os.environ.get("TULPA_WG_DB", ROOT / "data" / "mcp-access.sqlite3"))
STATE_DIR = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp" / "fudeng"))
MUTE = Path(os.environ.get("TULPA_WG_MUTE", ROOT / ".tmp" / "wg_muted"))
CONFIG = ROOT / "water-group" / "config.json"

LOG = STATE_DIR / "wg_fudeng.log"
LEDGER = STATE_DIR / "wg_fudeng.json"

REPEAT_NEEDED = 2      # how many different senders make a run a repeat
LOOKBACK = 15          # messages read per group per poll
MAX_LEN = 80           # longer text is not echoed (it reads as spam)
COOLDOWN_TEXT = 300    # same line is not echoed again for 5 minutes
COOLDOWN_GROUP = 25    # at most one echo per group per 25 seconds
POLL = 3               # seconds between polls

# Non-text messages carry only a placeholder as their content, and two people
# posting a picture each would both read as "[图片]" — a false repeat. Segment
# types arrive both in the OneBot English names and in the Chinese names this
# build writes, so both spellings are listed.
MEDIA_SEGMENTS = {"image", "mface", "record", "video", "file", "forward", "face",
                  "图片", "表情", "语音", "视频", "文件", "合并转发", "闪照",
                  "戳一戳", "红包", "链接", "json", "xml"}
PLACEHOLDERS = {"[图片]", "[视频]", "[语音]", "[文件]", "[表情]", "[合并转发]",
                "[闪照]", "[动画表情]", "[链接]", "[红包]", "[戳一戳]", "[语音消息]",
                "[非文本消息]", "[引用非文本消息]"}


def is_placeholder(text):
    """A message made only of placeholders has nothing to echo."""
    rest = text.strip()
    for token in PLACEHOLDERS:
        rest = rest.replace(token, "")
    return not rest.strip()


def log(line):
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass


def onebot():
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    base = str(cfg.get("onebot_http_url") or "").rstrip("/")
    return base, str(cfg.get("onebot_token") or "")


def session_of(state):
    try:
        return (Path(state) / "wg_session.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def muted():
    return MUTE.exists()


def load_ledger():
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_ledger(data):
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        LEDGER.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def send_group(gid, text):
    base, token = onebot()
    if not base or not token:
        return False
    try:
        group_id = int(gid)
    except (TypeError, ValueError):
        log(f"send_group_msg({gid!r}) skipped: not a group id")
        return False
    payload = {"group_id": group_id,
               "message": [{"type": "text", "data": {"text": text}}]}
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), trust_env=False) as client:
            r = client.post(f"{base}/send_group_msg", json=payload,
                            headers={"Authorization": f"Bearer {token}"})
        ok = r.status_code < 400 and r.json().get("retcode") == 0
        if not ok:
            log(f"send_group_msg({gid}) failed: {r.status_code} {r.text[:200]}")
        return ok
    except Exception as exc:  # noqa: BLE001
        log(f"send_group_msg({gid}) error: {type(exc).__name__}: {exc}")
        return False


def recent(sid, limit):
    if not sid:
        return []
    try:
        db = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        rows = db.execute(
            "SELECT payload FROM chat_inbox WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (sid, int(limit)),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        db.close()
    out = []
    for (payload,) in rows:
        try:
            p = json.loads(payload)
        except ValueError:
            continue
        if p.get("is_self") or p.get("synthetic") or p.get("recalled"):
            continue
        content = (p.get("content") or "").strip()
        if not content or is_placeholder(content):
            continue
        # Two people posting a picture each both read as "[图片]", so the
        # segment type has to be checked too, not just the text.
        kinds = {seg.get("type") for seg in p.get("segments") or []
                 if isinstance(seg, dict)}
        if kinds & MEDIA_SEGMENTS:
            continue
        # An @-mention names someone; echoing it would ping them again. The
        # same goes for the wake line addressed to the bot.
        if "at" in kinds or content.startswith("@"):
            continue
        sender = str(p.get("sender_id") or p.get("sender") or "")
        if not sender:
            continue
        out.append({"sender": sender,
                    "onebot_id": str(p.get("onebot_message_id") or ""),
                    "content": content})
    return out


def answered_ids(state):
    """OneBot ids of events the agent answered inside the cooldown window.

    The reply ledger stores ``wg_events`` ids while inbox rows carry OneBot
    message ids, so the event log is the bridge between the two. Reading it is
    deferred to the rare moment a repeat is actually detected.
    """
    state = Path(state)
    now = int(time.time())
    answered = set()
    try:
        lines = (state / "wg_replied.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return answered
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if now - int(item.get("ts") or 0) <= COOLDOWN_TEXT:
            answered.add(item.get("id"))
    if not answered:
        return answered
    out = set()
    try:
        lines = (state / "wg_events.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        onebot_id = event.get("onebot_message_id")
        if event.get("id") in answered and onebot_id:
            out.add(str(onebot_id))
    return out


def detect(rows):
    """The line at the top of the run, if enough different people sent it."""
    if len(rows) < REPEAT_NEEDED:
        return None
    text = rows[0]["content"]
    if len(text) > MAX_LEN or is_placeholder(text):
        return None
    senders = {rows[0]["sender"]}
    for r in rows[1:]:
        if r["content"] == text:
            senders.add(r["sender"])
        else:
            break
    return text if len(senders) >= REPEAT_NEEDED else None


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log("fudeng start")
    root_dir = state_dirs.root(ROOT / ".tmp")
    ledger = load_ledger()
    empty_logged = False
    while True:
        try:
            if muted():
                time.sleep(POLL)
                continue
            now = int(time.time())
            groups = state_dirs.state_dirs(
                root_dir, os.environ.get("TULPA_WG_CONVERSATION", ""))
            if not groups and not empty_logged:
                log("no groups: need g<id>/ or wg_session.txt + wg_conversation.txt")
                empty_logged = True
            elif groups:
                empty_logged = False
            for gid, state in groups:
                state = Path(state)
                # The collector holds wg_pause while the agent plans and sends;
                # echoing into that window races the reply we are about to make.
                if (state / "wg_pause").exists():
                    continue
                rows = recent(session_of(state), LOOKBACK)
                text = detect(rows)
                if not text:
                    continue
                answered = answered_ids(state)
                if any(row["content"] == text and row["onebot_id"] in answered
                       for row in rows):
                    continue
                gkey = f"g:{gid}"
                tkey = f"{gid}:{text}"
                if now - int(ledger.get(gkey, 0) or 0) < COOLDOWN_GROUP:
                    continue
                if now - int(ledger.get(tkey, 0) or 0) < COOLDOWN_TEXT:
                    continue
                if send_group(gid, text):
                    log(f"g{gid} echo: {text!r}")
                    ledger[gkey] = now
                    ledger[tkey] = now
                    save_ledger(ledger)
            time.sleep(POLL)
        except Exception as exc:  # noqa: BLE001
            log(f"loop error: {type(exc).__name__}: {exc}")
            time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
