---
description: Archive finished plans and state files, and sweep worktrees, scratch and root strays
---

# /harness_prune — the recurring harness sweep

The retroactive sweep for what each surface's own close missed: `/drive` archives the plan and state file it finishes, and this command catches the rest. Plans and state files are only ever moved to an archive folder, never deleted. Worktree removal stays `/worktree_prune`'s call. `.claude/reference/` is out of scope.

## Argument

None: run every step. `<name>`: classify the one plan `.claude/plans/<name>` and skip the other steps. No such file → print `No plan named <name> in .claude/plans/.` and stop without changes.

## Steps

### 1. Plans and state files

Enumerate both sets, each row labelled `tracked` or `untracked`:

```bash
git ls-files -- ':(top,glob).claude/plans/*.md' ':(top,glob).claude/work/*.md'
git ls-files --others --exclude-standard -- ':(top,glob).claude/plans/*.md' ':(top,glob).claude/work/*.md'
```

Classify each file from its own claims, never its filename: its status or Definition-of-Done section, its completion banner, the status of any Roadmap Part it cites, `git log --oneline -- <path>`, and one search per row of its `Surfaces touched` table against the current tree. A state file's slice list is its claim.

- **SHIPPED:** every named deliverable is present, the file carries a completion banner, its cited Part reads `complete`, or every slice of a state file reads done.
- **SUPERSEDED:** a later commit or plan explicitly replaces it. Name that artifact.
- **ACTIVE:** partial or no implementation. Report the next action.
- **UNCLEAR:** the evidence reaches no other verdict. Name the gap.

A tracked, clean SHIPPED or SUPERSEDED file gets `**Archived:** <date> /harness_prune — <evidence clause>` prepended and moves with `git mv` into the archive folder beside it. An untracked or dirty file never moves: list its state and next action.

### 2. Worktrees

Invoke `/worktree_prune` unchanged and copy its table and recommendations into the report.

### 3. Scratch

`.claude/scratch/` is gitignored, so an entry exists only on this machine. Keep what is still locally relevant, archive to the vault what a later session could need, delete the rest. `tools/scratch_prune.py` makes the deterministic calls (git checkouts, references, recency, vault reads); a model judges recall value. Its docstring owns the thresholds.

1. `python3 .claude/tools/scratch_prune.py trim` deletes old bulk files inside cited folders. Report its `trimmed=` line.
2. `python3 .claude/tools/scratch_prune.py inventory --out .claude/scratch/scratch_inventory_<sid8>.json`. Give no verdict to a row marked `unverified`: a vault input went unread, and `apply` refuses it.
3. A verified CANDIDATE row with no `proseFiles` gets DELETE, reason `no prose`. Send the rows with `proseFiles` to `/delegate` as read-only jobs, each brief carrying a batch of rows and enough of each prose file to judge it, returning `{name: {verdict, reason}}`. A row's `referrers.prose` shows what a later session may look for; only `referrers.code` holds a row. Verdicts:
   - ARCHIVE: the prose holds findings, decisions or measurements a later session could need that no tracked doc carries. Add `archiveFiles` (paths relative to the entry) when the keepable files are a small part of a large entry.
   - DELETE: logs, scripts, JSON, dumps, briefs and superseded drafts.
   - KEEP: only with a stated reason.
4. Merge every verdict into `.claude/scratch/scratch_verdicts_<sid8>.json`. Every verified CANDIDATE row needs one. Recover a missing batch from its output first; re-send it only with narrowed input or after a transient failure.
5. `python3 .claude/tools/scratch_prune.py apply --inventory <inventory path> --verdicts <verdicts path>`. It never removes a row holding `gitWork` (KEEP-GIT, for the owner), skips a row that changed since the inventory, and exits 1 when any row failed.

Any non-zero exit blocks the step, and nothing goes to `apply` while a verdict is missing.

### 4. Project steps

A project adds a sweep of its own surfaces as `.claude/commands/reference/prune_project_<name>.md`. Follow each one present, in name order, and write the report section it names. None: write `### Project steps` as `None.`

### 5. Strays

`python3 .claude/tools/root_strays.py list` names tracked top-level `.claude/` entries no other tracked file mentions, past the tool's recency floor. Report only: an unreferenced entry may still be read by a person or loaded implicitly, so the owner decides each one.

## Report

Write `.claude/scratch/harness_prune_<sid8>.md`. A failed step still gets its heading, with the exact failure under it.

```markdown
## /harness_prune — <date>

### Plans and state files
| File | Git | Verdict | Evidence | Next action |
|---|---|---|---|---|
Archived (N): <file> — <evidence clause> ...

### Worktrees
<worktree_prune's table and recommendations, verbatim>

### Scratch
| Bucket | Count | Bytes |
|---|---|---|
Vault inputs: read N, unread N; unverified rows N.
Apply: archived N, deleted N, kept N, skipped N, failed N (<failures>).
KEEP-GIT (owner decides): <entry> — <bytes>, <age>, <gitWork> ...

### Project steps
<each project step's section, or None.>

### Strays
<entry> — <files> files, last touched <age> days ago (owner decides) ..., or None
```

Print the report path, the archive count, the worktree result, the scratch totals, each project step's counts and the stray count.
