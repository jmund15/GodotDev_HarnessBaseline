---
disable-model-invocation: true
---

# `/worklog` — cloud queue and replay

Read before any `/worklog` form when `CLAUDE_CODE_REMOTE=true`, or when .claude/worklog-pending.md holds un-struck `- ` lines on a local session. The vault is not mounted on cloud, so no vault doc can be written there.

## On cloud

- **Writes** (`add`, `complete`, `promote`, `unblock`, `user-add` and triage dispositions) touch neither the vault nor the mirror. Append the operation to the tracked .claude/worklog-pending.md, under one header per session, and stop:
  ```
  ## <ISO timestamp> cloud session <id>
  - ADD active: <title> (<domain> · <class> · scope <n>)
  - COMPLETE: <title>
  - PROMOTE: <title>
  ```
  Commit the file; git carries it to the next local session.
- **Reads:** `show` uses the mirror. A form that needs the vault (`show all`, `sweep`, `triage`, `drive`, `user-show`, `history`) prints `Vault not mounted on cloud — run it in a local session.` and stops.

## Replay on a local session

1. Report `Replaying N pending cloud-session entries to Obsidian`, N being the un-struck `- ` lines below the file's header.
2. Apply each entry as its form (ADD as `add`, COMPLETE as `complete`, PROMOTE as `promote`).
3. An entry that no longer fits the vault (a COMPLETE already archived, an ADD of an existing title) is rewritten `- ~~<entry>~~ (skipped: <reason>)` and skipped; the replay never fails on it.
4. Truncate the body and keep the header. A header-only body is the no-change result: the next run replays nothing.
