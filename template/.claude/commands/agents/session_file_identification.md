---
disable-model-invocation: true
---

# Session File Identification Procedure
<!-- Single source of truth for identifying which files were modified during this session. -->
<!-- Referenced by: /commit_push (step 2), /session_audit (Phase 1a) -->

Use this procedure to determine which files belong to this session vs pre-existing dirty state.

## Step 1: Session Manifest (Primary Source)

Check for a session manifest file — the fastest and most reliable source:

```
logs/session_files/<session_id>.json
```

If the manifest exists, it contains the authoritative `files` list (cumulative across all compactions). Use it directly and proceed to Step 3 for cross-verification.

**If the manifest does NOT exist**, fall through to Step 2.

## Step 2: Compaction Recovery (MANDATORY)

**Always check for compaction data** — do not skip this step regardless of whether you think compaction has occurred. Check `logs/pre_compact.json` for entries matching the current session ID.

1. Read `logs/pre_compact.json` — find ALL entries matching the current session ID
2. For each entry with a `summary_path`, read the `.summary.json` file
3. Check the `files_modified` field (sorted list of file paths edited before compaction)
4. Union these with files from your conversation memory

**WARNING — Narrative summaries are NOT file lists:** The "Summary:" block injected into conversation after compaction is a narrative for context resumption, NOT an authoritative file list. Only `.summary.json` `files_modified` fields are machine-generated and authoritative — they capture every Edit/Write tool call from the pre-compaction window.

**If `files_modified` is absent** (older summary format, schema <1.1): fall back to `todo_final_state` and `last_user_request` for clues, plus git history.

**Multiple compactions:** Summaries are cumulative — each later summary is a superset of all earlier ones. The LATEST `.summary.json` for the session ID contains the complete file list. You can read just the latest one instead of unioning all.

**No compactions found:** If `logs/pre_compact.json` is missing or contains no entries for this session ID:
```
⚠️ WARNING: No compaction data found for this session.
This is unusual for /session_end and /session_audit — these commands almost never run
without at least one compaction. File identification will rely on conversation memory
and git status only, which may be incomplete.
```

## Step 3: Conversation Context (Current Window)

List all files you know you created or modified during this session from your conversation memory.

**CRITICAL:** After compaction, conversation memory only covers the POST-compaction window. Steps 1-2 cover the pre-compaction window.

## Step 4: Git Verification (Secondary)

Cross-reference with git to catch anything missed:
```bash
git diff --name-only HEAD
git diff --cached --name-only HEAD
git ls-files --others --exclude-standard
```

**Mega-session fallback:** If the session has intermediate commits (check `git log main..HEAD --oneline`), the above `git diff HEAD` only shows uncommitted changes. Use the full session diff:
```bash
git diff --name-only main...HEAD
```

**Multi-session branches:** `git diff` captures ALL dirty files on the branch, not just this session's. Use git only to cross-reference Steps 1-3, NOT as the primary source.

## Step 5: Reconcile & Verify

- **Session files** = Union of Steps 1 (or 2) + 3, verified against Step 4
- **Pre-existing dirty** = Files in `git status` that appear in NONE of Steps 1, 2, or 3
- If uncertain about a file, the `.summary.json` `files_modified` field is **machine-generated and authoritative** for the pre-compaction window — trust it over your own memory

**Output verification** — After reconciliation, report:
```
Session File Identification Results:
  Source: [manifest | compaction summaries (N found) | conversation only]
  Session files: X total (Y .cs, Z .tres, W other)
  Pre-existing dirty (excluded): [list or "none"]
```

**Callers may apply extension filters** (e.g., `*.cs *.tres` for audit) after this procedure completes.
