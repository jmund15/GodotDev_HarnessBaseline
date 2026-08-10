---
disable-model-invocation: true
allowed-tools: Bash(gh pr create:*), Bash(gh pr list:*), Bash(gh pr edit:*), Bash(gh pr view:*), Bash(gh label:*), Bash(git log:*), Bash(git diff:*), Bash(git status:*), Bash(git branch:*), Bash(git rev-parse:*), Bash(rm:*), Glob, Grep, Read, Write
description: Create or update the branch's PR with enforced conventions - labels, body template, fidelity rules
---

Create a {{PROJECT_NAME}} PR (or update the branch's existing one) with enforced conventions: labels, body template, and fidelity-preserving updates.

**Prerequisite:** Branch is pushed to origin (run `/commit_push` or `/clean_push` first).

PROJECT-CONFIG: a project with a paired submodule/repo (its PR must merge first)
inserts its pairing steps per its own layer's procedure — detect the pointer
change after Step 1, create/update the paired PR, and cross-reference both
bodies.

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
- No PR for this branch → **CREATE mode** (Step 3-Create).
- PR exists → **UPDATE mode** (Step 3-Update). Cache `body` for fidelity-preserving merge.

---

## Step 2: Classify & Label

If `agents/pr_classification.md` exists (supplied by the code layer), classify and label per that procedure (Domain, Type, Label Colors, and `gh label` commands). Otherwise classify by conventional-commit type alone (`feat`/`fix`/`refactor`/`chore`/`docs`) and apply matching labels if the repo defines them. Use `git diff main..HEAD --name-only` to determine changed files.

---

## Step 3-Create: Create {{PROJECT_NAME}} PR (CREATE mode only)

### Title
Use `$ARGUMENTS` if provided. Otherwise, derive from commit history:
- Single-concern branch → use the primary commit's message
- Multi-concern branch → summarize with conventional commit format: `<type>(<scope>): <description>`
- Title must be under 70 characters

### Body Template

**Body-file pattern (all PR bodies in this command):** per Shell Discipline, never `--body "$(cat <<'EOF' ...)"` — `$()` triggers a manual permission prompt every time. Instead, `Write` the body to a temp file (session scratchpad), pass `--body-file`, `rm` the temp after.

`Write` this body to a temp file, then `gh pr create --title "<title>" --body-file "<temp-file>"`:

```markdown
## Summary
- <bullet 1: primary change>
- <bullet 2: secondary change, if any>
- <bullet 3: etc.>

## Key design decisions
- <decision 1 with rationale>
- <decision 2 with rationale>

## Verification
- <optional: one line on how the change was verified, if non-obvious>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

**Template rules:**
- **Key design decisions:** Only include if there are notable architectural choices. Omit for simple fixes.
- **Verification:** One optional notes line; omit when the Summary already makes it obvious. (A layer with a test suite replaces this with its own test-plan template.)

### Apply Labels (Step 2)

After PR creation, apply the labels determined in Step 2.

---

## Step 3-Update: Update existing {{PROJECT_NAME}} PR (UPDATE mode only)

Append a `## Review-pass updates` (or `## Follow-up commits`) section to the cached body. Two hard rules:

**Rule 1 — Preserve original body fidelity.** Summary + Key design decisions from the cached body stay verbatim, EXCEPT for surgical fixes to claims invalidated by new commits (e.g., a removed field that the original body described as load-bearing). Call out each surgical fix in the new section so the diff is visible. Never rewrite the original PR's prose — that's revisionism that obscures original intent and erases reviewer history.

**Rule 2 — Update length must be proportional to commits added.** Update sections are NOT mini-PR-bodies. Detail belongs in commit messages; the PR body needs only the WHAT changed at a high level. Rough length budget:

| Commits added | Review-pass section length target |
|---|---|
| 1–3 | 1 short paragraph + ≤5 bullets |
| 4–8 | 1 paragraph + grouped bullets by theme (bugs/cleanup/tests/deferred); ≤30% of total body length |
| 9+ | Consider splitting into a follow-up PR — at this point the update is its own PR |

Cite commit hashes (e.g., `00675e2e → 4b5047ad`) once at the section top; let reviewers click through for per-commit detail rather than restating it in the body.

Title: leave unchanged unless the update fundamentally repositions the PR's intent.

`Write` this body to a temp file, then `gh pr edit <pr-number> --body-file "<temp-file>"` (body-file pattern — Step 3-Create):

```markdown
<verbatim original Summary>
<verbatim original Key design decisions, with surgical fixes if any>

## Review-pass updates (<N> commits post-original)
<one short paragraph: scope + commit range>

- **<theme>**: <one-line highlight>
- **<theme>**: <one-line highlight>
- **Deferred**: <worklog candidates>

See commit messages for per-finding rationale.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Labels: re-check whether the new commits introduce a domain or type not yet labeled (e.g., review-pass added `tests` where original was pure `feature` → add `tests` label). Don't churn existing labels.

---

## Step 4: Summary

Print a summary:

```
╔════════════════════════════════════════════════╗
║  PR Created / Updated                           ║
╠════════════════════════════════════════════════╣
║  #<N> — <title>                                 ║
║  Labels: <label1>, <label2>                     ║
╚════════════════════════════════════════════════╝
```

---

## Constraints

- **Read-only for code** — this command never modifies source files
- **Prerequisite: pushed** — do not commit or push; that's `/commit_push`'s job
- **Ask before creating OR editing** — show the user the title and body before running `gh pr create` / `gh pr edit`
- **UPDATE mode preserves original body** — never rewrite original Summary / Key design decisions; surgical fixes for stale-by-new-commits claims only, called out in the Review-pass section
- **UPDATE section length is proportional to work added** — see Step 3-Update length budget. Detail belongs in commit messages, not duplicated into the PR body
- **Labels at creation time** — don't defer to `/merge_pr`
