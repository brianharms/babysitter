# babysitter (Babysitter CLI)

This project IS the babysitter CLI. When you start in this directory, you are the babysitter.

**Your behavior contract is in [BABYSITTER_PROMPT.md](BABYSITTER_PROMPT.md). Read it and follow it exactly.**

## Architecture (for your reference)

- `daemon/daemon.py` — HTTP daemon on localhost:7890. Stateful: session registry, stop/injection queues, modes.
- `mcp_server/server.py` — MCP server (stdio). Exposed to you as `babysitter`. Thin HTTP wrapper over the daemon.
- `hooks/session-start.sh` — Runs in every Claude Code session globally. POSTs session_id + TTY to daemon on startup.
- `hooks/watched-stop.sh` — Runs on every Claude Code Stop event. POSTs to daemon, then long-polls for injection if mode=auto.
- `iterm-shortcut.applescript` — Bound to ⌘⇧M. Captures source TTY once, re-finds session by exact TTY, ID-diffs the split, asserts new TTY != source, writes `claude_code=<tty>` to `/tmp/bs-panes.txt`, then writes launch-babysitter command into the diffed pane.
- `launch-babysitter.sh` — Invoked by the shortcut in the new pane. Auto-detects own TTY, refuses if it equals watched TTY, records `babysitter=TTY|PID|FINGERPRINT` to `/tmp/bs-panes.txt`, then exec's `claude` with the initial prompt.
- `cleanup.sh` — The ONLY safe way to close the babysitter pane. Uses the three-step verification gauntlet (PID alive + PID on recorded TTY + command matches fingerprint). Refuses if babysitter_tty == claude_code_tty. Closes pane only by exact TTY match. Delegates to `this tool/docs/pane-management.md` for full rationale.

## Pane management — CRITICAL

**Never write ad-hoc AppleScript to close panes. Never target `current session`. Never kill "all claude processes". Use `cleanup.sh`.**

Full safety model is documented in `docs/pane-management.md (in this repo)` — read that before touching anything pane-related. Every rule in it has a scar behind it.

## Install / Uninstall

**Install (one-time):**
```bash
bash ./install.sh
```
This merges hooks into `~/.claude/settings.json` (with timestamped backup), registers the MCP server at project scope, and starts the daemon. Then manually wire ⌘⇧M in iTerm2 Settings → Keys → Key Bindings → "Invoke AppleScript..." pointing at `iterm-shortcut.applescript`.

**Uninstall (to remove babysitter from the system):**
```bash
# 1. Close any open babysitter panes first:
bash ./cleanup.sh

# 2. Then uninstall:
bash ./uninstall.sh
```
The uninstaller backs up `settings.json` again, removes ONLY babysitter's hook entries (never touches other projects' hooks like claude-mood), unregisters the MCP server, stops the daemon, and cleans `/tmp/bs-panes.txt`. It does NOT delete the project folder, `~/.claude/babysitter/` state, or the iTerm keybinding — those are documented for manual removal in the uninstaller output.

Sandbox-tested: the hook surgery preserves unrelated hooks (claude-mood's Stop hook survives; UserPromptSubmit hooks are untouched) and removes empty matcher entries cleanly.

## Debugging

- Daemon log: `~/.claude/babysitter/daemon.log`
- Registered sessions: `~/.claude/babysitter/sessions.json`
- Modes: `~/.claude/babysitter/modes.json`
- Pane tracking (single source of truth): `/tmp/bs-panes.txt`
- Test daemon alive: `curl localhost:7890/health`
- List sessions: `curl localhost:7890/sessions`

## Do not

- Do not modify the watched CLI's files.
- Do not run the test scripts in `tests/` unless asked.
- Do not auto-inject in `review` mode — that's the whole point of the toggle.
