#!/bin/bash
# babysitter Stop hook — delegates to watched-stop.py
# stdin: Claude Code hook event JSON
# stdout: either nothing (normal stop) or {"decision":"block","reason":"..."}
exec /usr/bin/python3 "$(dirname "$0")/watched-stop.py"
