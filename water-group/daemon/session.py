"""Ensure a live chat session exists for the water group and cache its id.

A session belongs to the MCP grant that created it, so refreshing a revoked grant
also drops the session. Re-creating it here means the collector keeps running across
grant rotation without anyone re-issuing anything by hand.
"""
import os
import sys
import uuid
from pathlib import Path

import httpx

import mcp_client

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
SID_FILE = STATE_DIR / "wg_session.txt"
GROUP = os.environ.get("TULPA_WG_CONVERSATION", "3885934113:group:1059426859")
PERSONA = os.environ.get("TULPA_WG_PERSONA", "yu_bao")
UI = os.environ.get("TULPA_UI_URL", "http://127.0.0.1:47860")


def current():
    try:
        return SID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def alive(sid):
    """A stopped session still answers get_chat_session, so check the state too."""
    data = mcp_client.call_tool("get_chat_session",
                                {"session_id": sid, "response_format": "compact"}, 30)
    if data.get("error"):
        return False
    state = data.get("session") or {}
    if state.get("active") is False or state.get("state") == "stopped":
        return False
    return True


def clear_group_sessions():
    """Stop sessions this group still holds.

    A session is owned by the grant that created it, so a grant refresh leaves an
    orphan the new grant cannot see but also cannot replace: starting a second
    session is rejected. Stopping by conversation id clears exactly that orphan.
    """
    headers = {"x-chatweave-ui": "1"}
    with httpx.Client(timeout=httpx.Timeout(15, connect=5), trust_env=False) as client:
        try:
            listed = client.get(f"{UI}/api/mcp/chats", headers=headers).json()
        except Exception:  # noqa: BLE001
            return False
        stopped = False
        for item in listed.get("sessions") or []:
            if item.get("conversation_id") != GROUP:
                continue
            sid = item.get("id") or item.get("session_id")
            if not sid:
                continue
            try:
                client.post(f"{UI}/api/mcp/chats/{sid}/stop", headers=headers, json={})
                stopped = True
            except Exception:  # noqa: BLE001
                continue
        return stopped


def start():
    data = mcp_client.call_tool(
        "start_chat_session",
        {"conversation_id": GROUP, "persona_preset": PERSONA,
         "response_format": "compact", "idempotency_key": uuid.uuid4().hex},
        90,
    )
    if data.get("error"):
        return ""
    sid = data.get("session_id") or (data.get("session") or {}).get("id")
    if sid:
        SID_FILE.write_text(sid, encoding="utf-8")
    return sid


def ensure():
    sid = current()
    if sid and alive(sid):
        return sid

    listed = mcp_client.call_tool("list_chat_sessions", {}, 30)
    for item in listed.get("sessions") or []:
        if item.get("conversation_id") != GROUP:
            continue
        sid = item.get("id") or item.get("session_id")
        if sid and alive(sid):
            SID_FILE.write_text(sid, encoding="utf-8")
            print(f"resumed {sid}")
            return sid

    sid = start()
    if sid:
        print(f"started {sid}")
        return sid

    if clear_group_sessions():
        print("cleared stale group session")
        sid = start()
        if sid:
            print(f"started {sid}")
            return sid
    print("session bootstrap failed")
    return ""


if __name__ == "__main__":
    sys.exit(0 if ensure() else 1)
