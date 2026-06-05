#!/usr/bin/env python3
"""babysitter babysitter self-prod Stop hook.

Runs only inside the babysitter session (project-local hook in babysitter/.claude/).
Reads Claude Code's hook event JSON on stdin, asks the daemon whether the
babysitter just dropped its wait_for_stop loop, and if so prints
{"decision":"block","reason":"..."} so Claude Code re-enters the babysitter with
new "user input" forcing it to call wait_for_stop again.

Pause sentinel: if the babysitter's last assistant message contains the literal
string [PAUSE-WATCH Xm] (e.g. [PAUSE-WATCH 30m]), the daemon records the pause
window and skips the prod for that duration. Used when the user explicitly tells
the babysitter to step out of the loop.
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
    transcript = data.get("transcript_path", "")
    stop_active = bool(data.get("stop_hook_active"))

    # Critical: never re-prod inside an already-prodded turn — would loop forever.
    if stop_active or not session_id or not transcript:
        return 0

    # Defense in depth: only prod if cwd really is the babysitter project.
    # The project-local hook should already scope this, but check anyway.
    cwd = data.get("cwd", "") or ""
    if "babysitter" not in cwd:
        return 0

    payload = json.dumps({
        "session_id": session_id,
        "transcript_path": transcript,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{DAEMON}/babysitter_stopped",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            resp = json.loads(r.read().decode())
    except Exception:
        return 0  # daemon down: never block the babysitter

    prod = resp.get("prod")
    if prod:
        print(json.dumps({"decision": "block", "reason": prod}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
