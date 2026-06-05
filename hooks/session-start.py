#!/usr/bin/env python3
"""babysitter SessionStart hook payload.

Reads Claude Code hook JSON on stdin; fire-and-forget POSTs session_id + tty
to the daemon. Never blocks session startup.
"""
import json
import os
import subprocess
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

    # TTY is captured by the wrapper (session-start.sh) BEFORE backgrounding,
    # because after backgrounding our $PPID is the detached subshell, not the
    # Claude Code pane's shell. The wrapper passes it via env.
    tty = os.environ.get("CLAUDE_WATCH_TTY", "")
    if tty in ("", "/dev/??", "/dev/?"):
        return 0  # no tty — subagent or non-interactive

    payload = json.dumps({
        "session_id": session_id,
        "tty": tty,
        "transcript_path": data.get("transcript_path", ""),
        "cwd": data.get("cwd", ""),
    }).encode()

    try:
        req = urllib.request.Request(
            f"{DAEMON}/register",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass  # daemon not running is fine; hook must never block session start

    return 0


if __name__ == "__main__":
    sys.exit(main())
