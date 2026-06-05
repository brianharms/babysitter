#!/usr/bin/env python3
"""babysitter SessionEnd hook payload.

Reads Claude Code hook JSON on stdin; POSTs session_id to /end so the daemon
marks the session ended and excludes it from /session_by_tty resolution.
Fire-and-forget — never blocks.
"""
import json
import sys
import urllib.request

DAEMON = "http://127.0.0.1:7890"


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0

    session_id = data.get("session_id", "")
    if not session_id:
        return 0

    payload = json.dumps({"session_id": session_id}).encode()
    try:
        req = urllib.request.Request(
            f"{DAEMON}/end",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
