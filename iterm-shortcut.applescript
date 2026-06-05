-- babysitter: split current pane vertically and launch the babysitter CLI.
-- Bind to ⌘⇧M in iTerm2 Settings > Keys > Key Bindings > "Invoke AppleScript..."
--
-- Safety model mirrors docs/pane-management.md:
--   1. Capture the keypress pane's TTY ONCE at entry. Never re-resolve via
--      "current session" later (focus can change between steps).
--   2. Re-find the source session by exact TTY equality.
--   3. Snapshot the tab's session IDs BEFORE the split.
--   4. Split, then diff IDs to identify the new pane (iTerm session order is
--      not stable after splits).
--   5. Assert the new pane's TTY is NOT the source TTY. Abort otherwise.
--   6. Record source TTY to /tmp/bs-panes.txt as claude_code=<tty> (the
--      untouchable pane). The launcher later appends the babysitter=... line.
--   7. Write the launch-babysitter command into the diffed pane.

on run
	tell application "iTerm"
		-- Step 1: capture source TTY once, up front.
		set sourceTTY to ""
		try
			tell current window
				tell current session
					set sourceTTY to tty
				end tell
			end tell
		on error errMsg
			display dialog "babysitter: could not read current session TTY: " & errMsg buttons {"OK"} default button 1
			return
		end try

		if sourceTTY is "" then
			display dialog "babysitter: source TTY was empty; refusing to split." buttons {"OK"} default button 1
			return
		end if

		-- Step 2: re-find the source session by exact TTY equality.
		set sourceSession to missing value
		set sourceTab to missing value
		set sourceWindow to missing value
		repeat with w in windows
			repeat with t in tabs of w
				repeat with s in sessions of t
					if tty of s is equal to sourceTTY then
						set sourceSession to s
						set sourceTab to t
						set sourceWindow to w
						exit repeat
					end if
				end repeat
				if sourceSession is not missing value then exit repeat
			end repeat
			if sourceSession is not missing value then exit repeat
		end repeat

		if sourceSession is missing value then
			display dialog "babysitter: could not re-find session with TTY " & sourceTTY buttons {"OK"} default button 1
			return
		end if

		-- Step 3: snapshot session IDs in the source tab.
		set beforeIDs to {}
		repeat with s in sessions of sourceTab
			set end of beforeIDs to (unique id of s)
		end repeat

		-- Step 4: split the specifically-found source session.
		tell sourceSession
			split vertically with default profile
		end tell

		-- Step 5: walk sessions in the tab; the one whose ID is new is ours.
		set newSession to missing value
		repeat with s in sessions of sourceTab
			set sid to (unique id of s)
			if beforeIDs does not contain sid then
				set newSession to s
				exit repeat
			end if
		end repeat

		if newSession is missing value then
			display dialog "babysitter: split succeeded but no new session ID appeared. Aborting." buttons {"OK"} default button 1
			return
		end if

		-- Step 6: assert the new pane's TTY differs from source. (Paranoia;
		-- TTY doesn't actually exist yet on a fresh pane — it gets assigned
		-- when the shell starts. But if it somehow is already set and equals
		-- sourceTTY, that's a disaster we must refuse.)
		set newTTY to ""
		try
			set newTTY to tty of newSession
		end try
		if newTTY is not "" and newTTY is equal to sourceTTY then
			display dialog "babysitter: new pane TTY collides with source TTY. Aborting before writing any command." buttons {"OK"} default button 1
			return
		end if

		-- Step 7: record source (watched) TTY as the untouchable pane.
		-- launch-babysitter.sh will append its own babysitter=... line after spawning.
		do shell script "printf 'claude_code=%s\\n' " & quoted form of sourceTTY & " > /tmp/bs-panes.txt"

		-- Write the launch command into the diffed pane.
		-- EDIT THIS LINE: set it to the absolute path of launch-babysitter.sh in your clone.
	set launcherPath to (POSIX path of (path to home folder)) & "babysitter/launch-babysitter.sh"
		tell newSession
			write text (quoted form of launcherPath & " " & quoted form of sourceTTY)
		end tell
	end tell
end run
