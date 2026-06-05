#!/usr/bin/env python3
"""babysitter MCP server — tools for the babysitter CLI."""
import json
import urllib.request
import urllib.parse
from mcp.server.fastmcp import FastMCP

DAEMON = "http://127.0.0.1:7890"

mcp = FastMCP("babysitter")


def _get(path, **params):
    qs = urllib.parse.urlencode(params)
    url = f"{DAEMON}{path}?{qs}" if qs else f"{DAEMON}{path}"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode())


def _post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{DAEMON}{path}", data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


@mcp.tool()
def list_sessions() -> dict:
    """List all registered Claude Code sessions (watched candidates).

    Returns a dict with 'sessions' (id -> info) and 'modes' (id -> review|auto).
    Use this to see what's available to attach to.
    """
    return _get("/sessions")


@mcp.tool()
def attach(session_id: str) -> dict:
    """Attach to a watched session. Returns current info + mode.

    Call this once when the babysitter starts, then loop on wait_for_stop().
    """
    sessions = _get("/sessions")
    info = sessions.get("sessions", {}).get(session_id)
    if not info:
        return {"error": f"session {session_id} not found", "available": list(sessions.get("sessions", {}).keys())}
    return {"session_id": session_id, "info": info, "mode": sessions.get("modes", {}).get(session_id, "review")}


@mcp.tool()
def wait_for_stop(session_id: str, timeout_seconds: int = 30) -> dict:
    """Long-poll: wait for the watched session to finish a turn.

    Returns {"event": {...}} when the watched CLI hits its Stop hook, or
    {"event": null} if timeout elapsed (caller should loop).

    Default timeout 30s. Max 60s (daemon-side urlopen limit).
    """
    return _get("/wait_for_stop", session_id=session_id, timeout=min(timeout_seconds, 55))


@mcp.tool()
def get_transcript(session_id: str, last_n: int = 20) -> dict:
    """Read the watched session's transcript. Returns last N messages.

    Each message is a raw jsonl event from Claude Code. Typical fields:
      - type: "user" | "assistant" | "system"
      - message: {role, content: [...]}
      - timestamp
    Call after wait_for_stop fires to see what the watched CLI just did.
    """
    result = _get("/transcript", session_id=session_id)
    msgs = result.get("messages", [])
    return {"messages": msgs[-last_n:], "total": len(msgs)}


@mcp.tool()
def queue_injection(session_id: str, message: str) -> dict:
    """Queue a message to be injected into the watched CLI on its next Stop.

    ONLY takes effect when mode is 'auto'. In 'review' mode, injections are ignored
    (the babysitter should speak to the user instead).

    The watched CLI's stop hook long-polls for this and, if present, returns
    {"decision":"block","reason": <message>} — which Claude Code treats as new user input.
    """
    return _post("/inject", {"session_id": session_id, "message": message})


@mcp.tool()
def set_mode(session_id: str, mode: str) -> dict:
    """Set the babysitter mode for a session.

    mode='review' — default. Babysitter observes only; reports concerns to the user.
    mode='auto'   — babysitter's queued injections get delivered to the watched CLI.
    """
    if mode not in ("review", "auto"):
        return {"error": "mode must be 'review' or 'auto'"}
    return _post("/mode", {"session_id": session_id, "mode": mode})


@mcp.tool()
def get_mode(session_id: str) -> dict:
    """Get the current mode for a session."""
    sessions = _get("/sessions")
    return {"mode": sessions.get("modes", {}).get(session_id, "review")}


if __name__ == "__main__":
    mcp.run()
