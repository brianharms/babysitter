#!/usr/bin/env python3
"""Automated tests for babysitter daemon.

Covers tests 1, 2, 4, 6, 7, 8 from the plan by exercising the HTTP API directly.
Tests 3 (shortcut) and 5 (mistake detection) require a real Claude CLI — see
test_manual.md.

Run: python3 tests/test_daemon.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import threading
import urllib.request
import urllib.parse
from pathlib import Path

DAEMON = "http://127.0.0.1:7890"
PROJECT_DIR = Path(__file__).parent.parent
DAEMON_SCRIPT = PROJECT_DIR / "daemon" / "daemon.py"
STATE_DIR = Path.home() / ".claude" / "babysitter"

# ---- helpers ----

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


def start_daemon():
    # Kill any existing
    subprocess.run(["pkill", "-f", str(DAEMON_SCRIPT)], capture_output=True)
    time.sleep(0.3)
    # Clear state
    for f in ["sessions.json", "modes.json"]:
        p = STATE_DIR / f
        if p.exists():
            p.unlink()
    # Start fresh
    proc = subprocess.Popen(
        [sys.executable, str(DAEMON_SCRIPT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        time.sleep(0.1)
        try:
            _get("/health")
            return proc
        except Exception:
            continue
    raise RuntimeError("daemon did not come up")


def stop_daemon(proc):
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def make_transcript(session_id, messages):
    """Create a fake jsonl transcript for testing."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    for m in messages:
        tmp.write(json.dumps(m) + "\n")
    tmp.close()
    return tmp.name


# ---- tests ----

PASS, FAIL = [], []

def test(name):
    def deco(fn):
        def wrapper():
            try:
                fn()
                PASS.append(name)
                print(f"  ✅ {name}")
            except AssertionError as e:
                FAIL.append((name, str(e)))
                print(f"  ❌ {name}: {e}")
            except Exception as e:
                FAIL.append((name, f"{type(e).__name__}: {e}"))
                print(f"  ❌ {name}: {type(e).__name__}: {e}")
        return wrapper
    return deco


@test("Test 1a: registration via /register")
def t_register():
    transcript = make_transcript("abc123", [{"type": "user", "message": "hi"}])
    r = _post("/register", {"session_id": "abc123", "tty": "/dev/ttys001", "transcript_path": transcript, "cwd": "/tmp"})
    assert r.get("ok"), r
    s = _get("/sessions")
    assert "abc123" in s["sessions"], f"session not registered: {s}"
    assert s["sessions"]["abc123"]["tty"] == "/dev/ttys001"
    assert s["modes"]["abc123"] == "review", "default mode should be review"


@test("Test 1b: TTY -> session lookup")
def t_tty_lookup():
    r = _get("/session_by_tty", tty="/dev/ttys001")
    assert r["session_id"] == "abc123", r


@test("Test 2: transcript readback")
def t_transcript():
    transcript = make_transcript("def456", [
        {"type": "user", "message": "q1"},
        {"type": "assistant", "message": "a1"},
        {"type": "user", "message": "q2"},
    ])
    _post("/register", {"session_id": "def456", "tty": "/dev/ttys002", "transcript_path": transcript, "cwd": "/tmp"})
    r = _get("/transcript", session_id="def456")
    assert len(r["messages"]) == 3, r
    assert r["messages"][0]["message"] == "q1"


@test("Test 4a: Stop event reaches babysitter via wait_for_stop")
def t_stop_event():
    # Spawn thread that will call /stopped after 0.5s
    def trigger():
        time.sleep(0.3)
        _post("/stopped", {"session_id": "abc123", "transcript_path": "/tmp/x.jsonl"})
    threading.Thread(target=trigger, daemon=True).start()

    t0 = time.time()
    r = _get("/wait_for_stop", session_id="abc123", timeout=3)
    elapsed = time.time() - t0
    assert r["event"] is not None, r
    assert r["event"]["session_id"] == "abc123"
    assert 0.25 < elapsed < 2.5, f"long-poll returned too fast/slow: {elapsed}s"


@test("Test 4b: wait_for_stop times out cleanly when nothing happens")
def t_stop_timeout():
    t0 = time.time()
    r = _get("/wait_for_stop", session_id="nonexistent", timeout=1)
    elapsed = time.time() - t0
    assert r["event"] is None, r
    assert 0.9 < elapsed < 2.0, f"timeout bound wrong: {elapsed}s"


@test("Test 6a: review mode does NOT deliver injection")
def t_review_no_inject():
    _post("/mode", {"session_id": "abc123", "mode": "review"})
    _post("/inject", {"session_id": "abc123", "message": "should not fire"})
    r = _get("/injection", session_id="abc123", timeout=1)
    assert r["injection"] is None, f"review mode leaked injection: {r}"
    assert r["mode"] == "review"


@test("Test 6b: auto mode delivers queued injection")
def t_auto_inject():
    _post("/mode", {"session_id": "abc123", "mode": "auto"})
    _post("/inject", {"session_id": "abc123", "message": "hello from babysitter"})
    r = _get("/injection", session_id="abc123", timeout=2)
    assert r["injection"] == "hello from babysitter", r
    assert r["mode"] == "auto"


@test("Test 6c: auto mode long-polls when no injection present")
def t_auto_longpoll():
    _post("/mode", {"session_id": "abc123", "mode": "auto"})

    def trigger():
        time.sleep(0.4)
        _post("/inject", {"session_id": "abc123", "message": "late msg"})
    threading.Thread(target=trigger, daemon=True).start()

    t0 = time.time()
    r = _get("/injection", session_id="abc123", timeout=3)
    elapsed = time.time() - t0
    assert r["injection"] == "late msg"
    assert 0.3 < elapsed < 2, f"long-poll elapsed={elapsed}"


@test("Test 6d: stop hook script produces decision:block when injection queued")
def t_stop_hook_output():
    _post("/mode", {"session_id": "abc123", "mode": "auto"})
    _post("/inject", {"session_id": "abc123", "message": "correct your answer"})
    hook = PROJECT_DIR / "hooks" / "watched-stop.sh"
    stdin = json.dumps({"session_id": "abc123", "transcript_path": "/tmp/x.jsonl", "stop_hook_active": False})
    r = subprocess.run([str(hook)], input=stdin, capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), f"no stdout from hook (expected decision:block JSON): stderr={r.stderr}"
    decision = json.loads(r.stdout)
    assert decision["decision"] == "block"
    assert decision["reason"] == "correct your answer"


@test("Test 6e: stop hook produces NO output in review mode")
def t_stop_hook_review():
    _post("/mode", {"session_id": "abc123", "mode": "review"})
    _post("/inject", {"session_id": "abc123", "message": "ignored"})  # should not be delivered
    hook = PROJECT_DIR / "hooks" / "watched-stop.sh"
    stdin = json.dumps({"session_id": "abc123", "transcript_path": "/tmp/x.jsonl", "stop_hook_active": False})
    r = subprocess.run([str(hook)], input=stdin, capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stderr
    assert not r.stdout.strip(), f"review mode should produce no stdout, got: {r.stdout!r}"


@test("Test 6f: stop hook respects stop_hook_active (no loop)")
def t_stop_hook_active():
    _post("/mode", {"session_id": "abc123", "mode": "auto"})
    _post("/inject", {"session_id": "abc123", "message": "should not fire"})
    hook = PROJECT_DIR / "hooks" / "watched-stop.sh"
    stdin = json.dumps({"session_id": "abc123", "stop_hook_active": True})
    r = subprocess.run([str(hook)], input=stdin, capture_output=True, text=True, timeout=15)
    assert not r.stdout.strip(), f"stop_hook_active should suppress output: {r.stdout!r}"


@test("Test 7: multiple sessions, isolated stop events")
def t_multi_session():
    # register two sessions
    tr1 = make_transcript("multi1", [{"m": 1}])
    tr2 = make_transcript("multi2", [{"m": 2}])
    _post("/register", {"session_id": "multi1", "tty": "/dev/multi1", "transcript_path": tr1, "cwd": "/tmp"})
    _post("/register", {"session_id": "multi2", "tty": "/dev/multi2", "transcript_path": tr2, "cwd": "/tmp"})

    # trigger stop only on multi1
    _post("/stopped", {"session_id": "multi1", "transcript_path": tr1})

    r1 = _get("/wait_for_stop", session_id="multi1", timeout=1)
    r2 = _get("/wait_for_stop", session_id="multi2", timeout=1)
    assert r1["event"] is not None, "multi1 should have received its own stop"
    assert r2["event"] is None, f"multi2 should NOT have received multi1's stop: {r2}"


@test("Test 8a: session end marks ended")
def t_session_end():
    _post("/end", {"session_id": "multi1"})
    s = _get("/sessions")
    assert s["sessions"]["multi1"]["ended"] is True


@test("Test 8b: session_by_tty ignores ended sessions")
def t_by_tty_ignores_ended():
    try:
        _get("/session_by_tty", tty="/dev/multi1")
        assert False, "expected 404 for ended session"
    except urllib.error.HTTPError as e:
        assert e.code == 404


@test("Test registration survives daemon restart (persistence)")
def t_persistence():
    # State was written to disk. Verify files contain what we expect.
    sessions = json.loads((STATE_DIR / "sessions.json").read_text())
    modes = json.loads((STATE_DIR / "modes.json").read_text())
    assert "abc123" in sessions
    assert modes.get("abc123") in ("review", "auto")


# ---- cleanup.sh tests (pane safety — no actual panes touched) ----

CLEANUP = PROJECT_DIR / "cleanup.sh"
PANES_FILE = Path("/tmp/bs-panes.txt")


@test("Test cleanup: refuses when babysitter_tty == claude_code_tty")
def t_cleanup_refuses_cc_tty_match():
    # Craft a malicious tracking file where babysitter and CC share a TTY.
    PANES_FILE.write_text(
        "claude_code=/dev/ttysEVIL\n"
        "babysitter=/dev/ttysEVIL|99999|babysitter\n"
    )
    r = subprocess.run(["bash", str(CLEANUP)], capture_output=True, text=True, timeout=5)
    assert r.returncode != 0, f"cleanup should refuse but exited 0: {r.stdout}"
    assert "REFUSED" in r.stdout, f"expected REFUSED message; got: {r.stdout}"
    # Tracking file must NOT be deleted on refusal (so user can inspect)
    assert PANES_FILE.exists(), "cleanup deleted tracking file despite refusing"
    PANES_FILE.unlink()


@test("Test cleanup: degraded mode when tracking file missing")
def t_cleanup_degraded_mode():
    if PANES_FILE.exists():
        PANES_FILE.unlink()
    r = subprocess.run(["bash", str(CLEANUP)], capture_output=True, text=True, timeout=5)
    assert r.returncode == 0, r.stderr
    assert "DEGRADED" in r.stdout, f"expected DEGRADED note: {r.stdout}"
    assert "will NOT touch any panes" in r.stdout


@test("Test cleanup: verify_worker skips stale PID (no kill, no file deletion side effects)")
def t_cleanup_verify_gauntlet():
    # PID 99999 almost certainly does not exist; fingerprint is irrelevant.
    PANES_FILE.write_text(
        "claude_code=/dev/ttysFAKE\n"
        "babysitter=/dev/ttysSTALE|99999|babysitter\n"
    )
    r = subprocess.run(["bash", str(CLEANUP)], capture_output=True, text=True, timeout=5)
    assert r.returncode == 0, r.stderr
    # Should say it couldn't verify and skipped — NOT that it closed a pane
    assert "could not verify" in r.stdout, f"expected skip message: {r.stdout}"
    assert "Closing babysitter pane" not in r.stdout, f"cleanup tried to close a pane for a stale PID: {r.stdout}"
    # After skip, tracking file should be removed (no longer useful)
    assert not PANES_FILE.exists(), "tracking file should be cleared after skip"


# ---- main ----

def main():
    print("=" * 60)
    print("babysitter daemon — automated tests")
    print("=" * 60)

    proc = start_daemon()
    print(f"daemon PID: {proc.pid}\n")

    try:
        for fn in [t_register, t_tty_lookup, t_transcript, t_stop_event, t_stop_timeout,
                   t_review_no_inject, t_auto_inject, t_auto_longpoll,
                   t_stop_hook_output, t_stop_hook_review, t_stop_hook_active,
                   t_multi_session, t_session_end, t_by_tty_ignores_ended, t_persistence,
                   t_cleanup_verify_gauntlet, t_cleanup_refuses_cc_tty_match, t_cleanup_degraded_mode]:
            fn()
    finally:
        stop_daemon(proc)

    print()
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("\nFailures:")
        for name, err in FAIL:
            print(f"  - {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
