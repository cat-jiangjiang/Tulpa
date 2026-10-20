"""Block until someone wakes the bot, then print it and exit.

The collector already captures a mention within about a second of it arriving.
What used to add a minute was the agent: it had no way to be told, so it looked
at wg_events.jsonl on its own schedule and the gap between two looks was dead
time. This closes that gap — the call returns the moment a wake lands, so the
only remaining delay is composing the reply.

Exit codes:
  0  a wake was printed
  2  timed out with nothing new (call again)
  1  the event stream is unreadable
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import up  # noqa: E402  (wake_reason / events / replied_ids live there)

ROOT = HERE.parents[1]
STATE = Path(os.environ.get("TULPA_WG_STATE", ROOT / ".tmp"))
CURSOR = STATE / "wg_watch.txt"
POLL = 1.5


def read_cursor():
    try:
        return int(CURSOR.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def write_cursor(value):
    try:
        CURSOR.write_text(str(value), encoding="utf-8")
    except OSError:
        pass


def latest_id(rows):
    return max((r.get("id") or 0) for r in rows) if rows else 0


def main():
    parser = argparse.ArgumentParser(description="等一条唤醒（@ 或关键词）")
    parser.add_argument("--timeout", type=int, default=240, help="最多等多少秒")
    parser.add_argument("--after", type=int, default=None, help="从这个事件 id 之后开始找")
    parser.add_argument("--replay", action="store_true", help="忽略游标，重扫所有未回唤醒")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cursor = 0 if args.replay else (args.after if args.after is not None else read_cursor())
    deadline = time.time() + args.timeout
    ledger = up.replied_ids()

    while True:
        rows = up.events()
        ledger = up.replied_ids()
        fresh = [e for e in rows
                 if (e.get("id") or 0) > cursor
                 and up.wake_reason(e)
                 and not up.replied_after(e.get("id"), rows, ledger)]

        if fresh:
            for e in fresh:
                stamp = time.strftime("%H:%M:%S", time.localtime((e.get("timestamp") or 0) / 1000))
                print(f"id={e.get('id')} {stamp} [{up.wake_reason(e)}] "
                      f"{e.get('sender')}: {(e.get('content') or '')[:80]}")
            write_cursor(max(latest_id(rows), max(e.get("id") or 0 for e in fresh)))
            print("WAKE")
            return 0

        # Nothing to report: move past everything seen so a later call does not
        # rescan the whole file, then wait for the collector to append.
        if rows:
            moved = max(cursor, latest_id(rows))
            if moved != cursor:
                cursor = moved
                write_cursor(cursor)

        if time.time() >= deadline:
            print("TIMEOUT")
            return 2
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
