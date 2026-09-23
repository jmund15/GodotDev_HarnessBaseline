---
disable-model-invocation: true
allowed-tools: Bash(gh pr view:*), Bash(gh pr edit:*), Bash(gh pr merge:*), Bash(gh pr close:*), Bash(gh pr list:*), Bash(gh label:*), Bash(git stash:*), Bash(git checkout:*), Bash(git pull:*), Bash(git rebase:*), Bash(git push:*), Bash(git add:*), Bash(git branch:*), Bash(git -C:*), Bash(git submodule:*), Bash(dotnet build:*), Bash(gdunit4:*), Glob, Grep, Read, Edit, Task, mcp__obsidian__obsidian_list_notes, mcp__obsidian__obsidian_read_note
description: "Build, test, and merge a single PR"
---

Build, test, and merge a single PR. Assumes code review has already been done via `/review_pr`.

## Arguments
- `$ARGUMENTS` — PR number (required)

---

## Step 1: Pre-Merge Checks

```bash
gh pr view <N> --json title,headRefName,state,mergeable,additions,deletions,changedFiles
```

Verify:
- PR is open (not closed/merged)
- PR is mergeable (no conflicts). If not mergeable, sync with main using the appropriate strategy:

  **Choose sync strategy based on branch size:**
  - **≤20 diverged commits → `git rebase main`** (each commit replays individually, conflicts are small and isolated)
  - **>20 diverged commits → `git merge main`** (one resolution pass with full branch context, avoids fatigue across 50+ resolution passes where the same conflict can recur)

  Count diverged commits: `git rev-list --count main..HEAD`

  **Rebase path** (small branches):
  ```bash
  git checkout <branch>
  git rebase main
  # Resolve conflicts per Step 2 rules (one commit at a time)
  git push --force-with-lease
  ```

  **Merge path** (large branches, >20 commits):
  ```bash
  git checkout <branch>
  git merge main
  # Resolve all conflicts in one pass per Step 2 rules
  # Build + test to verify resolution
  git push
  ```
  > **Why the threshold?** Rebase replays each commit individually — excellent for isolation, but at 50+ commits the same file can conflict repeatedly, causing resolution fatigue and silent errors. Merge resolves each conflict exactly once with full branch context. The conflict resolution rules (interface checks, data-file verification, feature spot-checks) apply equally to both strategies.

  Ask user to confirm before syncing.
  > **Note (when called from `/review_prs`):** The orchestrator may have already synced this branch preemptively in Phase 3. This mergeability check is a safety net that catches any remaining issues (e.g., conflicts from fix commits applied in Phase 2).
- If a submodule pointer changed: warn about merge order. The paired submodule PR merges to the submodule's primary branch FIRST.
  - Find the paired submodule branch by the project's pairing convention (the submodule's path-scoped rule names it) and verify it exists: `git -C <submodule> ls-remote --heads origin`.
  - If the submodule PR conflicts with its primary branch: sync it the same way as Step 1, then push.
  - Merge the submodule PR via `gh pr merge <sub-pr> --repo <sub-remote> --merge --delete-branch`.
  - **CRITICAL — Do NOT update this branch's submodule pointer after the submodule merge.** Leave it pointing at its original submodule commit. GitHub resolves submodule pointers at merge time, and the commit stays reachable through the submodule's merge history. Updating the pointer to the submodule's latest primary branch pulls in commits from OTHER unrelated PRs, introducing API changes this branch was never designed for. The pointer reconciles naturally when this PR merges.

---

## Step 2: Conflict Resolution (if sync has conflicts)

If the sync in Step 1 produces merge conflicts (whether from rebase or merge), resolve them using this category-based guide. **Do NOT blindly accept either side** — each file type has different resolution semantics.

> ⚠️ **Rebase ours/theirs are INVERTED from merge:**
> - `--ours` = HEAD = main + already-replayed commits (the base you're rebasing ONTO)
> - `--theirs` = the branch commit being cherry-picked (the commit being replayed)
>
> This is the opposite of merge semantics. Always verify after `checkout --ours/--theirs` that the file contains what you expect before staging.

### Decision Framework

| Confidence | Action | Example |
|------------|--------|---------|
| **Safe to auto-resolve** | Resolve and continue rebase | Additive-only changes to different sections of the same file |
| **Resolvable with care** | Resolve using category rules below, verify with build + tests | Serialized data-file reference conflicts, package-manifest additions |
| **Ask user** | Present conflict context and wait for direction | Logic disagreements, behavioral changes, ambiguous design intent |

> **Default posture: conservative.** If you aren't confident the resolution preserves correctness, STOP and present the conflict to the user with both versions and your recommendation. **If ever unsure which side to take, ask the user directly — never guess.**

### Category Resolution Rules

#### Source Files (non-test)

| Scenario | Resolution | Escalate? |
|----------|-----------|-----------|
| Both branches ADD new methods/classes (no overlap) | Keep both | No |
| Both modify the SAME method body | **Ask user** — behavioral intent matters | **Yes** |
| One branch renames/moves, other modifies | Apply modifications to the renamed version | No, unless semantics changed |
| Import/`using` statement conflicts | Union all imports, remove duplicates | No |
| Registry/dictionary additions | Keep ALL entries from both branches | No |

#### Test Files

| Scenario | Resolution | Escalate? |
|----------|-----------|-----------|
| Both branches add new test methods | Keep ALL tests from both branches | No |
| Shared fixture changes | Union additions — keep all new fixture entries from both | No |
| Test modifies assertion on same method | **Ask user** — expected values may reflect different design intent | **Yes** |
| Parameterized test-case additions | Keep all cases from both branches | No |

#### Serialized Data Files

**⚠️ Highest risk category.** Git can auto-merge serialized data files (scenes, resources, generated configs) into **semantically broken** results — an entry referencing an ID declared only on the other branch — while the build stays green.

| Scenario | Resolution | Escalate? |
|----------|-----------|-----------|
| Both branches add entries to the same collection | **Keep ALL entries.** Give each added declaration a unique ID and keep every declaration the entries reference. | No, but verify carefully |
| Both modify the SAME value | **Ask user** — design intent matters | **Yes** |
| Engine-assigned ID conflicts | Fetch the real ID from the engine's tooling. Never guess. | No |

**Post-resolution check:** every reference resolves to a declaration in the same file, no ID is declared twice with different targets, and any declared entry count matches the entries. The path-scoped rule for the file format (it auto-loads on those files) owns format-specific resolution.

#### Metafiles

| File | Resolution |
|------|-----------|
| Package/project manifests | Union all dependency and source entries. Remove duplicates. |
| Generated metadata and caches | Regenerate with the tool that owns them. Never hand-edit. |
| Test/run settings | Take newer version (functional config, not accumulated data) |

#### Submodule Pointers

**Never resolve submodule pointer conflicts by picking a side.** Both branches point to commits that may not exist on the submodule's primary branch yet.

Resolution:
1. Confirm BOTH paired submodule branches are merged to the submodule's primary branch first (Step 1 submodule check)
2. After they merge, update the submodule to the latest primary branch:
   ```bash
   git -C <submodule> fetch origin
   git -C <submodule> checkout <primary>
   git -C <submodule> pull
   git add <submodule>
   ```
3. Continue the rebase or merge

#### Claude-Specific Files (`.claude/`)

These files are modified by multiple worktree sessions and frequently conflict.

**Accumulative data files** — content from ALL branches must be preserved:

| File | Resolution | Rationale |
|------|-----------|-----------|
| `self_evaluate_archive.json` | **Union all entries.** Each evaluation is a timestamped snapshot — append all, sort by date. | Independent session evaluations, no conflicts possible if appended correctly |

> **Merge technique for accumulative JSON:** Load both sides via `git show :2:<file>` and `:3:<file>` with Python (use `stdout.decode('utf-8')` to avoid Windows cp1252 errors). Union arrays by unique key (title/date), sort chronologically, renumber IDs sequentially, write result.

**Functional files** — take the NEWER (more recently edited) version:

| File | Resolution | Rationale |
|------|-----------|-----------|
| `commands/*.md` | Take the version with more recent edits. If both branches edited the same command, **ask user**. | Commands are iterated individually — the latest version reflects most recent improvements |
| `skills/*/SKILL.md` | Take newer version. If both edited same skill, **ask user**. | Same as commands |
| `hooks/*.py` | Take newer version. If both edited same hook, check `git log --oneline --format="%h %ai %s" <branch> -- <file>` on both sides to identify which has the authoritative overhaul, then **ask user** to confirm. | Behavioral code — can't safely merge without understanding intent |
| `settings.json` | Union all permission entries additively. Never remove permissions. | Permissions accumulate across sessions |
| `scripts/*.sh` | Take newer version | Utility scripts, latest version is authoritative |

**Session artifacts** — safe to discard branch version:

| File | Resolution | Rationale |
|------|-----------|-----------|
| `plans/*.md` | Keep both (unique filenames, no conflicts expected) | Historical artifacts |
| `settings.local.json` | Take current main version (local-only, not shared) | Per-machine config |
| `hooks/__pycache__/` | Delete and regenerate | Build artifacts |

### Post-Resolution Verification

After resolving ALL conflicts in a rebase:
1. `git rebase --continue` (repeat for each conflicted commit)
2. Build the project — compilation errors reveal broken resolutions
3. For serialized data files: run the Step 2 post-resolution check
4. `git push --force-with-lease` to update the PR branch
5. Proceed to Step 3

---

## Step 3: Classify Domain

Classify using the [PR Classification](agents/pr_classification.md) tables. The classification determines whether user testing is required (Step 7) and which labels to apply (Step 8).

---

## Step 4: Branch Checkout

```bash
# Stash any local changes
git stash --include-untracked -m "merge-pr: stash before PR #<N>"

# Switch to PR branch
git checkout <branch>

# CRITICAL: Update submodule to match the branch's pointer
# git checkout updates the pointer but NOT the working tree — without this, builds fail with missing types
git submodule update --init --recursive
```

---

## Step 5: Build Verification

Run the project's build.

If build fails:
1. Present compilation errors
2. Attempt to fix — apply fixes, push to PR branch
3. Re-build to confirm

---

## Step 6: Automated Tests

### 6a. Run the Regression Gate

Run the project's regression gate (`change_control` §Gate cadence names it). The gate owns suite execution, count validation, silent-skip detection and failure handling.

If tests fail and fixes are applied, push fixes to the PR branch and re-run the gate.

### 6b. Surface the Pre-Merge Checklist

After the regression gate produces its pre-commit checklist, re-render it here with **PR-specific items appended** for the merge decision. PR merge is a higher-stakes gate than session commit (irreversible, public, paired-submodule entanglement), so the checklist gains additional rows the in-session commit doesn't need:

```
## Pre-Merge Checklist

(items 1-N from the regression gate's pre-commit checklist)

[<state>] PR is mergeable per `gh pr view` (no conflicts, all checks passing)
[<state>] Submodule pointers compatible (paired submodule PRs merged FIRST if a pointer changed)
[<state>] PR title and description accurate; labels applied (Step 8 will enforce hygiene)
[<state>] Manual verification checklist (Step 7) — N/A when Step 3's classification needs none

Verdict: PROCEED TO MERGE | RESOLVE BLOCKERS FIRST
```

**PR-specific self-attest rules:**
- **Mergeable checkbox:** `[x]` if Step 1's `gh pr view` returned `state: open` AND `mergeable: true` AND no failed status checks. `[ ]` if any of those failed. `[—]` never (always applicable to a PR merge).
- **Submodule pointer checkbox:** `[—]` if the PR's diff doesn't touch a submodule pointer. `[x]` if a pointer changed AND the paired submodule PR has merged per Step 1's check. `[ ]` if a pointer changed and the submodule PR hasn't merged yet → STOP, do not proceed past Step 7.
- **PR title/labels checkbox:** `[ ]` initially (verified in Step 8). The orchestrator may render `[—]` here if Step 8 has already run in this session.
- **Manual verification checkbox:** `[—]` when Step 3's classification needs no manual verification. `[x]` if Step 7's checklist is 100% complete or auto-approved (B.1/B.3). `[ ]` if Step 7 returned partial coverage and user chose "Merge anyway" — note partial coverage in commit footer.

**Decision rule (same shape as `/session_end` Phase 7a):**
- All items `[x]` or `[—]` → proceed to Step 7 (or Step 8 if Step 7 was N/A).
- Any item `[ ]` → STOP. Use `AskUserQuestion` to either resolve, acknowledge with rationale, or abort the merge.

This checklist is the gate decision before the actual `gh pr merge` runs in Step 9.

---

## Step 7: Manual Verification Gate (PRs That Need It)

**Do NOT skip this step** when Step 3's classification says the PR needs manual verification (in a game, any PR that adds or affects gameplay scripts or systems).
**Skip this step** for PRs the classification exempts.

**When in doubt, STOP and ask if the user would like to verify manually.**

The project's manual-verification checklist command (`change_control` §Gate cadence names it) owns the checklist's location, filename and generation.

### 7.1 Evaluate Checklist State

**Case A — No checklist exists:** generate it with that command, report its path, and wait for the user to verify on their own schedule. Do NOT run the app and tell the user to do something. When the user returns, re-read the checklist and evaluate as Case B.

**Case B — Checklist exists:** count checkboxes:
- `checked` = lines matching `- [x]` (case-insensitive)
- `total` = lines matching `- [x]` + `- [ ]`

**B.1 — 100% complete** (`checked == total`, `total > 0`): report `Manual verification: ✅ {checked}/{total} items complete (100%)` and proceed directly to Step 8. No user wait needed.

**B.2 — Partially complete or not started** (`checked < total`): report `{checked}/{total} items complete ({percent}%)` with the first 5 unchecked items summarized, then ask the user:
- **"Continue verifying"** — Pause and wait for the user to complete the remaining items. When they return, re-read and re-evaluate.
- **"Update checklist"** — Run the checklist command's update mode to add any new commit coverage, then re-read and re-evaluate.
- **"Merge anyway"** — User accepts incomplete verification. Note partial coverage in the PR description during Step 8: `"Manual verification: partial (X/N items, Y%)"`. Proceed to Step 8.
- **"Abort"** — Stop the merge workflow.

**B.3 — Zero checkboxes** (`total == 0`, file exists): nothing needs manual verification. Auto-approve and proceed to Step 8.

### 7.2 Feedback Loop

If user reports issues after verifying:
1. Fix them
2. Rebuild (Step 5)
3. Re-run regression gate (Step 6)
4. Run the checklist command's update mode to capture any new commits from fixes
5. Push fixes to PR branch
6. Re-evaluate checklist (return to 7.1)

Loop until user is satisfied or chooses "Merge anyway."

---

## Step 8: PR Hygiene

### Auto-Label
Apply labels using the [PR Classification](agents/pr_classification.md) procedure (Domain + Type labels, colors, and `gh label` commands).

### Fix Title/Description
If the PR title is auto-generated or the body is empty:
- Suggest an improved title following conventional commits format: `<type>(<scope>): <description>`
- Draft a description body summarizing the changes
- Apply with `gh pr edit <N> --title "..." --body "..."`
- **Ask user to confirm** before editing

---

## Step 9: Merge

**ALWAYS ask user to confirm before merging.**

```bash
gh pr merge <N> --merge --delete-branch
```

### Submodule Branch Cleanup
`--delete-branch` only affects the current repo; paired submodule branches live on a different remote. The submodule's path-scoped rule decides whether cleanup is yours or the user's. When it is yours, for each paired branch that still exists (`git -C <submodule> ls-remote --heads origin <branch>`):
- Close the submodule PR if one is open: `gh pr list --repo <sub-remote> --head <branch> --state open --json number -q '.[0].number'` → `gh pr close <sub-pr-number> --repo <sub-remote>`
- Delete the remote branch: `git -C <submodule> push origin --delete <branch>`
- Delete the local branch if it exists: `git -C <submodule> branch -d <branch> 2>/dev/null || true`

Then return to main:
```bash
git checkout main
git pull

# CRITICAL: Update submodules after pull — the merged PR may have changed a pointer
git submodule update --init --recursive
```

Pop stash if one was created:
```bash
git stash pop 2>/dev/null || true
```

---

## Constraints

- **Never force-push** to PR branches
- **Never merge without user confirmation**
- **Build before test** — compilation errors waste user testing time
- **Tests before user** — automated tests are cheaper than human time
- **Respect submodule merge order** — paired submodule branches merge first
- **Don't run the app during user verification** — wait for the user to verify independently
- **Labels are additive** — see [PR Classification](agents/pr_classification.md)
- **Never modify code without building + testing after** — every fix must be verified before pushing
