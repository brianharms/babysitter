#!/bin/bash
# babysitter cleanup — close the babysitter pane and (optionally) stop the daemon.
#
# Mirrors the safety model in docs/pane-management.md (in this repo):
#   1. Read /tmp/bs-panes.txt (single source of truth).
#   2. Refuse if babysitter_tty == claude_code_tty.
#   3. verify_worker: PID alive + PID still on recorded TTY + command still matches fingerprint.
#      All three must pass. Any single failure = skip (do not kill, do not close).
#   4. Kill babysitter by verified PID (never `lsof -t tty | xargs kill`).
#   5. Close babysitter pane by EXACT TTY match (never `contains`, never `current session`,
#      never "close anything that isn't my TTY").
#   6. Delete tracking file.
#
# Usage:
#   cleanup.sh               -- close babysitter pane only
#   cleanup.sh --stop-daemon -- also stop the daemon
#
# Degraded mode: if the tracking file is missing, we do NOT touch any panes.
# At most, we offer to stop the daemon by PID.
set -u

PANES_FILE="/tmp/bs-panes.txt"
STOP_DAEMON=0

for arg in "$@"; do
  case "$arg" in
    --stop-daemon) STOP_DAEMON=1 ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

# -----------------------------------------------------------------------------
# verify_worker TTY PID FINGERPRINT
# -----------------------------------------------------------------------------
verify_worker() {
  local TTY="$1" PID="$2" FINGERPRINT="$3"

  # 1. PID alive
  if ! kill -0 "$PID" 2>/dev/null; then
    return 1
  fi

  # 2. PID still on recorded TTY
  local ACTUAL_TTY
  ACTUAL_TTY=$(ps -o tty= -p "$PID" 2>/dev/null | tr -d ' ')
  # ps reports bare "ttysNNN", tracking file has "/dev/ttysNNN"
  if [ "/dev/$ACTUAL_TTY" != "$TTY" ] && [ "$ACTUAL_TTY" != "$TTY" ]; then
    return 1
  fi

  # 3. Command still matches fingerprint
  if ! ps -o command= -p "$PID" 2>/dev/null | grep -q "$FINGERPRINT"; then
    return 1
  fi

  return 0
}

# -----------------------------------------------------------------------------
# close_pane_by_exact_tty TTY
# -----------------------------------------------------------------------------
close_pane_by_exact_tty() {
  local TARGET_TTY="$1"
  /usr/bin/osascript <<OSA
tell application "iTerm"
  set found to false
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if tty of s is equal to "$TARGET_TTY" then
          close s
          set found to true
          exit repeat
        end if
      end repeat
      if found then exit repeat
    end repeat
    if found then exit repeat
  end repeat
  if not found then return "Not found: $TARGET_TTY"
  return "Closed: $TARGET_TTY"
end tell
OSA
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if [ ! -f "$PANES_FILE" ]; then
  echo "No $PANES_FILE — nothing tracked."
  echo "DEGRADED MODE: will NOT touch any panes."
  if [ "$STOP_DAEMON" = "1" ]; then
    echo "Stopping daemon by fingerprint (pkill)..."
    pkill -f "babysitter/daemon/daemon.py" 2>/dev/null
  fi
  exit 0
fi

# Parse the tracking file.
CLAUDE_CODE_TTY=$(grep '^claude_code=' "$PANES_FILE" | head -1 | cut -d= -f2-)
BABYSITTER_LINE=$(grep '^babysitter=' "$PANES_FILE" | head -1 | cut -d= -f2-)

if [ -z "$BABYSITTER_LINE" ]; then
  echo "$PANES_FILE has no babysitter= line. Nothing to close."
  if [ "$STOP_DAEMON" = "1" ]; then
    pkill -f "babysitter/daemon/daemon.py" 2>/dev/null
  fi
  rm -f "$PANES_FILE"
  exit 0
fi

BABYSITTER_TTY=$(echo "$BABYSITTER_LINE" | cut -d'|' -f1)
BABYSITTER_PID=$(echo "$BABYSITTER_LINE" | cut -d'|' -f2)
BABYSITTER_FP=$(echo "$BABYSITTER_LINE" | cut -d'|' -f3)

# Guard (belt-and-suspenders on top of the launch-time refusal)
if [ -n "$CLAUDE_CODE_TTY" ] && [ "$BABYSITTER_TTY" = "$CLAUDE_CODE_TTY" ]; then
  echo "REFUSED: babysitter TTY ($BABYSITTER_TTY) equals claude_code TTY."
  echo "Something is wrong with the tracking file. Not touching anything."
  exit 1
fi

echo "babysitter cleanup:"
echo "  babysitter:     $BABYSITTER_TTY  (pid $BABYSITTER_PID, fingerprint: $BABYSITTER_FP)"
echo "  guarded CC:  ${CLAUDE_CODE_TTY:-<none>}"
echo ""

# Verification gauntlet
if verify_worker "$BABYSITTER_TTY" "$BABYSITTER_PID" "$BABYSITTER_FP"; then
  echo "✓ verified babysitter; killing PID $BABYSITTER_PID"
  kill -TERM "$BABYSITTER_PID" 2>/dev/null
  # Give it a moment before closing the pane
  sleep 0.3
  kill -0 "$BABYSITTER_PID" 2>/dev/null && kill -9 "$BABYSITTER_PID" 2>/dev/null
else
  echo "⚠ could not verify babysitter (pane closed / TTY recycled / process gone)"
  echo "  skipping kill AND pane close — tracking entry is stale"
  BABYSITTER_VERIFIED=0
  rm -f "$PANES_FILE"
  if [ "$STOP_DAEMON" = "1" ]; then
    pkill -f "babysitter/daemon/daemon.py" 2>/dev/null
    echo "daemon stopped"
  fi
  exit 0
fi

# Close the babysitter pane ONLY if we verified it above.
echo "Closing babysitter pane at $BABYSITTER_TTY..."
close_pane_by_exact_tty "$BABYSITTER_TTY"

# Remove tracking file.
rm -f "$PANES_FILE"

if [ "$STOP_DAEMON" = "1" ]; then
  echo "Stopping daemon..."
  pkill -f "babysitter/daemon/daemon.py" 2>/dev/null
fi

echo "cleanup complete."
