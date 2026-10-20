---
description: Refresh the subsystem registry after folder-shape or substantial subsystem changes
---

# /sync_subsystems

Source of truth for the upcoming `architecture_brainstorm` subsystem-breadth scope-litmus. Invoked from `/session_end` Phase 4 (conditional) or manually after subsystem-shape work.

## Step 0: Signal gate

Run `python3 .claude/tools/close_predicates.py --only sync_subsystems` and read its `basis`:

- `shape` — a changed path whose first component is NOT already in the `subsystems:` YAML of `project_subsystems/SKILL.md` AND not in the reserved-name list.
- `density` — subsystems with at least `DENSITY_THRESHOLD` changed files under their `paths:` (likely warrants a refresh of that subsystem's one-line summary or "depends on" wording).

A `null` value (digest or registry absent) runs the command. If **neither** signal is present, print `[sync_subsystems] No subsystem-shape or density signal this session. Skipping.` and exit.

## Step 1: Enumerate

Walk top-level directories under the project root. Exclude reserved names listed in `project_subsystems/SKILL.md`: `.claude/`, `addons/`, `Jmodot/`, `Docs/`, `logs/`, `TestResults/`, `Temp/`, `gdunit4_testadapter_v5/`, `script_templates/`.

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

Edit `project_subsystems/SKILL.md`:
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
