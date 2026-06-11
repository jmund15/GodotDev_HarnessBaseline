---
description: When the session changed subsystem shape (new/renamed/removed folder, or significant changes to one subsystem), diff actual top-level folders against the project_subsystems registry and propose updates.
---

# /sync_subsystems

Source of truth for the upcoming `architecture_brainstorm` subsystem-breadth scope-litmus. Invoked from `/session_end` Phase 4b (conditional) or manually after subsystem-shape work.

## Step 0: Signal gate

Run `git diff --name-status HEAD` (or against the session's start ref if known). Classify:

- **Subsystem-shape change** — any added (`A`) or renamed (`R`) path whose first component is NOT already in the `subsystems:` YAML of `project_subsystems/SKILL.md` AND not in the reserved-name list.
- **Subsystem-density change** — ≥5 files added/modified under a single existing subsystem's `paths:` (likely warrants a refresh of that subsystem's one-line summary or "depends on" wording).

If **neither** signal is present, print `[sync_subsystems] No subsystem-shape or density signal this session. Skipping.` and exit. Do not proceed.

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

For each candidate, use `AskUserQuestion` with options:

- **Add candidate** → ask which existing subsystem `id` to extend (append to `paths:`) OR "new subsystem" (then ask for id, summary, depends-on relationships).
- **Remove candidate** → confirm deletion or mark as renamed.
- **Rename candidate** → confirm the source/destination pairing.
- **Refresh candidate** → present the current summary + a sample of changed files, ask the user to draft/approve a new summary.

## Step 5: Apply

Edit `project_subsystems/SKILL.md`:
- YAML block updated for adds/removes/renames.
- For new subsystems: also append a *Subsystem Details* section entry (don't leave human-readable section out of sync with YAML).
- For refresh: edit the per-subsystem details paragraph.

If R12 placement rules in [`structure_rules.md`](../skills/architecture_philosophy/structure_rules.md) need new entries, remind the user — do not edit `structure_rules.md` directly.

## Step 6: Report

Print a final diff summary (added / removed / renamed / refreshed) so the user can review before commit. Stage edits but do not auto-commit.

## What it does NOT do

- Does not enforce placement decisions — `/structure_audit` owns that.
- Does not auto-commit.
- Does not run on every session — Step 0 gates it.
