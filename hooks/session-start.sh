#!/bin/bash
# babysitter SessionStart hook.
# Captures the iTerm pane TTY synchronously, then runs the python helper
# synchronously too. The daemon path is localhost with a 2s timeout, so this
# is fast when the daemon is up. If the daemon is down, curl/urlopen fails
# cleanly — the session still starts (python returns 0).

TTY_RAW=$(ps -o tty= -p "$PPID" 2>/dev/null | tr -d ' ')
if [ -z "$TTY_RAW" ] || [ "$TTY_RAW" = "?" ] || [ "$TTY_RAW" = "??" ]; then
  TTY=""
else
  TTY="/dev/$TTY_RAW"
fi

INPUT=$(cat)
echo "$INPUT" | CLAUDE_WATCH_TTY="$TTY" /usr/bin/python3 "$(dirname "$0")/session-start.py"
exit 0
