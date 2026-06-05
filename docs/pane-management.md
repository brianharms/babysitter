# iTerm2 Pane Management — How this tool opens and closes panes safely

This document describes the **exact** rules and mechanisms this project uses to open and close iTerm2 panes without ever destroying unrelated work. It exists because ad-hoc AppleScript sweeps ("close anything that isn't my TTY", "kill every Python process I see") have repeatedly destroyed live CC sessions in adjacent windows. Everything here is defensive by design.

> **TL;DR — if you take nothing else away:**
> - **Open panes with `scripts/launch.sh` (or `scripts/iterate-launch.sh`).**
> - **Close panes with `scripts/cleanup.sh`.**
> - **Never write inline AppleScript to close panes. Never target `current session`. Never kill "all the Pythons".**

---

## The three tracking files

Everything is coordinated through three files on `/tmp`. These are the ground truth — if a file is missing, the corresponding teardown path is skipped, not guessed.

| File | Who writes it | What's in it | Who reads it |
|---|---|---|---|
| `/tmp/cv-panes.txt` | `launch.sh`, `iterate-launch.sh` | `claude_code=/dev/ttysNNN`, `daemon=ttysNNN\|PID\|FINGERPRINT`, `worker=ttysNNN` | `cleanup.sh`, `WorkerManager._spawn_iterm_pane` |
| `/tmp/cv-worker-panes.txt` | `WorkerManager._save_worker_panes` (runtime) | `<project>=<TTY>\|<PID>\|<FINGERPRINT>` per dynamically-spawned sub-worker | `cleanup.sh`, `WorkerManager._preflight_cleanup` |
| `/tmp/cv-v06-daemon-tty` | `iterate-launch.sh` | Single line: the daemon pane's TTY | iterative test loop teardown |

**The guard invariant**: every teardown path refuses to close a TTY that matches the `claude_code=` line. The CC TTY is recorded at launch time and is treated as untouchable.

---

## Opening panes

### Three entry points — pick the right one

| Script | Produces | Use when |
|---|---|---|
| `scripts/launch.sh <cc_tty>` | Daemon pane + (optional) worker pane | Normal local voice sessions. The default. |
| `scripts/launch.sh <cc_tty>` with `CV_MCP_WORKER=1` | Daemon pane only (`WorkerManager` spawns workers on demand) | v0.6 MCP mode — the channel-server worker is created per-dispatch, not at launch. |
| `scripts/iterate-launch.sh <cc_tty>` | Daemon pane only (v0.6 entry point, no `--local --headphones`) | The iterative test loop (`docs/superpowers/specs/2026-04-11-iterative-test-loop-design.md`). |

The `<cc_tty>` argument is **always** the currently-running CC's TTY. **Auto-detect it, never ask the user:**
```bash
MY_TTY=$(ps -o tty= -p $PPID | xargs)
scripts/launch.sh "$MY_TTY"
```

### How launch.sh identifies a new pane reliably

The critical move is the **before/after session-ID diff**. Every reliable spawn does this dance:

```
1. Find the CC session by exact TTY match (walk windows → tabs → sessions).
2. Snapshot the set of session IDs in the CC's tab → `beforeIDs`.
3. Tell the CC session (not `current session`, not `front window`) to split.
4. Walk the tab's sessions again; the one whose ID is not in `beforeIDs` is the new pane.
5. Assert the new pane's TTY is not the CC TTY. Abort if it is.
6. `write text` the daemon command into the diffed pane.
7. Return the new pane's TTY. Record it in /tmp/cv-panes.txt.
```

Two things this avoids:

- **`current session` is a trap.** It resolves to whatever is focused at execution time, not the intended pane. If the user Cmd+Tabs mid-split, you write the daemon command into the wrong pane.
- **"The most recent session" heuristics are a trap.** iTerm2 session ordering is not stable across splits. Only ID diffing is safe.

When a second pane is needed (legacy mode's worker), `launch.sh` re-snapshots IDs between splits so the two diffs can't alias.

### Command-fingerprint capture

After the daemon spawns, `launch.sh` waits a moment, then uses `lsof -t <tty>` to capture the daemon's PID and records it alongside a command fingerprint (`"m daemon --local"`). That tuple — **TTY + PID + fingerprint** — is how `cleanup.sh` later verifies ownership.

The fingerprint is not the full command string; it's a substring chosen to be stable across Python minor versions and Homebrew vs system interpreter differences. Specifically: Homebrew Python's argv[0] is `.../Python`, not `python3`, so `pkill -f python3 -m daemon` silently never matched. We key on `"m daemon --local"` instead.

---

## Closing panes

### `cleanup.sh` — the only correct way

```bash
scripts/cleanup.sh
```

It does this, in order:

1. **Read `/tmp/cv-panes.txt`.** If missing: `pkill -9 -f "m daemon --local"`, **do not touch any panes**, exit. This is the single safest degraded mode.
2. **Parse daemon, worker, and claude_code TTYs** from the file.
3. **Guard check.** If `daemon_tty == claude_code_tty` or `worker_tty == claude_code_tty`, print "REFUSED" and bail. This is paranoia-level belt-and-suspenders on top of the launch-time guard.
4. **Kill the daemon by verified PID** using `verify_worker()` — PID alive, PID still on the recorded TTY, command still matches fingerprint. If any check fails, fall back to `pkill -f "m daemon --local"`. **Never** use `lsof -t <tty> | xargs kill` — that kills whatever process was reassigned to the TTY.
5. **Close worker pane first, then daemon pane.** Two separate AppleScript passes. Closing both in one pass can race with iTerm2's session-list reshuffle and miss the second pane.
6. **AppleScript close logic** walks all windows → tabs → sessions looking for `tty of s is equal to <target>` — strict equality, not `contains`. If found, `close s`. If not found, returns "Not found:" without error.
7. **Delete `/tmp/cv-panes.txt`.**
8. **Worker-panes sweep.** Read `/tmp/cv-worker-panes.txt` (populated by `WorkerManager` when it spawns dynamic sub-workers). For each entry, run the same PID+TTY+fingerprint verification before killing and closing. Skip anything that doesn't verify. Delete the file.

### `verify_worker()` — the trust gate

```bash
# From cleanup.sh. Same three-step check exists in daemon/worker_manager.py:86 (_is_our_worker).
verify_worker() {
  local TTY="$1" PID="$2" FINGERPRINT="$3"
  kill -0 "$PID" 2>/dev/null || return 1                 # 1. PID alive
  local ACTUAL_TTY=$(ps -o tty= -p "$PID" | tr -d ' ')   # 2. PID still on recorded TTY
  [ "$ACTUAL_TTY" = "${TTY#/dev/}" ] || return 1
  ps -o command= -p "$PID" | grep -q "$FINGERPRINT"      # 3. Command still matches
}
```

All three must pass. Any single failure means: the pane was closed, the TTY was recycled to a shell the user is typing in, or the process crashed and something else took its slot. **Skip it** — don't kill, don't close.

---

## `WorkerManager` (runtime) — dynamic sub-worker panes

Beyond the daemon pane, the v0.6 pipeline spawns on-demand Claude Code workers when a voice tool requests `dispatch_worker(project=...)`. These panes are created **at runtime** by `daemon/worker_manager.py`, not by `launch.sh`.

### Spawn (`_spawn_iterm_pane`, worker_manager.py:620)

Same ID-diff technique as `launch.sh`:
1. Read CC TTY from `/tmp/cv-panes.txt`. If missing, refuse to spawn.
2. Find the CC session by TTY contains-match.
3. Snapshot `beforeIDs` of sessions in the CC's tab.
4. `split vertically` from the CC session.
5. Diff to find the new pane.
6. `write text` the worker command (`cvwrapper-expect claude ... --dangerously-load-development-channels server:voice-channel`).
7. Return the new pane's TTY.

The WorkerManager then records `worker.tty`, `worker.pid`, and `worker.fingerprint = "claude.*--settings.*voice"` on its `WorkerInstance`, and calls `_save_worker_panes()` to persist them to `/tmp/cv-worker-panes.txt`. Any subsequent daemon crash/restart picks these up via `_preflight_cleanup`.

### Pre-flight cleanup on daemon startup (`_preflight_cleanup`, worker_manager.py:129)

When the daemon restarts after a crash, sub-worker panes from the previous run are still open. The tracking file survives because it's on `/tmp`. At startup:

1. Read `/tmp/cv-worker-panes.txt`.
2. For each entry, require the new format `TTY|PID|FINGERPRINT`. Skip old-format entries with a warning — **never kill blindly** based on just a TTY.
3. Run `_is_our_worker(tty, pid, fingerprint)` — same three checks.
4. If verified: `SIGTERM` the PID, `osascript` close the pane by TTY, remove the `.mcp.json` entry the worker added, log the cleanup.
5. If any check fails: log a "pane gone or recycled" message and move on.

### Graceful teardown at daemon exit (`close_all`, worker_manager.py:883)

Symmetric to pre-flight:

1. Cancel all SSE listener tasks.
2. For each tracked worker: verify `_is_our_worker`, `SIGTERM` if verified, close the pane by exact-TTY match.
3. Remove `.mcp.json` entries.
4. Delete `/tmp/cv-worker-panes.txt`.

---

## The full lifecycle

```
┌──────────────────────────────────────────────────────────────────┐
│  User runs: scripts/launch.sh $(ps -o tty= -p $PPID | xargs)     │
├──────────────────────────────────────────────────────────────────┤
│  1. launch.sh auto-detects CC TTY from the calling shell.        │
│  2. AppleScript finds CC session by exact TTY match.             │
│  3. Snapshots session IDs before split.                          │
│  4. Splits → diffs → new pane = daemon.                          │
│  5. Writes daemon command into diffed pane.                      │
│  6. sleep 2, captures daemon PID via lsof on the daemon TTY.     │
│  7. Writes /tmp/cv-panes.txt with TTY + PID + fingerprint.       │
│                                                                   │
│  ━━━ session runs ━━━                                            │
│                                                                   │
│  If voice tool calls dispatch_worker(project=X):                 │
│    WorkerManager._spawn_iterm_pane reads /tmp/cv-panes.txt for   │
│    the CC TTY (not current session!), splits from CC, diffs IDs, │
│    captures worker PID, writes /tmp/cv-worker-panes.txt.         │
│                                                                   │
│  ━━━ user says "end session" / triggers teardown ━━━              │
│                                                                   │
│  User runs: scripts/cleanup.sh                                   │
│  1. Reads /tmp/cv-panes.txt. Refuses if daemon TTY == CC TTY.    │
│  2. Verifies daemon PID+TTY+fingerprint. Kills by PID if match;  │
│     else pkill -f "m daemon --local". Never lsof-kills the TTY.  │
│  3. AppleScript closes worker pane, then daemon pane, by exact   │
│     TTY equality. Does not close the guarded CC TTY.             │
│  4. Sweeps /tmp/cv-worker-panes.txt with the same verification   │
│     gauntlet. Skips anything that doesn't verify.                │
│  5. Deletes both tracking files.                                 │
└──────────────────────────────────────────────────────────────────┘
```

---

## Things you must never do

Every rule below has a scar behind it.

- **Never write inline AppleScript to close panes.** It will close the user's CC window the first time a TTY gets recycled.
- **Never reference `current session`, `current tab`, or `front window`** to locate a target pane. Always find the session by TTY equality.
- **Never close panes by "any session that isn't my TTY" logic.** Other iTerm2 windows may belong to unrelated work.
- **Never close panes by "contains TTY substring"** when exact-match is an option — `ttys001` is a substring of `ttys0010`. Use equality (`is equal to`) wherever possible; `contains` is acceptable only during the initial CC-session lookup where the TTY argument is a known bare `ttysNNN`.
- **Never kill processes by TTY alone** (e.g. `lsof -t /dev/ttysXXX | xargs kill`). The TTY may have been recycled to a user's shell. Kill by PID after `_is_our_worker` verification; fall back to a fingerprinted `pkill` only if the PID path fails.
- **Never kill "all claude processes" or "all pythons".** Other projects run their own. Fingerprints are narrow on purpose.
- **Never skip the PID+TTY+fingerprint gauntlet** just because "obviously that's our pane". Pane recycling is silent.
- **Never restart a daemon by closing and reopening panes.** Kill the daemon by PID, then `write text` the relaunch command into the existing pane (whose TTY you read from `/tmp/cv-panes.txt`). Same TTY, same pane, new daemon process.
- **Never trust `/tmp/cv-worker-panes.txt` entries without the new `TTY|PID|FINGERPRINT` format.** Old entries have only a TTY, which is not enough to verify ownership — the preflight code logs them and skips.
- **Never delete `/tmp/cv-panes.txt` without tearing down first.** The file is how cleanup finds the panes; losing it strands the panes with no safe way to close them.

---

## Debugging checklist

If a cleanup misbehaves:

1. `cat /tmp/cv-panes.txt` and `cat /tmp/cv-worker-panes.txt` — are the TTY, PID, and fingerprint what you expect?
2. `ps -o tty=,command= -p <PID>` — is the PID alive, still on the recorded TTY, and still matching the fingerprint?
3. `ls /dev/ttys*` — is the TTY device even present?
4. Run `scripts/cleanup.sh` with `bash -x scripts/cleanup.sh` to watch the verification decisions.
5. If you suspect a pane close has gone rogue: the guard line in `cleanup.sh` prints `REFUSED:` and exits non-zero before touching anything. That message is the canary.

If a launch misbehaves:

1. `ps -o tty= -p $$` and `ps -o tty= -p $PPID` — are you passing the right CC TTY?
2. The AppleScript returns `"ERROR: TTY <ttys> not found"` if the CC session doesn't exist by that TTY. Check iTerm2 → Window menu; the TTY you passed must match one shown there.
3. `"ERROR: daemon pane diff failed"` means the ID diff didn't find a new session after the split. Usually iTerm2 is unresponsive — try again with more `delay`.
4. `"ERROR: daemon pane TTY collides with CC TTY"` is the split-but-no-new-TTY case. Should never happen; if it does, do not proceed.
