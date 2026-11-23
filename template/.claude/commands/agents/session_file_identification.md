---
disable-model-invocation: true
---

# Session File Identification Procedure
<!-- Single source of truth for identifying which files were modified during this session. -->
<!-- Referenced by: /commit_push step 1, /close -->

Use this procedure to determine which files belong to this session and which were dirty before it.

## Step 1: Session files

```
python3 .claude/tools/session_digest.py --session <this session's id>
```

The transcript is append-only across compactions, so the digest covers the whole session. Its *What it did* section lists the session's commits and the files it wrote. Those files are the session's set. A run that fails, or finds no transcript for the id, leaves the scope unknown: report it, and never treat it as an empty scope.

## Step 2: Dirty state

List the checkout's uncommitted files:
```bash
git status --porcelain=v1 --untracked-files=all
```

The list holds every dirty file on the checkout, other sessions' included, so it classifies files and never adds one to the session's set.

## Step 3: Reconcile and report

- **Session files** = Step 1's files that Step 2 lists as dirty.
- **Pre-existing dirty** = Step 2's entries outside the session files.

Report:
```
Session File Identification Results:
  Source: session digest <session id>
  Session files: X total
  Pre-existing dirty (excluded): [list or "none"]
```

Callers may filter by extension after this procedure completes.
