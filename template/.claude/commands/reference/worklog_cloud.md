---
disable-model-invocation: true
---

# `/worklog` — cloud fallback and replay

Read when `CLAUDE_CODE_REMOTE=true`, or when `.claude/worklog-pending.md` holds un-struck `- ` lines on a local session.

The vault is not mounted on cloud, so `Worklog.md` cannot be written. Detect with the Bash form `[ "${CLAUDE_CODE_REMOTE:-}" = "true" ]` (this is a `.md` command — there is no `is_cloud()` import).

- **Mutating ops** (`add`, `complete`, `promote`, `unblock`, triage dispositions): do NOT touch the vault or the mirror. Append the op to the tracked queue `.claude/worklog-pending.md`, one header per session (new header only when the session changes), then stop:
  ```
  ## <ISO timestamp> cloud session <id>
  - ADD active: <title> (<domain> · <class> · scope <n>)
  - COMPLETE: <title>
  - PROMOTE: <title>
  ```
- **Read ops:** `show` uses the local mirror. Ops needing the full vault doc (`show all` Future Scope, `sweep`, `drive`) print `Vault not mounted on cloud — defer to a local session.` and stop.
- **Commit** the pending file from cloud (it is tracked); the cloud→local handoff crosses machines via git.

### Replay (local session)

Any `/worklog` invocation first checks `.claude/worklog-pending.md`. If its body (below the DO-NOT-HAND-EDIT header) has un-struck `- ` lines:

1. Report `Replaying N pending cloud-session entries to Obsidian` (N = un-struck `- ` lines).
2. Apply each entry as the matching native op (ADD→add, COMPLETE→complete, PROMOTE→promote).
3. **Conflict policy = skip-and-audit-trail:** an entry no longer matching Obsidian state (`COMPLETE: X` already archived; `ADD` of an existing title) is rewritten struck-through (`- ~~<entry>~~ (skipped: <reason>)`) and skipped — never hard-fail the replay.
4. After applying, truncate the body, keep the header.
5. **Idempotency:** a later run seeing a header-only body no-ops without re-prompting.

The SessionStart hook surfaces `Cloud worklog: N pending` on local sessions when un-struck entries exist.
