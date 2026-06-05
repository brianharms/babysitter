#!/bin/bash
# babysitter installer — adds SessionStart + Stop hooks to ~/.claude/settings.json,
# registers the MCP server, generates .mcp.json, prints the ⌘⇧M keybinding instructions.
set -eu

# Auto-detect the repo location from where THIS script lives — works no matter
# where the user cloned the repo (no hardcoded path).
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="$HOME/.claude/settings.json"

# ---- ensure ~/.claude/settings.json exists ----
mkdir -p "$HOME/.claude"
if [ ! -f "$SETTINGS" ]; then
  echo "{}" > "$SETTINGS"
  echo "✓ Created $SETTINGS (was missing — first-time Claude Code setup)"
else
  TS=$(date +%Y%m%d-%H%M%S)
  cp "$SETTINGS" "$SETTINGS.bak-$TS"
  echo "✓ Backed up settings.json -> settings.json.bak-$TS"
fi

# ---- merge hooks ----
# PROJECT_DIR is passed into Python via the environment, not interpolated as a literal.
BABYSITTER_PROJECT_DIR="$PROJECT_DIR" python3 - <<'PY'
import json, pathlib, os

p = pathlib.Path.home() / ".claude/settings.json"
d = json.loads(p.read_text() or "{}")
hooks = d.setdefault("hooks", {})

PROJ = os.environ["BABYSITTER_PROJECT_DIR"]
# Claude Code runs hook commands through /bin/sh -c, so paths with spaces
# must be quoted.
SS_CMD = f'"{PROJ}/hooks/session-start.sh"'
STOP_CMD = f'"{PROJ}/hooks/watched-stop.sh"'
SE_CMD = f'"{PROJ}/hooks/session-end.sh"'

def ensure_hook(event, command):
    arr = hooks.setdefault(event, [])
    # Find/create a "no matcher" entry
    target = None
    for entry in arr:
        if "matcher" not in entry:
            target = entry
            break
    if target is None:
        target = {"hooks": []}
        arr.append(target)
    # Skip if our command is already installed
    for h in target.get("hooks", []):
        if h.get("command") == command:
            print(f"  - {event}: already installed, skipping")
            return
    target.setdefault("hooks", []).append({"type": "command", "command": command})
    print(f"  + {event}: appended babysitter hook")

ensure_hook("SessionStart", SS_CMD)
ensure_hook("Stop", STOP_CMD)
ensure_hook("SessionEnd", SE_CMD)

p.write_text(json.dumps(d, indent=2))
PY

echo "✓ Hooks installed in ~/.claude/settings.json"

# ---- create venv + install deps ----
VENV_PY="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "  creating venv + installing deps (requirements.txt)..."
  python3 -m venv "$PROJECT_DIR/.venv"
  "$PROJECT_DIR/.venv/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt"
fi

# ---- generate .mcp.json from the template, with the real path ----
if [ -f "$PROJECT_DIR/.mcp.json.template" ]; then
  sed "s#__PROJECT_DIR__#$PROJECT_DIR#g" "$PROJECT_DIR/.mcp.json.template" > "$PROJECT_DIR/.mcp.json"
  echo "✓ Generated .mcp.json for this machine"
fi

# ---- register MCP server at PROJECT scope (babysitter-only) ----
echo ""
echo "Registering MCP server 'babysitter' at project scope (babysitter-only)..."
# Project scope = only loads when cwd is babysitter/. Keeps these tools
# out of every other Claude Code session's tool list.
if command -v claude >/dev/null 2>&1; then
  (cd "$PROJECT_DIR" && claude mcp remove babysitter -s project 2>/dev/null || true)
  (cd "$PROJECT_DIR" && claude mcp add babysitter -s project -- "$VENV_PY" "$PROJECT_DIR/mcp_server/server.py")
  echo "✓ MCP server 'babysitter' registered at project scope"
  echo "  (only available when cwd = babysitter/)"
else
  echo "⚠️  'claude' CLI not found on PATH; register MCP manually:"
  echo "    cd \"$PROJECT_DIR\" && claude mcp add babysitter -s project -- \"$VENV_PY\" \"$PROJECT_DIR/mcp_server/server.py\""
fi

# ---- start daemon ----
if ! /usr/bin/curl -s -m 1 http://127.0.0.1:7890/health >/dev/null 2>&1; then
  echo "Starting daemon..."
  nohup python3 "$PROJECT_DIR/daemon/daemon.py" >/dev/null 2>&1 &
  sleep 1
fi
/usr/bin/curl -s http://127.0.0.1:7890/health && echo ""
echo "✓ Daemon running"

# ---- keybinding instructions ----
cat <<EOF

─────────────────────────────────────────────────────────────
⌘⇧M keybinding — one-time manual setup in iTerm2
─────────────────────────────────────────────────────────────
  1. iTerm2 → Settings → Keys → Key Bindings → "+"
  2. Keyboard Shortcut: ⌘⇧M
  3. Action: "Invoke AppleScript..."
  4. Paste the contents of:
       $PROJECT_DIR/iterm-shortcut.applescript
  5. Save.

  Test: open a new iTerm pane, run \`claude\` in it, then hit ⌘⇧M.
  A vertical split should open with the babysitter attaching.

To close the babysitter pane safely:
    $PROJECT_DIR/cleanup.sh
    $PROJECT_DIR/cleanup.sh --stop-daemon   (also stops the daemon)

To run the automated test suite:
    cd "$PROJECT_DIR" && python3 tests/test_daemon.py
EOF
