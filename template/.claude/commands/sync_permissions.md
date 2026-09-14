---
description: Sync local and user permissions into the tracked project settings.json.
disable-model-invocation: true
---

Sync permissions from local/user settings into the tracked project settings.json.

## Purpose
When "Always allow" is clicked during a session, it writes to `settings.local.json` (gitignored) or `~/.claude/settings.json` (user-level). These don't carry over to worktrees or other workstations. This command promotes those permissions into the tracked `settings.json`.

## Steps

### 1. Read all three settings files
- **Project tracked:** `.claude/settings.json` (the target — shared via git)
- **Project local:** `.claude/settings.local.json` (gitignored, per-checkout)
- **User global:** `~/.claude/settings.json` (per-machine)

### 2. Extract and diff permissions
From each file, extract the `permissions.allow` array. Identify entries in local or user-global that are NOT already in the tracked project settings.

**Critical rule:** If an entry exists in `settings.local.json`, it was explicitly granted because the tracked permissions **did not match**. The local file is proof of a gap. Do NOT dismiss local entries as "already covered by a similar entry" — Claude Code uses exact string matching, so `Bash(python:*)` and `Bash(python *)` are distinct permissions. When a local entry looks like a syntax variant of a tracked entry, propose adding the **exact local form** alongside the existing tracked form.

### 3. Present the diff
Show the user a clear table:

| Permission | Source | Status |
|---|---|---|
| `Bash(cd:*)` | local | NEW — not in settings.json |
| `mcp__godot__get_uid` | local | Already tracked |

Only show NEW entries (ones missing from `settings.json`).

### 4. Ask which to promote
Ask the user which new permissions to add to `settings.json`. Options:
- **All** — add everything
- **Select** — let the user pick
- **None** — just informational, no changes

### 5. Apply
- Add approved permissions to `.claude/settings.json` `permissions.allow` array
- Remove promoted entries from `.claude/settings.local.json` to keep it clean (leave any that weren't promoted)
- Do NOT modify `~/.claude/settings.json` (user-global stays as-is)

### 6. Generalize hyper-specific entries
Before scanning transcripts, examine all hyper-specific entries in local and user-global that were NOT directly promotable. These are entries with hardcoded paths, specific arguments, or one-off patterns.

**For each hyper-specific entry:**
1. Extract the command prefix (e.g., `git -C` from `Bash(git -C "C:\\Users\\...specific path...")`)
2. Check if a wildcard version already exists in tracked — check BOTH syntax forms: `Bash(cmd *)` AND `Bash(cmd:*)`
3. If BOTH wildcard forms are tracked: skip
4. If only one form is tracked: propose adding the missing form (Claude Code uses exact string matching)
5. If neither wildcard form is tracked: flag it as a generalization candidate — propose BOTH forms

**Present a generalization table:**

| Specific Entry (Source) | Proposed Generalization | Already Covered? |
|---|---|---|
| `Bash(git -C "C:\\...\\{{PROJECT_NAME}}" status)` | `Bash(git -C:*)` | ✅ Yes — skip |
| `Bash(Select-String -Pattern "error")` | `Bash(Select-String:*)` | ❌ No — NEW |
| `Bash(timeout /t 4 /nobreak)` | `Bash(timeout:*)` | ❌ No — NEW |
| `Bash(Get-Content "..." -Tail 200)` | `Bash(Get-Content:*)` | ❌ No — NEW |

**Rules:**
- Only generalize if 2+ specific instances share the same command prefix (shows repeated usage)
- Single-use entries with no generalizable pattern should be skipped silently
- The generalized form should always use `:*` wildcard suffix
- Ask user which generalizations to promote (same All/Select/None options as Step 4)

### 7. Scan current session for ephemeral one-time allows
After the settings file diff and generalization pass, scan **this session only** for tools that were used but aren't in any settings file (tracked, local, or user-global). These represent one-time session approvals that the user might want to promote.

**Scope:** This session has TWO sources of history:
1. **Live conversation context** — what's currently in your context window (post-last-compaction)
2. **This session's transcript backups** — files in `logs/transcript_backups/` matching `transcript_{THIS_SESSION_ID}_*.jsonl`. Each compaction creates a backup, so a long session may have many.

Do NOT scan transcript files from other sessions — only this session's ID.

**How to extract:**
1. Review your own tool calls from the live conversation context
2. Find this session's transcript backups by matching the session ID in filenames under `logs/transcript_backups/`
3. Parse those `.jsonl` files for `"type":"tool_use"` entries to extract tool names and Bash command prefixes
4. Combine results from both sources, deduplicate
5. Subtract all permissions already in `settings.json` (including wildcard coverage — e.g., `Bash(git -C:*)` covers all `Bash(git -C ...)` variants)
6. Present any NEW tool permissions as a separate table

**Important filtering:**
- Skip hyper-specific one-off Bash commands (specific file paths, commit messages, python -c scripts) — these are session artifacts
- Only surface broad patterns worth promoting (e.g., `Bash(git branch:*)`, `Bash(npm:*)`)
- Group by category like the main diff

**Present separately from the settings diff:**

| Permission | Source | Occurrences |
|---|---|---|
| `Bash(npm install:*)` | this session | 7 |
| `mcp__new_server__tool` | this session | 2 |

Ask if any should be promoted, same options as Step 4.

### 8. Identify the denial layer before changing permissions
Record the tool input, exact denial reason, permission mode, and `claude --version`. A prompt alone proves neither a missing allow rule nor a platform regression. Check the current official [permission modes](https://code.claude.com/docs/en/permission-modes) and [denial review](https://code.claude.com/docs/en/auto-mode-config#review-denials) documentation before proposing a change.

1. **Project hook denial** — identify the named hook and its matching predicate. Change it only after a narrow reproduction distinguishes an intended block from a false positive. Do not reactivate dormant shell-shape blocks merely because a prompt returned.
2. **Auto-mode classifier denial** — inspect `/permissions` → **Recently denied** and `claude auto-mode config`. For an intended one-off action, have the user select it and press `r` for manual review. Do not retry under another tool. A generic “Blocked by classifier” leaves the matched rule UNKNOWN; preserve that limit rather than guess.
3. **Classifier unavailable or no verdict** — distinguish the failure from a policy block using the returned reason. These may lack a Recently denied entry. Follow the recovery documented for that error. If neither the error nor the docs gives a recovery, stop and report the exact error. Adding an allow rule does not repair a failed classifier request.
4. **Protected-path write** — `.claude/` writes, including project scratch, receive extra review. A settings-file `Edit(.claude/**)` allow rule does not bypass that review. Do not broaden interpreter permissions or move a denied write to evade it.
5. **Verified allow-rule gap** — route the scoped rule through “Extract and diff permissions,” “Present the diff,” “Ask which to promote,” and “Apply”. For a `cd` compound, extend `compound_cd_approver.py` only when every newly accepted segment is read-only and tests prove both accepted and rejected cases.

Close with the observed cause, change, and verification, or the exact unresolved permission decision. No new guard, blanket allow, or automatic retry is the default outcome.

### 9. Summary
Report what was added (promoted, generalized, and transcript-discovered) and remind the user to commit `settings.json` if desired.
