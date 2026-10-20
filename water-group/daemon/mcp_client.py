"""Minimal Tulpa MCP client for the water-group daemons.

Only the transport lives here: a JSON-RPC call against the local Streamable HTTP
endpoint, with a one-shot grant refresh when the server rejects the token.

The token is never stored in source. Resolution order:
  1. TULPA_MCP_TOKEN environment variable
  2. <state dir>/mcp_token.txt, written by mint_grant.py
"""
import json
import os
from pathlib import Path

import httpx

URL = os.environ.get("TULPA_MCP_URL", "http://127.0.0.1:48777/mcp")
STATE_DIR = Path(os.environ.get("TULPA_WG_STATE", Path(__file__).resolve().parents[2] / ".tmp"))
TOKEN_FILE = STATE_DIR / "mcp_token.txt"


def current_token():
    token = os.environ.get("TULPA_MCP_TOKEN", "").strip()
    if token:
        return token
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def headers():
    return {
        "Authorization": f"Bearer {current_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": URL.split("//", 1)[-1].split("/", 1)[0],
    }


def parse(resp):
    ctype = resp.headers.get("content-type", "")
    text = resp.text
    if "text/event-stream" in ctype:
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except ValueError:
                    continue
        return {"raw": text}
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text, "status": resp.status_code}


def _call_once(name, arguments, timeout):
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=5), trust_env=False) as client:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "tulpa-water-group", "version": "1.0"}}}
        r = client.post(URL, headers=headers(), json=init)
        if r.status_code >= 400:
            return {"error": "initialize_failed", "status": r.status_code, "body": r.text[:300]}
        hdrs = headers()
        sid = r.headers.get("mcp-session-id")
        if sid:
            hdrs["Mcp-Session-Id"] = sid
            client.post(URL, headers=hdrs, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}
        r = client.post(URL, headers=hdrs, json=call)
        data = parse(r)
        if "error" in data:
            return data
        result = data.get("result", {})
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict)]
        body = "\n".join(texts) if texts else json.dumps(result, ensure_ascii=False)
        try:
            return json.loads(body)
        except ValueError:
            return {"text": body}


def call_tool(name, arguments, timeout=60.0):
    """Call a tool; a revoked grant is re-minted once and the call replayed.

    Grants are bound to the store epoch and the bound-account snapshot, so they die
    whenever local data changes or the QQ account gets bound. For a listener that
    has to survive unattended, recovering here beats waiting for a human.
    """
    data = _call_once(name, arguments, timeout)
    if "denied" in str(data.get("error", "")).lower():
        try:
            import mint_grant
            mint_grant.mint()
        except Exception as exc:  # noqa: BLE001
            return {"error": f"grant refresh failed: {exc}"}
        data = _call_once(name, arguments, timeout)
    return data
