---
description: End a session - commit and push, update the state file, run due upkeep, print the resume line
---

# /close — end a session

Run it when the session's work is done or handed off. Steps run in order. A failed step is reported with its error, and the later steps still run. The **changed files** below are the session's files as [Session File Identification](agents/session_file_identification.md) finds them, committed or not.

## Arguments

None: close this session. Any text is the owner's note on how to close. Apply it where a step leaves a choice. A note never skips step 4.

Resolve every open choice before step 4. Apply your recommendation to a reversible one. Ask an owner-only fork as one packet (`CLAUDE.core.md` §Preferences), unless `python3 .claude/hooks/overnight_ask_guard.py --status` reports `armed`: then `/overnight` Step 2 decides or parks it.

## Steps

1. **State file.** `.claude/scratch/active_state_<session_id>` (the `Session id:` line SessionStart printed; its `State file:` line names the file) names this session's state file. When it exists, update the file's `## Standing facts`: each standing permission the owner gave (push authority included), with the owner's words and where they said them, and for each outward action the tool or file that owns its call. Remove a permission the owner withdrew. Slice status is `/drive`'s, not this step's. No pointer means no state file: skip.
2. **Subsystems.** Run `/sync_subsystems` when a changed file adds or empties a folder, which changes folder shape. Otherwise skip.
3. **Worklog.** Run `/worklog sweep`, each half only when its input exists: the completion half when the session worked a worklog item, the add half when it deferred or parked work it named. Vault edits stay out of the git commit.
4. **Commit and push.** Run [`/commit_push`](commit_push.md). Invoking `/close` is push authority for the active branch, `main` included.
5. **Search index.** Run `/reindex_search` after the last commit when a changed file is one the index covers: docs, memory, data or resource files. An unavailable plugin skips the step. An error such as a peer's index lock is reported. Neither blocks the close.
6. **Resume line.** Print it last: `Resume: /drive <slug>` when step 1 found a state file, else `Resume: /session_digest <session id>`.

## Report

The commits and push target `/commit_push` printed, the standing facts changed, each upkeep step run or skipped with its reason, every failure, then the resume line. Never end on a question.
