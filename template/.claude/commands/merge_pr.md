---
disable-model-invocation: true
allowed-tools: Bash(gh pr view:*), Bash(gh pr edit:*), Bash(gh pr merge:*), Bash(gh pr close:*), Bash(gh pr list:*), Bash(gh label:*), Bash(git stash:*), Bash(git checkout:*), Bash(git pull:*), Bash(git rebase:*), Bash(git push:*), Bash(git add:*), Bash(git branch:*), Bash(git -C Jmodot *), Bash(git submodule:*), Bash(dotnet build:*), Bash(gdunit4:*), Glob, Grep, Read, Edit, Task, mcp__obsidian__obsidian_list_notes, mcp__obsidian__obsidian_read_note
description: "Build, test, and merge a single PR"
---

Build, test, and merge a single PR. Assumes code review has already been done via `/review-pr`.

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
  > **Why the threshold?** Rebase replays each commit individually — excellent for isolation, but at 50+ commits the same file can conflict repeatedly, causing resolution fatigue and silent errors. Merge resolves each conflict exactly once with full branch context. The conflict resolution rules (interface checks, `.tres` verification, feature spot-checks) apply equally to both strategies.

  Ask user to confirm before syncing.
  > **Note (when called from `/review-prs`):** The orchestrator may have already synced this branch preemptively in Phase 3. This mergeability check is a safety net that catches any remaining issues (e.g., conflicts from fix commits applied in Phase 2).
- If Jmodot submodule pointer changed: warn about merge order. Jmodot branches must merge to master FIRST.
  - **Find the paired Jmodot branch:** Convention is `jmodot/<worktree-name>` (e.g., {{PROJECT_NAME}} branch `claude/naughty-mayer` → Jmodot branch `jmodot/naughty-mayer`). Verify it exists on the Jmodot remote: `git -C Jmodot ls-remote --heads origin "jmodot/*"`.
  - If the Jmodot PR has conflicts with Jmodot master: rebase it (`git -C Jmodot rebase origin/master`, resolve conflicts, `git -C Jmodot push --force-with-lease`).
  - Merge the Jmodot PR via `gh pr merge <jmodot-pr> --repo <jmodot-remote> --merge --delete-branch`.
  - **CRITICAL — Do NOT update the PP branch's submodule pointer after the Jmodot merge.** Leave the PP branch pointing at its original Jmodot commit. GitHub resolves submodule pointers at merge time — the commit is reachable via Jmodot master's merge history. Updating the pointer to Jmodot's latest master pulls in commits from OTHER unrelated PRs, introducing API changes the PP branch was never designed for. The PP submodule pointer gets reconciled naturally when the PP PR merges to main.

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
| **Resolvable with care** | Resolve using category rules below, verify with build + tests | `.tres` ext_resource conflicts, `.csproj` package additions |
| **Ask user** | Present conflict context and wait for direction | Logic disagreements, behavioral changes, ambiguous design intent |

> **Default posture: conservative.** If you aren't confident the resolution preserves correctness, STOP and present the conflict to the user with both versions and your recommendation. **If ever unsure which side to take, ask the user directly — never guess.**

### Category Resolution Rules

#### C# Logic Files (`.cs` — non-test)

| Scenario | Resolution | Escalate? |
|----------|-----------|-----------|
| Both branches ADD new methods/classes (no overlap) | Keep both | No |
| Both modify the SAME method body | **Ask user** — behavioral intent matters | **Yes** |
| One branch renames/moves, other modifies | Apply modifications to the renamed version | No, unless semantics changed |
| `using` statement conflicts | Union all `using` statements, remove duplicates | No |
| Registry/Dictionary additions (e.g., `PushinPotionRegistry`) | Keep ALL entries from both branches | No |

#### C# Test Files (`.cs` in `Tests/`)

| Scenario | Resolution | Escalate? |
|----------|-----------|-----------|
| Both branches add new test methods | Keep ALL tests from both branches | No |
| Shared fixture changes (e.g., `SpellTestFixture`, `ArchetypePaths`) | Union additions — keep all new fixture entries from both | No |
| Test modifies assertion on same method | **Ask user** — expected values may reflect different design intent | **Yes** |
| `[DataPoint]` / `[TestCase]` additions | Keep all data points from both branches | No |

#### Data Files (`.tres` / `.tscn`)

**⚠️ Highest risk category.** Git can auto-merge `.tres` files but produce **semantically broken** results — sub_resources referencing ext_resource IDs that only exist in one branch.

| Scenario | Resolution | Escalate? |
|----------|-----------|-----------|
| Both branches add attributes to same Dictionary | **Keep ALL entries.** Assign unique `ext_resource` IDs (increment suffix). Update `load_steps` count. Add ALL required `ext_resource` declaration lines at file top. | No, but verify carefully |
| Both modify the SAME attribute value | **Ask user** — design intent matters | **Yes** |
| Scene node additions (`.tscn`) | Keep both node trees, verify no name collisions. **Quick triage:** compare node counts via `git show :2:<file> \| grep "^\[node" \| wc -l` vs `:3:` — a large disparity (e.g., 517 vs 37) means one version has major additions; use it as the base. | No |
| `uid://` reference conflicts | Use `get_uid` MCP tool to verify correct UIDs. Never guess. | No |

**Post-resolution `.tres` checklist:**
1. Every `sub_resource` block references only `ext_resource` IDs declared at the file top
2. `load_steps` count matches actual number of `ext_resource` + `sub_resource` entries + 1
3. No duplicate `ext_resource` IDs with different paths

#### Metafiles

| File | Resolution |
|------|-----------|
| `.csproj` | Union all `<PackageReference>` and `<Compile>` entries. Remove duplicates. |
| `.import` | Regenerate — delete conflicted `.import` files, run `godot --headless --import --quit` |
| `.uid` / `uid_cache.bin` | Regenerate via Godot import. Never hand-edit. |
| `.runsettings` | Take newer version (functional config, not accumulated data) |

#### Submodule Pointer (`Jmodot/`)

**Never resolve submodule pointer conflicts by picking a side.** Both branches point to commits that may not exist on Jmodot master yet.

Resolution:
1. Confirm BOTH Jmodot branches are merged to Jmodot master first (Step 1 submodule check)
2. After Jmodot merges, update submodule to latest Jmodot master:
   ```bash
   git -C Jmodot fetch origin
   git -C Jmodot checkout master
   git -C Jmodot pull
   git add Jmodot
   ```
3. Continue rebase

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
2. `dotnet build` — compilation errors reveal broken resolutions
3. For `.tres`/`.tscn` changes: open file and verify `ext_resource`/`sub_resource` integrity
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

```bash
dotnet build
```

If build fails:
1. Present compilation errors
2. Attempt to fix — apply fixes, push to PR branch
3. Re-build to confirm

---

## Step 6: Automated Tests

### 6a. Run the Regression Gate

Invoke `/regression_gate` to run all test suites. See [`regression_gate.md`](regression_gate.md) for the canonical test execution procedure (build, run suites, count validation, silent skip detection, failure handling).

If tests fail and fixes are applied, push fixes to the PR branch and re-run the gate.

### 6b. Surface the Pre-Merge Checklist

After the regression gate produces its Pre-Commit Checklist (see [`/regression_gate` Step 7b](regression_gate.md)), re-render it here with **PR-specific items appended** for the merge decision. PR merge is a higher-stakes gate than session commit (irreversible, public, paired-submodule entanglement), so the checklist gains additional rows the in-session commit doesn't need:

```
## Pre-Merge Checklist

(items 1-N from /regression_gate Step 7b — Logic/Integration/Sanity, silent-skip,
 JmoLogger, /session_audit, CLAUDE.md compliance, refactor parity)

[<state>] PR is mergeable per `gh pr view` (no conflicts, all checks passing)
[<state>] Jmodot submodule pointer compatible (paired Jmodot PR merged FIRST if pointer changed; CLAUDE.md §6)
[<state>] PR title and description accurate; labels applied (Step 8 will enforce hygiene)
[<state>] Manual playtest checklist (Step 7) — N/A on Logic/Data/Meta-only PRs

Verdict: PROCEED TO MERGE | RESOLVE BLOCKERS FIRST
```

**PR-specific self-attest rules:**
- **Mergeable checkbox:** `[x]` if Step 1's `gh pr view` returned `state: open` AND `mergeable: true` AND no failed status checks. `[ ]` if any of those failed. `[—]` never (always applicable to a PR merge).
- **Jmodot pointer checkbox:** `[—]` if the PR's diff doesn't touch the submodule pointer. `[x]` if pointer changed AND the paired Jmodot PR has merged to master per Step 1's check. `[ ]` if pointer changed and Jmodot PR hasn't merged yet → STOP, do not proceed past Step 7.
- **PR title/labels checkbox:** `[ ]` initially (verified in Step 8). The orchestrator may render `[—]` here if Step 8 has already run in this session.
- **Manual playtest checkbox:** `[—]` for pure Logic/Data/Meta PRs (per Step 3 classification). `[x]` if Step 7's checklist is 100% complete or auto-approved (B.1/B.3). `[ ]` if Step 7 returned partial coverage and user chose "Merge anyway" — note partial coverage in commit footer.

**Decision rule (same shape as `/session_end` Phase 7a):**
- All items `[x]` or `[—]` → proceed to Step 7 (or Step 8 if Step 7 was N/A).
- Any item `[ ]` → STOP. Use `AskUserQuestion` to either resolve, acknowledge with rationale, or abort the merge.

This checklist is the gate decision before the actual `gh pr merge` runs in Step 9.

---

## Step 7: Playtest Quality Gate (Gameplay PRs Only)

**Do NOT skip this step** for ANY PRs that directly add or affect gameplay SCRIPTS or SYSTEMS (e.g., Refactors, Features, System/Architecture).
**Skip this step** for PURELY Logic, Data, or Meta-only PRs.

**When in doubt, STOP and ask if the user would like to playtest.**

### 7.1 Derive Checklist Filename

Strip the `claude/` prefix from the branch name, replace `/` with `-`. This matches the `/pr_test_checklist` filename convention.

Example: `claude/critter-scurry-ingredients-bVx4D` → `critter-scurry-ingredients-bVx4D`

### 7.2 List the PRTesting Folder

```
Glob("*", path="{{VAULT_ROOT}}/DevProjects/{{PROJECT_NAME}}/Claude/TODO/PRTesting")
```

### 7.3 Evaluate Checklist State

Check whether `{filename}.md` exists in the listing.

**Case A — No checklist exists:**

Invoke `/pr_test_checklist` to generate it. Then report:

```
Playtest checklist generated:
  📄 DevProjects/{{PROJECT_NAME}}/Claude/TODO/PRTesting/{filename}.md

Open in Obsidian, playtest each item, and check boxes as you go.
Waiting for your testing results...
```

**IMPORTANT:** Do NOT run the game and tell user to do something. Wait for user to test on their own schedule (CLAUDE.md: "Invisibility Workflow").

When the user returns, re-read the checklist and evaluate as Case B.

**Case B — Checklist exists:**

Read the checklist:
```
obsidian_read_note(filePath: "DevProjects/{{PROJECT_NAME}}/Claude/TODO/PRTesting/{filename}.md")
```

Count checkboxes:
- `checked` = lines matching `- [x]` (case-insensitive)
- `total` = lines matching `- [x]` + `- [ ]`

**B.1 — 100% complete** (`checked == total`, `total > 0`):

```
Playtest checklist: ✅ {checked}/{total} items complete (100%)
Auto-approved — all playtest items verified.
```

Proceed directly to Step 8. No user wait needed.

**B.2 — Partially complete or not started** (`checked < total`):

```
Playtest checklist: {checked}/{total} items complete ({percent}%)

Remaining items:
  • <first 5 unchecked items summarized>
  {... and N more}
```

Ask user with options:
- **"Continue testing"** — Pause and wait for user to complete remaining items in Obsidian. When they return, re-read and re-evaluate.
- **"Update checklist"** — Run `/pr_test_checklist` (UPDATE mode) to add any new commit coverage, then re-read and re-evaluate.
- **"Merge anyway"** — User accepts incomplete testing. Note partial coverage in PR description during Step 8: `"Manual testing: partial (X/N items, Y%)"`. Proceed to Step 8.
- **"Abort"** — Stop the merge workflow.

**B.3 — Zero checkboxes** (`total == 0`, file exists):

All commits were Logic-domain — nothing to playtest. Auto-approve and proceed to Step 8.

### 7.4 Feedback Loop

If user reports issues after testing:
1. Fix them
2. Rebuild (Step 5)
3. Re-run regression gate (Step 6)
4. Run `/pr_test_checklist` in UPDATE mode to capture any new commits from fixes
5. Push fixes to PR branch
6. Re-evaluate checklist (return to 7.3)

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

### Jmodot Branch Cleanup
After the PP merge, clean up the paired Jmodot branch to prevent stale branch accumulation. `--delete-branch` only affects the current repo — paired Jmodot branches live on a different remote.

1. Derive the Jmodot branch name: if PP branch is `claude/<worktree-name>`, Jmodot branch is `jmodot/<worktree-name>`
2. Check if the paired Jmodot branch exists on the remote:
   ```bash
   git -C Jmodot ls-remote --heads origin jmodot/<worktree-name>
   ```
3. If it exists:
   - Close the Jmodot PR if one exists: `gh pr list --repo <jmodot-remote> --head jmodot/<worktree-name> --state open --json number -q '.[0].number'` → `gh pr close <jmodot-pr-number> --repo <jmodot-remote>`
   - Delete the remote branch: `git -C Jmodot push origin --delete jmodot/<worktree-name>`
   - Delete the local branch if it exists: `git -C Jmodot branch -d jmodot/<worktree-name> 2>/dev/null || true`

Then return to main:
```bash
git checkout main
git pull

# CRITICAL: Update submodule after pull — the merged PR may have changed the Jmodot pointer
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
- **Respect submodule merge order** — Jmodot branches merge first (Memory: `Git_Submodule_PR_Merge_Strategy`)
- **Don't run game during user test** — wait for user to test independently (CLAUDE.md: "Invisibility Workflow")
- **Labels are additive** — see [PR Classification](agents/pr_classification.md)
- **Never modify code without building + testing after** — every fix must be verified before pushing
