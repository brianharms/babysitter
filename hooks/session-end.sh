#!/bin/bash
# babysitter SessionEnd hook.
# Fires when a Claude Code session terminates. POSTs to the daemon so the
# session is marked ended and stops being a candidate in /session_by_tty.
INPUT=$(cat)
echo "$INPUT" | /usr/bin/python3 "$(dirname "$0")/session-end.py"
exit 0
