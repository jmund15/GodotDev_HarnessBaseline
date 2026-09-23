---
disable-model-invocation: true
allowed-tools: Bash(git:*), Read
description: Re-inject SessionStart-equivalent context (branch, working tree, recent commits, worklog, optional build) without /clear-and-resume.
---

## Purpose

After long sessions or major state changes (commits in another window, branch switch, submodule rebase, mid-session `/commit_push`), the SessionStart `<session-context>` block goes stale. The passive `prompt_git_state_delta.py` hook catches *deltas*, but sometimes you want to *actively* re-pull a complete fresh snapshot.

This command compresses the 4-call manual pattern (git status + git log + read worklog-titles + optional build) into one keystroke and synthesizes the same `<session-refresh>` block shape as SessionStart.

## Forms

Argument: `$ARGUMENTS`

| Form | Operation |
|------|-----------|
| (no args) | **Fast snapshot.** Git + submodules + worklog only. ~1 s. |
| `full` | **Verified snapshot.** Adds a build health check. |

## Procedure

### 1. Capture git context (always)

Run these in parallel:

```bash
git rev-parse --abbrev-ref HEAD                                    # branch
git rev-parse --short HEAD                                         # HEAD sha
git status --porcelain                                             # working tree
git log -3 --format='%h %s'                                        # last 3 commits
git rev-list --left-right --count HEAD...@{upstream} 2>/dev/null   # ahead/behind (may fail — that's fine)
```

### 2. Capture submodule context (always, per submodule `git submodule status` lists)

```bash
git -C <submodule> rev-parse --abbrev-ref HEAD
git -C <submodule> rev-parse --short HEAD
git -C <submodule> log -3 --format='%h %s'
```

Report an uninitialized submodule as `<submodule>: not initialized` and skip its lines. No submodules → skip this step.

### 3. Read worklog mirror (always)

```
Read .claude/worklog-titles.md
```

Extract the `## Active` section. Group by domain (the `domain — title` prefix is already structured).

### 4. Optional: build verification (only on `full`)

When `full`:

Run the build command `reference/project_stack.md` names on its `Build:` line. Report `Build: OK (N warnings)` or `Build: FAILED (N errors, N warnings)` from its output. No `Build:` line → report `Build: not configured`.

### 5. Synthesize the `<session-refresh>` block

Format must mirror the SessionStart `<session-context>` shape so it's a drop-in mental replacement:

```xml
<session-refresh>
Worktree: YES (root: <abs-path>)   [or:  Worktree: no (main repo)]

Git: <branch> | <N uncommitted | clean>
HEAD: <short-sha>
Upstream: ahead=<N> behind=<N>     [omit line if no upstream]

Recent {{PROJECT_NAME}} commits:
  <sha> <subject>
  <sha> <subject>
  <sha> <subject>

Recent <submodule> commits:         [one block per initialized submodule]
  <sha> <subject>
  <sha> <subject>
  <sha> <subject>

Build: OK (N warnings)             [only when /session_refresh full]

Worklog Active (from .claude/worklog-titles.md):
  <domain> — <title>
  ...
</session-refresh>
```

### 6. Output

Print the `<session-refresh>` block as a chat-visible message. No tool calls beyond the ones above. Don't ask follow-up questions — the block IS the answer.

## When to use

- **Mid-long-session check-in** — "where am I, really?"
- **After committing in another window** — to verify HEAD/working-tree alignment
- **Before invoking the regression gate or `/commit_push`** — confirm working tree is in expected shape
- **Picking up after a coffee break** — refresh worklog state without nuking conversation history with `/clear`

## When NOT to use

- **Right after SessionStart** — context is already fresh; this would just duplicate it
- **As a substitute for the `<git-state-delta>` hook** — that hook fires automatically on changes; this command is for proactive full re-snapshots
- **In place of the regression gate** — `full` runs the build, not the test suite. If you need test-pass verification, use the regression gate
