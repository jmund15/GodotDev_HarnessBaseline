---
description: Refresh the subsystem registry after folder-shape or substantial subsystem changes
---

# /sync_subsystems

Source of truth for the upcoming `architecture_brainstorm` subsystem-breadth scope-litmus. Run by `/close` when the session changed folder shape, or manually after subsystem-shape work.

## Step 0: Signal gate

List the session's changed paths: `git diff --name-only` over its commits, plus `git status --porcelain`. Run the command when either signal holds:

- **shape** — a changed path's first component is in neither the `subsystems:` YAML of `.claude/skills/project_subsystems/SKILL.md` nor its `reserved_paths:` list.
- **density** — the changes under one subsystem's `paths:` are broad enough that its one-line summary or "depends on" wording may be stale.

A missing registry runs the command. If **neither** signal is present, print `[sync_subsystems] No subsystem-shape or density signal this session. Skipping.` and exit.

## Step 1: Enumerate

Walk top-level directories under the project root. Exclude the names in the `reserved_paths:` list of the registry.

## Step 2: Parse registry

Extract every `paths:` token from the `subsystems:` YAML block in `.claude/skills/project_subsystems/SKILL.md`.

## Step 3: Diff and classify

- **Add candidates** — on disk, not in registry.
- **Remove candidates** — in registry, not on disk.
- **Rename candidates** — one add + one remove with overlapping git history (use `git log --diff-filter=R --follow`).
- **Refresh candidates** — Step 0's density signal flagged an existing subsystem; the per-subsystem entry's summary may be stale.

## Step 4: Propose per candidate

Decide each candidate from its evidence (paths, git history, the files' contents) and apply it in Step 5. Use `AskUserQuestion` only when the evidence fits two dispositions equally.

- **Add candidate** → extend the existing subsystem `id` whose scope covers it (append to `paths:`), or create a new subsystem with its id, summary and depends-on relationships.
- **Remove candidate** → delete it, or mark it renamed when a rename candidate pairs with it.
- **Rename candidate** → apply the source/destination pairing git history shows.
- **Refresh candidate** → rewrite the summary from the current summary and a sample of changed files.

## Step 5: Apply

Edit `.claude/skills/project_subsystems/SKILL.md`:
- YAML block updated for adds/removes/renames.
- For new subsystems: also append a *Subsystem Details* section entry (don't leave human-readable section out of sync with YAML).
- For refresh: edit the per-subsystem details paragraph.

If R12 placement rules in [`structure_rules.md`](../skills/architecture_philosophy/structure_rules.md) need new entries, remind the user — do not edit `structure_rules.md` directly.

## Step 6: Report

Print a final diff summary (added / removed / renamed / refreshed) so the user can review before commit. Stage edits but do not auto-commit.

## What it does NOT do

- Does not enforce placement decisions — the project's structure audit, where it ships one, owns that.
- Does not auto-commit.
- Does not run on every session — Step 0 gates it.
