#!/usr/bin/env python3
"""babysitter daemon — session registry, transcript tailer, injection queue."""
import json
import os
import sys
import time
import threading
import queue
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path

PORT = 7890
STATE_DIR = Path.home() / ".claude" / "babysitter"
STATE_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_FILE = STATE_DIR / "sessions.json"
MODE_FILE = STATE_DIR / "modes.json"
LOG_FILE = STATE_DIR / "daemon.log"

STATE_LOCK = threading.RLock()
SESSIONS = {}           # session_id -> {tty, transcript_path, started, last_stop}
STOP_QUEUES = {}        # session_id -> queue.Queue() of stop events for babysitter long-poll
INJECTION_QUEUES = {}   # session_id -> queue.Queue() of injections for watched stop-hook long-poll
MODES = {}              # session_id -> "review" | "auto"
BABYSITTER_PAUSE_UNTIL = {} # session_id -> epoch; if now < value, suppress self-prod
BABYSITTER_PROD_LOG = {}    # session_id -> [epoch, ...] last N self-prod times (rate limiting)

# Self-prod rate limit: max N prods within a sliding window
PROD_RATE_LIMIT_N = 5
PROD_RATE_LIMIT_WINDOW_SEC = 120


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass
    sys.stderr.write(line)


def persist_sessions():
    with STATE_LOCK:
        try:
            with open(SESSIONS_FILE, "w") as f:
                json.dump(SESSIONS, f, indent=2)
        except Exception as e:
            log(f"persist_sessions error: {e}")


def persist_modes():
    with STATE_LOCK:
        try:
            with open(MODE_FILE, "w") as f:
                json.dump(MODES, f, indent=2)
        except Exception as e:
            log(f"persist_modes error: {e}")


def load_state():
    global SESSIONS, MODES
    try:
        if SESSIONS_FILE.exists():
            with open(SESSIONS_FILE) as f:
                SESSIONS = json.load(f)
    except Exception:
        SESSIONS = {}
    try:
        if MODE_FILE.exists():
            with open(MODE_FILE) as f:
                MODES = json.load(f)
    except Exception:
        MODES = {}


def ensure_queues(session_id):
    if session_id not in STOP_QUEUES:
        STOP_QUEUES[session_id] = queue.Queue()
    if session_id not in INJECTION_QUEUES:
        INJECTION_QUEUES[session_id] = queue.Queue()


def _read_last_assistant_message(transcript_path):
    """Tail the jsonl transcript and return the most recent assistant message dict."""
    if not transcript_path or not Path(transcript_path).exists():
        return None
    try:
        # Read tail: walk backwards from EOF reading chunks until we have enough
        # newlines. For typical Claude Code transcripts (lines ~1-5KB), reading
        # the last 64KB is plenty to find the most recent assistant message.
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 65536)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="replace")
        lines = [ln for ln in tail.splitlines() if ln.strip()]
        # Walk backwards looking for an assistant message
        for line in reversed(lines):
            try:
                evt = json.loads(line)
            except Exception:
                continue
            if evt.get("type") == "assistant":
                return evt
        return None
    except Exception:
        return None


def _extract_text_from_assistant(assistant_evt):
    """Return concatenated text content from an assistant event, or empty string."""
    if not assistant_evt:
        return ""
    msg = assistant_evt.get("message", {})
    content = msg.get("content", [])
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _assistant_called_wait_for_stop(assistant_evt):
    """True if the assistant message contains a tool_use block for wait_for_stop."""
    if not assistant_evt:
        return False
    msg = assistant_evt.get("message", {})
    content = msg.get("content", [])
    if not isinstance(content, list):
        return False
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        # Match both bare and namespaced forms
        if name == "wait_for_stop" or name.endswith("__wait_for_stop"):
            return True
    return False


_PAUSE_RE = None


def _extract_pause_minutes(text):
    """If text contains [PAUSE-WATCH Xm] sentinel, return X as int. Else None."""
    global _PAUSE_RE
    if _PAUSE_RE is None:
        import re
        _PAUSE_RE = re.compile(r"\[PAUSE-WATCH\s+(\d+)m\]", re.IGNORECASE)
    if not text:
        return None
    m = _PAUSE_RE.search(text)
    if not m:
        return None
    try:
        n = int(m.group(1))
        # Clamp to sane range
        return max(1, min(n, 240))
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default stderr spam

    def _send(self, code, body):
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)

        if url.path == "/health":
            return self._send(200, {"ok": True, "sessions": len(SESSIONS)})

        if url.path == "/sessions":
            with STATE_LOCK:
                return self._send(200, {"sessions": SESSIONS, "modes": MODES})

        if url.path == "/session_by_tty":
            tty = qs.get("tty", [""])[0]
            with STATE_LOCK:
                candidates = [
                    (info.get("started", 0), sid, info)
                    for sid, info in SESSIONS.items()
                    if info.get("tty") == tty and not info.get("ended")
                ]
            if candidates:
                candidates.sort(reverse=True)  # newest started wins
                _, sid, info = candidates[0]
                return self._send(200, {"session_id": sid, "info": info})
            return self._send(404, {"error": "no active session for tty"})

        if url.path == "/transcript":
            sid = qs.get("session_id", [""])[0]
            since = float(qs.get("since", ["0"])[0])
            with STATE_LOCK:
                info = SESSIONS.get(sid)
            if not info:
                return self._send(404, {"error": "unknown session"})
            path = info.get("transcript_path")
            if not path or not Path(path).exists():
                return self._send(200, {"messages": [], "mtime": 0})
            try:
                st = os.stat(path)
                messages = []
                with open(path) as f:
                    for line in f:
                        try:
                            evt = json.loads(line)
                        except Exception:
                            continue
                        ts = evt.get("timestamp") or evt.get("ts") or 0
                        # jsonl uses ISO strings; convert if needed
                        if isinstance(ts, str):
                            try:
                                ts = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
                            except Exception:
                                ts = 0
                        messages.append(evt)
                return self._send(200, {"messages": messages, "mtime": st.st_mtime})
            except Exception as e:
                return self._send(500, {"error": str(e)})

        if url.path == "/injection":
            # Long-poll for watched CLI's stop hook
            sid = qs.get("session_id", [""])[0]
            timeout = float(qs.get("timeout", ["5"])[0])
            with STATE_LOCK:
                ensure_queues(sid)
                q = INJECTION_QUEUES[sid]
                mode = MODES.get(sid, "review")
            if mode != "auto":
                return self._send(200, {"injection": None, "mode": mode})
            try:
                msg = q.get(timeout=timeout)
                return self._send(200, {"injection": msg, "mode": mode})
            except queue.Empty:
                return self._send(200, {"injection": None, "mode": mode})

        if url.path == "/wait_for_stop":
            # Long-poll for babysitter CLI.
            # Returns the SINGLE OLDEST queued event, OR — if the queue has
            # multiple events backlogged — drains ALL of them and returns the
            # most recent, with `dropped` indicating how many older ones were
            # discarded. This prevents the babysitter from falling behind during
            # bursts of activity.
            sid = qs.get("session_id", [""])[0]
            timeout = float(qs.get("timeout", ["30"])[0])
            with STATE_LOCK:
                ensure_queues(sid)
                q = STOP_QUEUES[sid]
            try:
                evt = q.get(timeout=timeout)
            except queue.Empty:
                return self._send(200, {"event": None, "dropped": 0})
            # Drain anything that piled up behind it; keep only the newest.
            dropped = 0
            while True:
                try:
                    newer = q.get_nowait()
                    dropped += 1
                    evt = newer
                except queue.Empty:
                    break
            if dropped:
                log(f"wait_for_stop {sid[:8]} drained {dropped} older events")
            return self._send(200, {"event": evt, "dropped": dropped})

        return self._send(404, {"error": "unknown path"})

    def do_POST(self):
        url = urlparse(self.path)
        body = self._read_json()

        if url.path == "/register":
            sid = body.get("session_id")
            if not sid:
                return self._send(400, {"error": "session_id required"})
            new_tty = body.get("tty", "")
            now = time.time()
            evicted = []
            with STATE_LOCK:
                # A SessionStart on this TTY means whatever was previously live
                # on that TTY is no longer the live Claude session for that pane.
                # Mark prior owners ended so /session_by_tty never returns ghosts.
                if new_tty:
                    for other_sid, info in SESSIONS.items():
                        if other_sid == sid:
                            continue
                        if info.get("tty") == new_tty and not info.get("ended"):
                            info["ended"] = True
                            info["ended_at"] = now
                            info["ended_reason"] = "tty_reused"
                            evicted.append(other_sid)
                SESSIONS[sid] = {
                    "session_id": sid,
                    "tty": new_tty,
                    "transcript_path": body.get("transcript_path", ""),
                    "cwd": body.get("cwd", ""),
                    "started": now,
                    "ended": False,
                }
                MODES.setdefault(sid, "review")
                ensure_queues(sid)
            persist_sessions()
            persist_modes()
            if evicted:
                log(f"register {sid[:8]} evicted {len(evicted)} prior on tty={new_tty}: {[e[:8] for e in evicted]}")
            log(f"register {sid[:8]} tty={new_tty} cwd={body.get('cwd')}")
            return self._send(200, {"ok": True})

        if url.path == "/stopped":
            sid = body.get("session_id")
            if not sid:
                return self._send(400, {"error": "session_id required"})
            with STATE_LOCK:
                ensure_queues(sid)
                if sid in SESSIONS:
                    SESSIONS[sid]["last_stop"] = time.time()
                evt = {
                    "session_id": sid,
                    "stopped_at": time.time(),
                    "transcript_path": body.get("transcript_path", ""),
                }
                STOP_QUEUES[sid].put(evt)
            persist_sessions()
            log(f"stopped {sid[:8]}")
            return self._send(200, {"ok": True})

        if url.path == "/inject":
            sid = body.get("session_id")
            msg = body.get("message", "")
            if not sid or not msg:
                return self._send(400, {"error": "session_id and message required"})
            with STATE_LOCK:
                ensure_queues(sid)
                mode = MODES.get(sid, "review")
                if mode != "auto":
                    log(f"inject {sid[:8]} DROPPED (mode={mode})")
                    return self._send(200, {"ok": False, "dropped": True, "mode": mode})
                INJECTION_QUEUES[sid].put(msg)
            log(f"inject {sid[:8]} len={len(msg)}")
            return self._send(200, {"ok": True, "queued": True})

        if url.path == "/mode":
            sid = body.get("session_id")
            mode = body.get("mode")
            if not sid or mode not in ("review", "auto"):
                return self._send(400, {"error": "session_id + mode (review|auto) required"})
            with STATE_LOCK:
                prev = MODES.get(sid, "review")
                MODES[sid] = mode
                # Drain any leftover injections when switching away from auto
                if prev == "auto" and mode != "auto":
                    ensure_queues(sid)
                    drained = 0
                    while not INJECTION_QUEUES[sid].empty():
                        try:
                            INJECTION_QUEUES[sid].get_nowait()
                            drained += 1
                        except queue.Empty:
                            break
                    if drained:
                        log(f"mode flip drained {drained} queued injections for {sid[:8]}")
            persist_modes()
            log(f"mode {sid[:8]} -> {mode}")
            return self._send(200, {"ok": True, "mode": mode})

        if url.path == "/end":
            sid = body.get("session_id")
            with STATE_LOCK:
                if sid in SESSIONS:
                    SESSIONS[sid]["ended"] = True
                    SESSIONS[sid]["ended_at"] = time.time()
            persist_sessions()
            log(f"end {sid[:8] if sid else '?'}")
            return self._send(200, {"ok": True})

        if url.path == "/babysitter_stopped":
            # Called by the babysitter's own Stop hook. Decides whether to prod
            # the babysitter to resume the wait_for_stop loop.
            sid = body.get("session_id", "")
            transcript = body.get("transcript_path", "")
            if not sid or not transcript:
                return self._send(200, {"prod": None})

            now = time.time()

            # 1. Pause sentinel — if babysitter said [PAUSE-WATCH Xm], honor it
            with STATE_LOCK:
                pause_until = BABYSITTER_PAUSE_UNTIL.get(sid, 0)
            if now < pause_until:
                return self._send(200, {"prod": None, "reason": "paused"})

            # 2. Read the last assistant message from the transcript
            last_assistant = _read_last_assistant_message(transcript)
            if last_assistant is None:
                return self._send(200, {"prod": None, "reason": "no_transcript"})

            # 3. Check for explicit pause sentinel in the assistant text
            text = _extract_text_from_assistant(last_assistant)
            pause_minutes = _extract_pause_minutes(text)
            if pause_minutes is not None:
                with STATE_LOCK:
                    BABYSITTER_PAUSE_UNTIL[sid] = now + (pause_minutes * 60)
                log(f"babysitter {sid[:8]} pause-sentinel {pause_minutes}m")
                return self._send(200, {"prod": None, "reason": "pause_sentinel"})

            # 4. Did the last assistant message call wait_for_stop?
            if _assistant_called_wait_for_stop(last_assistant):
                return self._send(200, {"prod": None, "reason": "loop_intact"})

            # 5. Rate limit — don't prod more than N times per window
            with STATE_LOCK:
                log_entry = BABYSITTER_PROD_LOG.setdefault(sid, [])
                # Drop entries outside window
                log_entry[:] = [t for t in log_entry if now - t < PROD_RATE_LIMIT_WINDOW_SEC]
                if len(log_entry) >= PROD_RATE_LIMIT_N:
                    log(f"babysitter {sid[:8]} prod RATE-LIMITED ({len(log_entry)} in window)")
                    return self._send(200, {"prod": None, "reason": "rate_limited"})
                log_entry.append(now)

            log(f"babysitter {sid[:8]} self-prod fired (loop drift)")
            return self._send(200, {
                "prod": (
                    "WATCH-LOOP DRIFT DETECTED. Your last turn ended without "
                    "calling wait_for_stop. Per BABYSITTER_PROMPT.md, every turn "
                    "in this session must end with wait_for_stop. Call it now "
                    "to resume the watch loop."
                ),
                "reason": "drift",
            })

        return self._send(404, {"error": "unknown path"})


def babysitter_heartbeat_loop():
    """Every 60s, push a synthetic 'tick' event into every active session's
    STOP_QUEUE if it's empty. This guarantees the babysitter's wait_for_stop
    returns at least once per minute, so heartbeats fire and the babysitter
    never falls behind real stops by more than ~60s.

    The synthetic event is marked `synthetic: true` so the babysitter can
    distinguish "real stop, review the turn" from "tick, just check & loop."
    """
    INTERVAL_SEC = 60
    while True:
        try:
            time.sleep(INTERVAL_SEC)
        except Exception:
            continue
        now = time.time()
        with STATE_LOCK:
            sids = list(SESSIONS.keys())
            queues = {sid: STOP_QUEUES.get(sid) for sid in sids}
        for sid in sids:
            q = queues.get(sid)
            if q is None:
                continue
            # Only inject a tick if the queue is empty — don't pile on top of
            # real events.
            if not q.empty():
                continue
            evt = {
                "session_id": sid,
                "stopped_at": now,
                "transcript_path": SESSIONS.get(sid, {}).get("transcript_path", ""),
                "synthetic": True,
            }
            try:
                q.put_nowait(evt)
            except Exception:
                pass


def main():
    load_state()
    log(f"daemon starting on :{PORT}")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)

    # Background watchdog: fires synthetic tick events into every quiet
    # session's queue once a minute. Daemon thread so it dies with main.
    threading.Thread(target=babysitter_heartbeat_loop, daemon=True).start()

    def shutdown(*_):
        log("daemon shutting down")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    server.serve_forever()


if __name__ == "__main__":
    main()
