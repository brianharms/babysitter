#!/bin/bash
# babysitter babysitter self-prod Stop hook.
# Only registered in babysitter/.claude/settings.json — runs ONLY when the
# babysitter session itself stops. Asks the daemon whether to inject a "resume the
# loop" prod into the babysitter's stdin.
exec /usr/bin/python3 "$(dirname "$0")/babysitter-self-prod.py"
