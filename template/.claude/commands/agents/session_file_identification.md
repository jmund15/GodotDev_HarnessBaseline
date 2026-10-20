---
disable-model-invocation: true
---

# Session File Identification Procedure
<!-- Single source of truth for identifying which files were modified during this session. -->
<!-- Referenced by: /commit_push (step 2), /session_audit (Phase 1a) -->

Use this procedure to determine which files belong to this session vs pre-existing dirty state.

## Step 1: Session Scope (Primary Source)

Refresh the digest first, because the scope reads the saved digest file and an earlier run in this session leaves it stale:

```
python3 .claude/tools/session_digest.py --prompt-tail <command>
```

Then write the session's scope, the commits and changed files every close surface reads:

```
python3 .claude/tools/close_predicates.py --session <id> --scope --json .claude/scratch/session_end/<sid>/scope.json
```

Its `commits` are the full hashes of the session's commits and its `files` the repo-relative files those commits, the digest's Edit/Write counter and the live transcript's named dirty paths changed; the tool's docstring owns the identity rules. Exit 1 means `files` is null and `error` names why. When the error is `transcript not found`, use Step 2. Any other error (unmatched commit invocations, a failing git call) is an unknown scope: report it, and never treat it as an empty one.

## Step 2: Compaction Recovery (Fallback)

Use this step only when Step 1 reports `transcript not found`.

`logs/session_files/<session_id>.json` is written by the PreCompact hook only, so it stops at the last compaction and omits every later edit.

1. Check `logs/pre_compact.json` for entries matching the current session ID
2. No match (or the file is missing — this harness's compaction hook writes `logs/transcript_backups/` instead): check `logs/transcript_backups/transcript_<session-id>_*.summary.json` — the transcript-backup summary carries the same machine-generated `files_modified` field
3. Read the LATEST `.summary.json` and check its `files_modified` field (sorted list of file paths edited before compaction). Summaries are cumulative — each later summary is a superset of all earlier ones.
4. **If `files_modified` is absent** (older summary format, schema <1.1): fall back to `todo_final_state` and `last_user_request` for clues, plus git history.

**Narrative summaries are NOT file lists:** The "Summary:" block injected into conversation after compaction is a narrative for context resumption, NOT an authoritative file list. Only `.summary.json` `files_modified` fields are machine-generated and authoritative — they capture every Edit/Write tool call from the pre-compaction window.

## Step 3: Dirty State

List the checkout's uncommitted files:
```bash
git status --porcelain=v1 --untracked-files=all
```

The list holds every dirty file on the checkout, other sessions' included, so it classifies files and never adds one to the session's set.

## Step 4: Reconcile & Verify

- **Session files** = Step 1's `files`, or the Step 2 summary when the transcript was unavailable
- **Pre-existing dirty** = Step 3 entries outside the session files. After the Step 2 fallback, the summary omits every edit since the last compaction, so an entry outside it is **unattributed**, not pre-existing; report it separately and never exclude it from closeout scope.

**Output verification** — After reconciliation, report:
```
Session File Identification Results:
  Source: [scope (<identity>) | compaction summary]
  Session files: X total (Y .cs, Z .tres, W other)
  Pre-existing dirty (excluded): [list or "none"]
  Unattributed (Step 2 fallback only): [list or "none"]
```

**Callers may apply extension filters** (e.g., `*.cs *.tres` for audit) after this procedure completes.
