#!/bin/bash
# launch-babysitter.sh <WATCHED_TTY>
#
# Invoked by the ⌘⇧M iTerm2 shortcut in a freshly-split pane.
# Responsibilities:
#   1. Auto-detect THIS pane's TTY (the babysitter pane).
#   2. Refuse to continue if our TTY equals the watched TTY (catastrophic mis-split).
#   3. Ensure daemon is running.
#   4. Resolve watched TTY -> session_id via daemon.
#   5. Record babysitter pane tuple (TTY | PID | FINGERPRINT) to /tmp/bs-panes.txt
#      so cleanup.sh can later verify + close us safely.
#   6. exec `claude` with the initial babysitter prompt.
#
# Pane management rules come from:
#   docs/pane-management.md (in this repo)
set -u

WATCHED_TTY="${1:-}"
if [ -z "$WATCHED_TTY" ]; then
  echo "ERROR: no watched TTY provided to launch-babysitter.sh" >&2
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAEMON="http://127.0.0.1:7890"
PANES_FILE="/tmp/bs-panes.txt"

# ---- Detect our own TTY (babysitter pane) ----
# The AppleScript shortcut split a new pane and ran this script in it. $$ is
# this bash process; its TTY is the babysitter pane's TTY.
MY_TTY_RAW=$(ps -o tty= -p $$ | tr -d ' ')
if [ -z "$MY_TTY_RAW" ] || [ "$MY_TTY_RAW" = "?" ]; then
  echo "ERROR: could not determine babysitter pane TTY" >&2
  exit 1
fi
MY_TTY="/dev/$MY_TTY_RAW"

# ---- Guard: never proceed if our pane is the watched pane ----
if [ "$MY_TTY" = "$WATCHED_TTY" ]; then
  echo "REFUSED: babysitter pane TTY ($MY_TTY) equals watched TTY. Something went wrong in the split."
  echo "Not launching; not writing tracking file."
  exit 1
fi

# ---- Ensure daemon is running ----
if ! /usr/bin/curl -s -m 1 "$DAEMON/health" >/dev/null 2>&1; then
  echo "Starting babysitter daemon..."
  nohup python3 "$PROJECT_DIR/daemon/daemon.py" >/dev/null 2>&1 &
  for i in 1 2 3 4 5; do
    sleep 0.3
    /usr/bin/curl -s -m 1 "$DAEMON/health" >/dev/null 2>&1 && break
  done
fi

# ---- Resolve watched TTY -> session_id ----
SESSION_ID=$(/usr/bin/curl -s -m 2 \
  "$DAEMON/session_by_tty?tty=$(/usr/bin/python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" "$WATCHED_TTY")" \
  | /usr/bin/python3 -c 'import json,sys
try:
  d=json.load(sys.stdin); print(d.get("session_id",""))
except Exception:
  pass')

# ---- Record babysitter pane tuple in the tracking file ----
# Format: one line per pane.
# claude_code=<watched_tty>   <- written by the AppleScript; untouchable
# babysitter=<tty>|<pid>|<fingerprint>
#
# $$ is this bash PID. We're about to `exec claude`, which REPLACES this
# process in place — so `claude` will inherit PID=$$. Recording $$ is correct.
#
# Fingerprint chosen narrow + stable: the claude binary plus our project path.
# "babysitter" in cwd uniquely identifies babysitters vs. other claude sessions.
FINGERPRINT="babysitter"
BABYSITTER_PID=$$

# Append our line. claude_code=... was already written by the AppleScript.
# If the panes file is missing for any reason, create it with just our line
# (cleanup.sh will then operate in degraded-mode: close babysitter only).
if [ ! -f "$PANES_FILE" ]; then
  echo "WARNING: $PANES_FILE missing (AppleScript didn't write it?). Creating with babysitter line only."
  : > "$PANES_FILE"
fi
echo "babysitter=${MY_TTY}|${BABYSITTER_PID}|${FINGERPRINT}" >> "$PANES_FILE"

# ---- Compose initial prompt ----
if [ -n "$SESSION_ID" ]; then
  INITIAL="You are the babysitter. Attach to session $SESSION_ID and begin the watch loop per CLAUDE.md. Current mode: review. Report findings here after each turn."
else
  INITIAL="You are the babysitter. No session was auto-detected for the spawning pane (TTY $WATCHED_TTY). Call list_sessions, show the user the options, and ask the user which to attach to."
fi

cd "$PROJECT_DIR" || exit 1

echo "📡 babysitter"
echo "   babysitter TTY: $MY_TTY  (pid $BABYSITTER_PID, fingerprint: $FINGERPRINT)"
echo "   watched TTY: $WATCHED_TTY"
if [ -n "$SESSION_ID" ]; then
  echo "   session:     ${SESSION_ID:0:8}..."
else
  echo "   session:     (not auto-detected — will prompt)"
fi
echo ""

# exec replaces this shell; PID stays $$; tracking file entry stays valid.
exec claude "$INITIAL"
