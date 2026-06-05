#!/usr/bin/env python3
"""babysitter Stop hook payload.

Reads Claude Code hook JSON on stdin; pings daemon; in auto mode, long-polls
for an injection and emits decision:block JSON to stdout.
"""
import json
import sys
import urllib.request
import urllib.parse

DAEMON = "http://127.0.0.1:7890"


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return 0

    session_id = data.get("session_id", "")
    transcript = data.get("transcript_path", "")
    stop_active = bool(data.get("stop_hook_active"))

    # Break injection loop
    if stop_active or not session_id:
        return 0

    # Fire-and-forget stop event
    try:
        req = urllib.request.Request(
            f"{DAEMON}/stopped",
            data=json.dumps({"session_id": session_id, "transcript_path": transcript}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass

    # Long-poll for injection (daemon returns fast in review mode)
    try:
        qs = urllib.parse.urlencode({"session_id": session_id, "timeout": 8})
        with urllib.request.urlopen(f"{DAEMON}/injection?{qs}", timeout=10) as r:
            resp = json.loads(r.read().decode())
    except Exception:
        return 0

    injection = resp.get("injection")
    if injection:
        print(json.dumps({"decision": "block", "reason": injection}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
