---
disable-model-invocation: true
allowed-tools: Bash(gh pr create:*), Bash(gh pr list:*), Bash(gh pr edit:*), Bash(gh pr view:*), Bash(gh label:*), Bash(git log:*), Bash(git diff:*), Bash(git status:*), Bash(git -C Jmodot *), Bash(git branch:*), Bash(git rev-parse:*), Glob, Grep, Read
description: Create PRs for {{PROJECT_NAME}} and paired Jmodot submodule
---

Create a {{PROJECT_NAME}} PR (and paired Jmodot PR if needed) with enforced conventions: cross-references, labels, body template, and submodule dependency warnings.

**Prerequisite:** Branch is pushed to origin (run `/commit_push` or `/clean_push` first).

## Arguments
- `$ARGUMENTS` — Optional PR title override. If omitted, derives from commit history.

---

## Step 1: Gather Context

```bash
git branch --show-current
git status
git log main..HEAD --oneline
git diff main..HEAD --stat
```

Verify:
- Not on `main` — abort if so
- Branch is pushed to origin (no unpushed commits)
- There are commits ahead of `main`

Extract the worktree name from the branch: `claude/<worktree-name>` → `<worktree-name>`.

**Mode detection:**
```bash
gh pr list --head <current-branch> --state open --json number,title,body
```
- No PR for this branch → **CREATE mode** (Step 5-Create).
- PR exists → **UPDATE mode** (Step 5-Update). Cache `body` for fidelity-preserving merge.

---

## Step 2: Detect Jmodot Changes

Check if this branch includes a Jmodot submodule pointer change:

```bash
git diff main..HEAD -- Jmodot
```

If the diff shows a submodule pointer change → **Jmodot PR required**.

---

## Step 3: Create Jmodot PR (if needed)

If Jmodot has changes:

1. Verify the Jmodot branch exists on remote:
   ```bash
   git -C Jmodot ls-remote --heads origin jmodot/<worktree-name>
   ```
   If not found, **STOP** — the push commands should have created this. Ask user to run `/commit_push` or `/clean_push` first.

2. Check if a Jmodot PR already exists:
   ```bash
   gh pr list --repo <jmodot-remote> --head jmodot/<worktree-name> --state open --json number,title
   ```

3. **If no PR exists**, create one:
   ```bash
   gh pr create --repo <jmodot-remote> --base master --head jmodot/<worktree-name> \
     --title "<PP PR title>" \
     --body "$(cat <<'EOF'
   Paired with {{PROJECT_NAME}} branch `claude/<worktree-name>`

   **Merge this FIRST** before the {{PROJECT_NAME}} PR.

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

4. **If PR exists**, note its number for cross-referencing in Step 5.

---

## Step 4: Classify & Label

Classify and label using the [PR Classification](agents/pr_classification.md) procedure (Domain, Type, Label Colors, and `gh label` commands). Use `git diff main..HEAD --name-only` to determine changed files.

---

## Step 5-Create: Create {{PROJECT_NAME}} PR (CREATE mode only)

### Title
Use `$ARGUMENTS` if provided. Otherwise, derive from commit history:
- Single-concern branch → use the primary commit's message
- Multi-concern branch → summarize with conventional commit format: `<type>(<scope>): <description>`
- Title must be under 70 characters

### Body Template

```bash
gh pr create --title "<title>" --body "$(cat <<'EOF'
## Summary
- <bullet 1: primary change>
- <bullet 2: secondary change, if any>
- <bullet 3: etc.>

## Jmodot dependency
Requires Jmodot PR [jmund15/Jmodot#<N>](<url>) — <brief description of Jmodot changes>. **Merge Jmodot first.**

## Key design decisions
- <decision 1 with rationale>
- <decision 2 with rationale>

## Test plan
- [x] <automated test count> unit/integration tests
- [x] Full regression gate: <total>/0 (Logic: <n>, Integration: <n>, Sanity: <n>)
- [ ] Manual playtest for <subjective aspects>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

**Template rules:**
- **Jmodot dependency section:** Only include if Step 2 detected submodule changes. Omit entirely for non-Jmodot PRs.
- **Key design decisions:** Only include if there are notable architectural choices. Omit for simple fixes.
- **Test plan:** Always include. Mark automated items as `[x]`, manual items as `[ ]`.
- **Regression gate counts:** Pull from recent `/regression_gate` output or commit messages.

### Apply Labels (Step 4)

After PR creation, apply the labels determined in Step 4.

---

## Step 5-Update: Update existing {{PROJECT_NAME}} PR (UPDATE mode only)

Append a `## Review-pass updates` (or `## Follow-up commits`) section to the cached body. Two hard rules:

**Rule 1 — Preserve original body fidelity.** Summary + Key design decisions + Test plan from the cached body stay verbatim, EXCEPT for surgical fixes to claims invalidated by new commits (e.g., a removed field that the original body described as load-bearing). Call out each surgical fix in the new section so the diff is visible. Never rewrite the original PR's prose — that's revisionism that obscures original intent and erases reviewer history.

**Rule 2 — Update length must be proportional to commits added.** Update sections are NOT mini-PR-bodies. Detail belongs in commit messages; the PR body needs only the WHAT changed at a high level. Rough length budget:

| Commits added | Review-pass section length target |
|---|---|
| 1–3 | 1 short paragraph + ≤5 bullets |
| 4–8 | 1 paragraph + grouped bullets by theme (bugs/cleanup/tests/deferred); ≤30% of total body length |
| 9+ | Consider splitting into a follow-up PR — at this point the update is its own PR |

Cite commit hashes (e.g., `00675e2e → 4b5047ad`) once at the section top; let reviewers click through for per-commit detail rather than restating it in the body.

Title: leave unchanged unless the update fundamentally repositions the PR's intent. Refresh `Test plan` checkboxes only for items the new commits affect (add a `Full regression rerun before merge` bullet if HSM / framework / public-API plumbing changed).

```bash
gh pr edit <pr-number> --body "$(cat <<'EOF'
<verbatim original Summary>
<verbatim original Key design decisions, with surgical fixes if any>

## Review-pass updates (<N> commits post-original)
<one short paragraph: scope + commit range>

- **<theme>**: <one-line highlight>
- **<theme>**: <one-line highlight>
- **Deferred**: <worklog candidates>

See commit messages for per-finding rationale.

<refreshed Test plan>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Labels: re-check whether the new commits introduce a domain or type not yet labeled (e.g., review-pass added `tests` where original was pure `feature` → add `tests` label). Don't churn existing labels.

---

## Step 6: Update Jmodot PR (if exists)

If a Jmodot PR was created or found in Step 3, update its body to cross-reference the PP PR:

```bash
gh pr edit <jmodot-pr-number> --repo <jmodot-remote> --body "$(cat <<'EOF'
## Summary
- <Jmodot-specific changes summary>

Paired with {{PROJECT_NAME}} PR [jmund15/{{PROJECT_NAME}}#<pp-pr-number>](<pp-pr-url>)
Paired with {{PROJECT_NAME}} branch `claude/<worktree-name>`

**Merge this FIRST** before the {{PROJECT_NAME}} PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Step 7: Summary

Print a summary:

```
╔════════════════════════════════════════════════╗
║  PRs Created                                    ║
╠════════════════════════════════════════════════╣
║  PP:     #<N> — <title>                         ║
║  Jmodot: #<N> — <title>  (or "N/A")            ║
║  Labels: <label1>, <label2>                     ║
║  Cross-refs: ✅ (or ❌ if no Jmodot)            ║
╚════════════════════════════════════════════════╝
```

---

## Constraints

- **Read-only for code** — this command never modifies source files
- **Prerequisite: pushed** — do not commit or push; that's `/commit_push`'s job
- **Ask before creating OR editing** — show the user the title and body before running `gh pr create` / `gh pr edit`
- **UPDATE mode preserves original body** — never rewrite original Summary / Key design decisions; surgical fixes for stale-by-new-commits claims only, called out in the Review-pass section
- **UPDATE section length is proportional to work added** — see Step 5-Update length budget. Detail belongs in commit messages, not duplicated into the PR body
- **Cross-reference both directions** — PP body links to Jmodot PR, Jmodot body links to PP PR
- **Labels at creation time** — don't defer to `/merge_pr`
- **Jmodot remote:** derive from `git -C Jmodot remote get-url origin` (don't hardcode)
