---
disable-model-invocation: true
allowed-tools: Bash(git:*), Bash(dotnet build:*), Read
description: Re-inject SessionStart-equivalent context (branch, working tree, recent commits, worklog, optional build) without /clear-and-resume.
---

## Purpose

After long sessions or major state changes (commits in another window, branch switch, submodule rebase, mid-session `/commit_push`), the SessionStart `<session-context>` block goes stale. The passive `prompt_git_state_delta.py` hook catches *deltas*, but sometimes you want to *actively* re-pull a complete fresh snapshot.

This command compresses the 4-call manual pattern (git status + git log + read worklog-titles + optional dotnet build) into one keystroke and synthesizes the same `<session-refresh>` block shape as SessionStart.

## Forms

Argument: `$ARGUMENTS`

| Form | Operation |
|------|-----------|
| (no args) | **Fast snapshot.** Git + Jmodot + worklog only. ~1 s. |
| `full` | **Verified snapshot.** Adds `dotnet build` health check. ~10–15 s. |

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

### 2. Capture Jmodot context (always, if submodule initialized)

```bash
git -C Jmodot rev-parse --abbrev-ref HEAD
git -C Jmodot rev-parse --short HEAD
git -C Jmodot log -3 --format='%h %s'
```

If `Jmodot/` is empty or missing, report `Jmodot: not initialized` and skip its lines.

### 3. Read worklog mirror (always)

```
Read .claude/worklog-titles.md
```

Extract the `## Active` section. Group by domain (the `domain — title` prefix is already structured).

### 4. Optional: build verification (only on `full`)

Skip this step entirely when no argument is passed. When `full`:

```bash
dotnet build --nologo -v q
```

Parse the trailing `N Error(s)` and `N Warning(s)` lines from output. Report as `Build: OK (N warnings)` or `Build: FAILED (N errors, N warnings)`.

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

Recent Jmodot commits:
  <sha> <subject>
  <sha> <subject>
  <sha> <subject>

Build: OK (N warnings)             [only when /session_refresh full]

Worklog Active (from .claude/worklog-titles.md):
  ai — Wire BehaviorSuppressedState into 5 remaining NPC scenes
  ai — Extract enemy_template.tscn from the 4 wired enemies
  ...
</session-refresh>
```

### 6. Output

Print the `<session-refresh>` block as a chat-visible message. No tool calls beyond the ones above. Don't ask follow-up questions — the block IS the answer.

## When to use

- **Mid-long-session check-in** — "where am I, really?"
- **After committing in another window** — to verify HEAD/working-tree alignment
- **Before invoking `/regression_gate` or `/commit_push`** — confirm working tree is in expected shape
- **Picking up after a coffee break** — refresh worklog state without nuking conversation history with `/clear`

## When NOT to use

- **Right after SessionStart** — context is already fresh; this would just duplicate it
- **As a substitute for the `<git-state-delta>` hook** — that hook fires automatically on changes; this command is for proactive full re-snapshots
- **In place of `/regression_gate`** — `full` runs `dotnet build`, not the test suite. If you need test-pass verification, use the regression gate
