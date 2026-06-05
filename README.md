# babysitter

> ## ⚠️ Before you start — what YOU (the human) must do
>
> An AI agent can run every command in this README, but a few things require **you**, because macOS and Apple security won't let any script do them. Read this first.
>
> **You need installed first:**
> - macOS + iTerm2
> - Python 3
> - the `claude` CLI on your PATH
>
> **Steps only a human can do (your AI agent will pause and ask for these):**
> - After `./install.sh`, **bind ⌘⇧M manually in iTerm2**: Settings → Keys → Key Bindings → + → ⌘⇧M → *Invoke AppleScript…* → paste the contents of `iterm-shortcut.applescript`.
> - **Edit one line** in that AppleScript so the launcher path points at where you cloned the repo (it's clearly marked `-- EDIT THIS LINE`).
> - Approve the **macOS Automation prompt** the first time it controls iTerm2 panes.

*A second Claude Code session that watches your first one — and either flags concerns or quietly corrects them.*

## What it does

`babysitter` runs a **second Claude Code session in a neighboring iTerm2 pane** whose only job is to watch your main ("watched") session work. After every turn the watched CLI completes, the babysitter reads the latest transcript and reviews it: factual errors, code that won't run, off-task drift, skipped verification, violations of your global `CLAUDE.md` rules. Then it does one of two things depending on its mode:

- **`review`** (default) — prints a terse finding to you (`✅ Turn looked good.` or `⚠️ Concern: …`) and keeps watching. Read-only; it never touches the watched session.
- **`auto`** — when it spots a problem, it injects corrective feedback *back into the watched CLI* as if you had typed it, so the watched session course-corrects on its own.

The plumbing is a small local HTTP daemon (port `7890`) that brokers between the two sessions via Claude Code hooks: a `SessionStart` hook registers each session, a `Stop` hook fires on every completed turn, and the babysitter long-polls the daemon for those stop events. In `auto` mode the watched session's `Stop` hook holds open briefly and returns `{"decision":"block","reason":…}` — Claude Code treats that as fresh user input.

**Maturity: advanced / power-user, and rough around the edges.** This is macOS + iTerm2 + AppleScript glue that controls terminal panes. The pane-safety model is solid and battle-tested (see `docs/pane-management.md`), but setup involves editing an AppleScript path by hand, wiring a keybinding through the iTerm2 GUI, and trusting a daemon to broker between two live Claude sessions. It works well once it's set up — it is not a one-click install. If you're not comfortable reading shell scripts before you run them, this probably isn't for you yet.

## Features

- **Live turn-by-turn review** of a watched Claude Code session, driven by Claude Code's own `Stop` hook.
- **Two modes** — `review` (observe + report) and `auto` (inject corrections), switchable at runtime via the `set_mode` MCP tool.
- **MCP server** exposing seven tools to the babysitter: `list_sessions`, `attach`, `wait_for_stop`, `get_transcript`, `queue_injection`, `set_mode`, `get_mode`.
- **Long-poll daemon** (`:7890`) with a per-session stop queue that **drains backlogged events** so the babysitter never falls behind during bursts (reports `dropped: N`).
- **60s synthetic heartbeat** — the daemon ticks every quiet session once a minute so the babysitter can emit visible proof-of-life and never drift more than ~60s behind a real stop.
- **Self-prod safety net** — a `Stop` hook scoped to the babysitter's own session asks the daemon "did you just call `wait_for_stop`?" If not, it nudges the babysitter back into its watch loop (rate-limited to 5 prods / 120s).
- **`[PAUSE-WATCH Xm]` sentinel** — the babysitter emits this when you ask it to step out for a human conversation; the daemon suppresses self-prods for that window.
- **Defensive iTerm2 pane management** — before/after session-ID diffing to find the right pane, TTY+PID+fingerprint verification before any close, and a hard refusal to ever close the watched (CC) pane. Documented exhaustively in `docs/pane-management.md`.
- **`⌘⇧M` launcher** — one keystroke splits the current pane and attaches a babysitter to the session running there.
- **Clean install / uninstall** — `install.sh` merges only its own hooks (backing up `settings.json` first) and registers the MCP server at *project scope* so its tools never leak into your other sessions; `uninstall.sh` reverses exactly that.
- **Automated + manual test suites** — `tests/test_daemon.py` exercises the full HTTP/hook surface (18 tests incl. the pane-safety verification gauntlet); `tests/test_manual.md` covers the parts that need a real `claude` CLI and your eyes.

## Requirements

- **macOS** with **iTerm2** (the pane control is iTerm2 AppleScript; it will not work in Terminal.app or other terminals).
- **Python 3** on your `PATH` (`python3`). The daemon and hooks use only the standard library; the MCP server needs the [`mcp`](https://pypi.org/project/mcp/) SDK, which `install.sh` installs into a project-local `.venv`.
- **Claude Code CLI** (`claude`) on your `PATH`, with hooks and MCP support.
- **`curl`** (system `/usr/bin/curl`) and **`osascript`** — both ship with macOS.
- A free **TCP port `7890`** on `127.0.0.1` for the daemon.
- This is a standalone vibekit project — **no sibling vibekit repo is required.** The bundled `docs/pane-management.md` describes the same pane-safety discipline used by the author's voice-tunnel tooling (it references that project's `scripts/launch.sh` / `cleanup.sh` as the canonical reference); babysitter's own equivalents are `launch-babysitter.sh` and `cleanup.sh` at the repo root.

## Setup / Install

> You can clone this repo **anywhere** — `install.sh`, `uninstall.sh`, `launch-babysitter.sh`, and `cleanup.sh` all auto-detect their own location, so no path-editing is needed for the shell side. The **only** spot that needs a manual path is the iTerm2 AppleScript (step 4), because it's pasted into iTerm2's GUI where the script can't see where it lives.

**1. Clone the repo.**

```bash
git clone https://github.com/brianharms/babysitter.git
cd babysitter
```

**2. Run the installer.** It creates `~/.claude/settings.json` if missing (or backs up the existing one), merges the `SessionStart` / `Stop` / `SessionEnd` hooks (idempotent — skips anything already present), creates `.venv` and installs dependencies from `requirements.txt`, generates a machine-local `.mcp.json` from the bundled template, registers the MCP server at **project scope**, and starts the daemon:

```bash
./install.sh
```

You should see `✓ Daemon running` and a `{"ok": true, ...}` health response. (The `claude` CLI must be on your `PATH` for the MCP registration step; if it isn't, the installer prints the exact command to run by hand.)

**3. Wire up the `⌘⇧M` keybinding (one-time, manual — iTerm2 has no CLI for this).**

1. iTerm2 → **Settings → Keys → Key Bindings → `+`**
2. Keyboard Shortcut: **`⌘⇧M`**
3. Action: **Invoke AppleScript…** (not "Run Coprocess")
4. Paste the **entire contents of `iterm-shortcut.applescript`**.
5. Save.

**4. Set the launcher path inside the AppleScript.** Near the top, the script has a clearly-marked line you must edit so it points at *your* clone:

```applescript
-- EDIT THIS LINE: set it to the absolute path of launch-babysitter.sh in your clone.
set launcherPath to (POSIX path of (path to home folder)) & "babysitter/launch-babysitter.sh"
```

The default assumes you cloned to `~/babysitter`. If you cloned elsewhere, change the string after `path to home folder` to the correct path (or replace the whole expression with an absolute POSIX path). This is the single most common setup mistake — the rest of the install is automatic.

**5. Verify.** Open a fresh iTerm2 pane, run `claude`, interact for one turn so a session registers, then hit `⌘⇧M`. A vertical split should open with a babysitter attaching to that session.

To uninstall later (removes only babysitter's hooks + MCP registration, backs up settings, stops the daemon; **never** deletes the repo or your state):

```bash
./uninstall.sh
```

## Usage

**Day-to-day:**

1. In an iTerm2 pane, run `claude` as you normally would — this is your **watched** session.
2. Do at least one turn so the `SessionStart` hook registers the session with the daemon.
3. Hit **`⌘⇧M`**. A pane splits to the right and a babysitter attaches to that session, starting in **`review`** mode. It auto-resolves the watched session from the pane's TTY; if it can't, it lists candidates and asks you which to attach to.
4. Work normally in the watched pane. After each turn, the babysitter prints a one-line verdict in its pane and a `🫀` heartbeat once a minute while idle.

**Switching to auto-correct:** tell the babysitter `switch to auto mode`. It calls `set_mode(session_id, "auto")`; from then on, concerns it finds are injected back into the watched session as corrective follow-ups. Say `back to review` to stop injecting (queued injections are drained on the flip).

**Pausing the watch:** tell the babysitter something like `stop watching for a bit` or `let's debug this without you watching`. It emits `[PAUSE-WATCH 30m]` and the daemon suppresses its loop-resume prods for that window. Continuing the loop needs no sentinel.

**Stopping:** tell the babysitter `stop watching` / `exit`, or just close its pane safely:

```bash
~/babysitter/cleanup.sh                 # close ONLY the babysitter pane; daemon survives
~/babysitter/cleanup.sh --stop-daemon   # also stop the daemon
```

`cleanup.sh` will **refuse** to touch a pane it can't verify by TTY + PID + fingerprint, and will never close the watched (CC) pane. If `/tmp/bs-panes.txt` is missing it enters degraded mode and touches no panes at all.

**Health check / debugging:**

```bash
curl -s localhost:7890/health        # {"ok": true, "sessions": N}
curl -s localhost:7890/sessions      # registered sessions + their modes
tail -f ~/.claude/babysitter/daemon.log
python3 tests/test_daemon.py          # full automated suite
```

State lives in `~/.claude/babysitter/` (`sessions.json`, `modes.json`, `daemon.log`).

## For AI coding agents

You're working **on** this repo. Orient yourself here first.

**Repo layout (top level):**

```
BABYSITTER_PROMPT.md        # The babysitter's operating contract. READ THIS FIRST.
docs/pane-management.md     # The pane-safety bible. Every rule has a scar behind it.
daemon/daemon.py            # HTTP daemon on :7890 — session registry, stop queues,
                            #   injection queues, mode store, 60s heartbeat, self-prod logic.
mcp_server/server.py        # FastMCP server: the 7 tools the babysitter calls.
hooks/                      # session-start.{sh,py}  watched-stop.{sh,py}
                            #   session-end.{sh,py}   babysitter-self-prod.{sh,py}
                            #   (.sh wrappers capture TTY / shell out; .py do the work)
launch-babysitter.sh        # Spawned by ⌘⇧M in the new pane; detects its TTY, resolves
                            #   the watched session, writes /tmp/bs-panes.txt, execs claude.
iterm-shortcut.applescript  # The ⌘⇧M handler: ID-diff split, write launch cmd. (.scpt = compiled)
cleanup.sh / install.sh / uninstall.sh
tests/test_daemon.py        # 18 automated tests over the HTTP/hook surface.
tests/test_manual.md        # Tests needing a real claude CLI + human eyes.
.claude/settings.json       # Project-local: registers ONLY the self-prod Stop hook.
.mcp.json                   # Project-scoped MCP registration (regenerated by install.sh).
```

Note: the in-repo `CLAUDE.md` (the babysitter's protocol doc, referenced by the launcher prompt) is **gitignored** and not shipped publicly. `BABYSITTER_PROMPT.md` is the canonical, public version of that protocol — treat the two as the same contract.

**Entry points & data flow:** Claude Code hooks POST to the daemon (`/register`, `/stopped`, `/end`, `/babysitter_stopped`). The babysitter, via `mcp_server/server.py`, calls the daemon's GET endpoints (`/wait_for_stop`, `/transcript`, `/sessions`) and POST (`/inject`, `/mode`). The watched session's `Stop` hook long-polls `/injection` and, in `auto` mode, prints `{"decision":"block","reason":…}` to stdout.

**Build / run / test:**

```bash
./install.sh                          # set up venv, hooks, MCP registration, start daemon
python3 daemon/daemon.py              # run the daemon in the foreground (debugging)
python3 tests/test_daemon.py          # automated suite — must pass before any change ships
# manual/live tests: see tests/test_manual.md
```

**Invariants — do not break these:**

- **Port `7890` is load-bearing.** It's hardcoded in `daemon.py` (`PORT`), `mcp_server/server.py` (`DAEMON`), all four hooks, and both shell scripts' health checks. Change it in *every* place or not at all.
- **Respect `docs/pane-management.md` absolutely.** Never write inline AppleScript to close panes, never target `current session` / `front window`, never kill "all the pythons" or "all claudes", never `lsof -t <tty> | xargs kill`. Open panes only via the ID-diff dance; close them only via `cleanup.sh`'s TTY+PID+fingerprint gauntlet. The guard that refuses to close the watched (`claude_code=`) TTY is sacred.
- **`/tmp/bs-panes.txt` is the single source of truth for panes.** Format: `claude_code=<tty>` (untouchable) + `babysitter=<tty>|<pid>|<fingerprint>`. The fingerprint is `babysitter`. Missing file ⇒ degraded mode ⇒ touch no panes. Don't delete it without tearing down first.
- **The `wait_for_stop` loop contract is the product.** Per `BABYSITTER_PROMPT.md`, *every* babysitter turn must end with a `wait_for_stop` call. The self-prod hook (`babysitter-self-prod.py` → daemon `/babysitter_stopped`) is the safety net, not a license to drop the loop.
- **`[PAUSE-WATCH Xm]` is a parsed sentinel**, not decoration. The daemon regex-matches it (`_extract_pause_minutes`, clamped 1–240) to suppress prods. Keep the exact literal form `[PAUSE-WATCH Xm]`.
- **`review` vs `auto` is enforced daemon-side.** `/inject` and `/injection` drop messages unless mode is `auto`. Don't move that gate to the client.
- **Hooks must never block session startup.** All hooks swallow daemon-down errors and `return 0`. Keep timeouts short (2s register, ~8s injection long-poll) and never let a hook hang a real Claude session.
- **MCP stays at project scope.** Registering it globally would inject these seven tools into every Claude Code session you run. `install.sh` / `uninstall.sh` deliberately keep it project-scoped — preserve that.
- **`.env`, `certs/`, `*.pem/*.key/*.p12`, `*.log`, `*.jsonl`, and `.claude/` (beyond `settings.json`) are gitignored. Never commit secrets, transcripts, or runtime state.**

## License

MIT © 2026 Brian Harms / Ritual Industries — [ritual.industries](https://ritual.industries)
