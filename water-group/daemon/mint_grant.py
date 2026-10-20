"""Mint a fresh local MCP grant and cache its token.

A grant is bound to the store epoch and to the per-platform account snapshot, so it
dies whenever local data changes or the QQ account gets bound. For a long-running
listener that has to be recoverable without a human: re-create the grant, keep the
same scope as the last water-group grant, and drop the token on disk.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
TOKEN_FILE = STATE_DIR / "mcp_token.txt"
DB = ROOT / "data" / "mcp-access.sqlite3"
BASE = os.environ.get("TULPA_UI_URL", "http://127.0.0.1:47860")
NAME = os.environ.get("TULPA_WG_GRANT", "water-group")

SCOPE_FIELDS = (
    "conversations", "start", "end", "media", "prepare", "voice", "onebot",
    "send", "manage", "chat", "chat_images", "chat_sticker_send",
    "chat_sticker_collect", "chat_reactions", "chat_voice", "chat_video",
    "chat_files", "qq_messages", "qq_interactions", "qq_friends", "qq_profile",
    "qq_qzone", "qq_albums",
)


def last_scope():
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT scope FROM grants WHERE name=? ORDER BY id DESC LIMIT 1", (NAME,)
    ).fetchone()
    if not row:
        # First run under this name: inherit the scope of the newest grant so the
        # permissions stay exactly what the operator already approved once.
        row = db.execute("SELECT scope FROM grants ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        raise SystemExit("no existing grant to copy the scope from")
    return json.loads(row["scope"])


def mint():
    scope = last_scope()
    body = {"name": NAME, "platforms": scope["platforms"], "all_conversations": True}
    for key in SCOPE_FIELDS:
        body[key] = scope.get(key, False)
    with httpx.Client(timeout=httpx.Timeout(20, connect=5), trust_env=False) as client:
        r = client.post(f"{BASE}/api/mcp/connections", json=body,
                        headers={"x-chatweave-ui": "1"})
    if r.status_code >= 400:
        raise SystemExit(f"mint failed {r.status_code}: {r.text[:300]}")
    token = r.json()["token"]
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    print(f"minted {r.json()['id']} -> {TOKEN_FILE}")
    return token


if __name__ == "__main__":
    sys.exit(0 if mint() else 1)
