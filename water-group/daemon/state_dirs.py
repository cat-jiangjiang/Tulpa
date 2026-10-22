"""Locate the state directories of the group daemons running on this machine.

Every daemon script addresses one group through TULPA_WG_STATE. A machine that
watches several groups keeps one state directory per group under a common root,
named ``g<group id>``, because two collectors sharing a state directory would
fight over the same cursor, session id and heartbeat. Tools that have to fan out
across groups read that layout from here instead of each carrying its own copy
of the rules.

A single-group setup keeps its files directly in the root; that counts as one
group and is returned as such, so the same tools work in both layouts. Its group
id comes from ``TULPA_WG_CONVERSATION`` or ``wg_conversation.txt`` — never from
the directory name, because the root directory is not named after the group.
"""
import os
import re
from pathlib import Path

GROUP_IN_CONVERSATION = re.compile(r":group:(\d+)$")


def root(default_root):
    """The common state root, overridable with TULPA_WG_STATE_ROOT."""
    return Path(os.environ.get("TULPA_WG_STATE_ROOT", default_root))


def group_id(conversation):
    """The group id in ``<account>:group:<id>``, or in a bare id file."""
    text = str(conversation or "").strip()
    match = GROUP_IN_CONVERSATION.search(text)
    if match:
        return match.group(1)
    return text if text.isdigit() else ""


def conversation(root_dir):
    """The root's wg_conversation.txt, or "" when it does not exist yet."""
    try:
        return (Path(root_dir) / "wg_conversation.txt").read_text(encoding="utf-8")
    except OSError:
        return ""


def state_dirs(root_dir, conversation_env=None):
    """[(group id, state dir)] for every group, ordered by directory name.

    Only directories that actually hold a session file count: a half-created
    state directory must not make a watcher spawn against a cursor that will
    never move. A group that was dropped from the watch list is removed by the
    launcher along with its state directory.

    The single-group layout has no group id in its directory name, so callers
    pass ``conversation_env`` (usually ``TULPA_WG_CONVERSATION``) or rely on the
    ``wg_conversation.txt`` Phase 5 writes. Without either, there is no group id
    to send to and the layout is reported as empty instead of a fake label.
    """
    root_dir = Path(root_dir)
    found = []
    for child in sorted(root_dir.glob("g*")):
        if not child.is_dir() or not child.name[1:].isdigit():
            continue
        if (child / "wg_session.txt").exists():
            found.append((child.name[1:], child))
    if found:
        return found
    if (root_dir / "wg_session.txt").exists():
        if conversation_env is None:
            conversation_env = os.environ.get("TULPA_WG_CONVERSATION", "")
        gid = group_id(conversation_env) or group_id(conversation(root_dir))
        if gid:
            return [(gid, root_dir)]
    return []
