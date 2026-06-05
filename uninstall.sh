#!/bin/bash
# babysitter uninstaller — reverse of install.sh.
#
# What this does:
#   1. Backs up ~/.claude/settings.json
#   2. Removes babysitter's SessionStart and Stop hook entries (only ours;
#      never touches other projects' hooks).
#   3. Removes the project-scoped MCP registration.
#   4. Stops the daemon if running.
#   5. Cleans up /tmp/bs-panes.txt.
#
# What this does NOT do:
#   - Delete the babysitter project folder (per the "never delete project
#     folders" rule). To archive, rename manually.
#   - Touch ~/.claude/babysitter/ state files. Delete those yourself if desired:
#       rm -rf ~/.claude/babysitter/
#   - Close any currently-open babysitter panes. Run ./cleanup.sh first if needed.
set -eu

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"

# ---- warn if a babysitter pane is currently tracked ----
if [ -f /tmp/bs-panes.txt ]; then
  echo "⚠️  /tmp/bs-panes.txt exists — a babysitter pane may still be open."
  echo "    Run: $PROJECT_DIR/cleanup.sh"
  echo "    Then re-run this uninstaller."
  exit 1
fi

# ---- back up settings.json ----
TS=$(date +%Y%m%d-%H%M%S)
cp "$SETTINGS" "$SETTINGS.bak-uninstall-$TS"
echo "✓ Backed up settings.json -> settings.json.bak-uninstall-$TS"

# ---- remove ONLY our hook entries ----
BABYSITTER_PROJECT_DIR="$PROJECT_DIR" python3 - <<'PY'
import json, pathlib, os

p = pathlib.Path.home() / ".claude/settings.json"
if not p.exists():
    print("  - no settings.json; nothing to remove")
    raise SystemExit(0)
d = json.loads(p.read_text() or "{}")
hooks = d.get("hooks", {})

PROJ = os.environ["BABYSITTER_PROJECT_DIR"]
# Match both the quoted form (current installer) and any older unquoted form.
OUR_COMMANDS = {
    f"{PROJ}/hooks/session-start.sh",
    f"{PROJ}/hooks/watched-stop.sh",
    f"{PROJ}/hooks/session-end.sh",
    f'"{PROJ}/hooks/session-start.sh"',
    f'"{PROJ}/hooks/watched-stop.sh"',
    f'"{PROJ}/hooks/session-end.sh"',
}

removed = 0
for event in ("SessionStart", "Stop", "SessionEnd"):
    arr = hooks.get(event, [])
    new_arr = []
    for entry in arr:
        entry_hooks = entry.get("hooks", [])
        kept = [h for h in entry_hooks if h.get("command") not in OUR_COMMANDS]
        dropped = len(entry_hooks) - len(kept)
        if dropped:
            removed += dropped
            print(f"  - {event}: removed {dropped} babysitter hook(s)")
        # Keep the matcher entry if it still has hooks; drop it if empty.
        if kept:
            entry["hooks"] = kept
            new_arr.append(entry)
        elif not entry_hooks:
            # Preserve an originally-empty matcher (don't accidentally create state)
            new_arr.append(entry)
    if new_arr != arr:
        if new_arr:
            hooks[event] = new_arr
        else:
            hooks.pop(event, None)

if removed == 0:
    print("  (no babysitter hooks found in settings.json — nothing to remove)")

p.write_text(json.dumps(d, indent=2))
PY

echo "✓ Hooks removed from ~/.claude/settings.json"

# ---- remove MCP registration (project scope) ----
echo ""
if command -v claude >/dev/null 2>&1; then
  (cd "$PROJECT_DIR" && claude mcp remove babysitter -s project 2>/dev/null) \
    && echo "✓ MCP server 'babysitter' unregistered (project scope)" \
    || echo "  (no project-scope MCP registration found — already clean)"
  # Also try user scope in case an older install put it there
  claude mcp remove babysitter -s user 2>/dev/null \
    && echo "✓ Also removed lingering user-scope MCP registration" \
    || true
else
  echo "⚠️  'claude' CLI not found; remove MCP manually:"
  echo "    cd $PROJECT_DIR && claude mcp remove babysitter -s project"
fi

# ---- stop daemon if running ----
if /usr/bin/curl -s -m 1 http://127.0.0.1:7890/health >/dev/null 2>&1; then
  pkill -f "babysitter/daemon/daemon.py" 2>/dev/null && echo "✓ Daemon stopped"
else
  echo "  (daemon not running)"
fi

# ---- clean /tmp tracking file if stale ----
rm -f /tmp/bs-panes.txt && echo "✓ /tmp/bs-panes.txt removed (if present)"

cat <<EOF

─────────────────────────────────────────────────────────────
babysitter uninstalled. Not touched (remove manually if desired):
  - $PROJECT_DIR/              (project folder)
  - ~/.claude/babysitter/    (sessions.json, modes.json, daemon.log)
  - iTerm2 ⌘⇧M keybinding     (Settings → Keys → Key Bindings → delete entry)
  - $SETTINGS.bak-*           (backups from install + uninstall)
─────────────────────────────────────────────────────────────
EOF
