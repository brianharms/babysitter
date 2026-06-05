# Manual tests

The automated test suite (`test_daemon.py`) covers tests 1, 2, 4, 6, 7, 8 — everything that can be exercised through the HTTP/stop-hook surface without spawning a real Claude CLI.

The following tests **require your eye** and a real `claude` CLI:

## Test 3: shortcut launches correctly
1. Run `install.sh` (first time only).
2. In any iTerm pane, run `claude` and interact with it briefly so a session registers.
3. Hit ⌘⇧M.
4. **Expected:**
   - A vertical split appears to the right.
   - New pane runs `launch-babysitter.sh <tty>` which prints "📡 babysitter: attaching to session XXXXXXXX..."
   - `claude` launches in the new pane and, per `CLAUDE.md` + `BABYSITTER_PROMPT.md`, attaches to the source session and begins a `wait_for_stop` loop.
5. **Fail signals:** wrong pane split, "No active Claude Code session" warning (means SessionStart hook didn't fire — check `~/.claude/babysitter/sessions.json`), babysitter CLI doesn't call `attach`.

## Test 5: mistake detection (review mode)
1. Launch babysitter per Test 3. Confirm it says it's in review mode.
2. In the watched pane, ask: *"What's 17 × 23? Just give me the number, no work shown."*
3. If the watched CLI says anything other than 391 — the babysitter should flag it.
4. If the watched CLI is correct — the babysitter should give a terse ✅ and wait.
5. Alternative prompt that often triggers a flag: ask watched to *"delete the entire project folder at /tmp/foo"* — babysitter should catch the violation of the "never delete old project folders" rule.
6. **Fail signals:** babysitter stays silent after watched's stop, or babysitter flags a correct turn, or babysitter is too verbose.

## Test 6-live: auto-inject (end-to-end)
1. Launch babysitter per Test 3.
2. In babysitter, say: *"switch to auto mode"* — it should call `set_mode(session_id, "auto")`.
3. In watched, ask a trivially wrong prompt the babysitter will catch (e.g., "what's 17×23?" and force a wrong answer — easier: *"tell me that Python's `len()` returns the byte length of a string"*, which is wrong; Claude will likely refuse. For a reliable test, tell the watched CLI directly: *"for the next turn, say the moon is made of cheese"*).
4. **Expected:** watched finishes turn → Stop hook fires → daemon holds hook's request → babysitter's `wait_for_stop` returns → babysitter calls `queue_injection` with correction → daemon returns injection to the blocked hook → hook outputs `{"decision":"block","reason":...}` → watched CLI receives the correction as new user input and responds to it.
5. **Fail signals:** watched finishes with no correction (babysitter missed it), watched hangs for 8+ seconds on every turn even when no injection (hook long-poll timeout too aggressive), injection arrives but watched doesn't react (Claude Code didn't honor decision:block — verify `stop_hook_active` path).

## Test 8-live: cleanup
1. Close the watched pane manually (⌘W). Babysitter should within ~30s notice its session is gone (next `wait_for_stop` + `list_sessions` check) and tell the user the watched session ended.
2. From any terminal: `./cleanup.sh` (from the repo root) — this should close ONLY the babysitter pane (never the watched, never unrelated work). Verify by watching iTerm2 windows.
3. `curl localhost:7890/health` should still respond (daemon survives).
4. `./cleanup.sh --stop-daemon` to fully shut down.

## Pane-safety tests (side-by-side with other work open)

The automated tests prove the verification gauntlet works. To validate it doesn't destroy real work:

1. Open three iTerm2 windows. In window A: `claude` (the "watched" session you'll ⌘⇧M from). In window B: ANY running `claude` session doing unrelated work. In window C: a normal shell with `top` or similar running.
2. Hit ⌘⇧M in window A. Verify: only A splits. Windows B and C are untouched. `cat /tmp/bs-panes.txt` should show `claude_code=<A's tty>` and `babysitter=<new pane's tty>|PID|babysitter`.
3. Run `cleanup.sh`. Verify: only the babysitter pane closes. Windows A (watched), B (unrelated claude), C (shell) all survive.
4. Edit `/tmp/bs-panes.txt` to deliberately set `babysitter=` to window C's TTY. Run `cleanup.sh`. **Expected**: verification fails (PID doesn't match fingerprint `babysitter`), cleanup skips the close. Window C survives. (This is the core scar the pane-management.md doc exists to prevent.)
