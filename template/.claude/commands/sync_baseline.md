---
allowed-tools: Bash(python3:*), Bash(git:*), Bash(git -C:*), Read, Edit, Write, Grep, Glob
description: Sync the universal harness config with the shared harness-baseline repo — check drift, upstream universal improvements, pull baseline updates.
---

## Scope

The universal portion of this project's `.claude/` harness is shared with other projects via the **harness-baseline repo** (recorded in `.claude/baseline.lock.json`). This command keeps the two in sync in both directions. The engine (`.claude/tools/baseline_sync.py`) is mechanical; **the judgment call — which changes are universal doctrine vs project-specific — is yours, per the Classification Rules below.**

Invocations: `/sync_baseline` (check), `/sync_baseline push`, `/sync_baseline pull`, `/sync_baseline fork <relpath>`, `/sync_baseline candidates`, `/sync_baseline audit` (maintainer-side separation health — run against a baseline checkout, not a consumer project).

## Context

- Lock summary: !`python3 .claude/tools/baseline_sync.py paths --status tracked | wc -l` tracked files (see `.claude/baseline.lock.json` for the full map)

## Classification Rules (the judgment core)

A change to a tracked file is **universal** (→ upstream it) when it would improve ANY project using this harness: tool-routing doctrine, workflow recipes, hook logic/thresholds, agent templates, generic checklists, bug fixes in scripts. It is **project-specific** (→ keep local) when it names this project's content domains, paths, subsystems, or design decisions.

- **Mixed change in one file:** materialize, then strip the project-specific hunks from the baseline copy before committing upstream. Never push project content upstream "for convenience".
- **Repeated project-specific edits to the same tracked file** = the file wants to fork: propose `/sync_baseline fork <relpath>` instead of fighting the drift report every session.
- **Placeholders:** baseline copies use `{{PROJECT_NAME}}`, `{{VAULT_ROOT}}`, `{{PROJECT_ROOT}}` (see lock `substitutions`). `materialize` reverse-substitutes automatically — verify the result didn't placeholder-ize a legitimate literal use of the project name.
- **`watch` files** (CLAUDE.md, settings.json, seed skills): never hash-synced. When a watch file changed, judge whether the change is shared doctrine (CLAUDE.md `BASELINE:core` region, hook wiring in settings.json) — if so, apply the equivalent edit to the baseline's `template/` copy by hand.

## Your task

**Default (check):**
1. Run `python3 .claude/tools/baseline_sync.py check` (add `--baseline-dir <path>` if a local baseline checkout exists; otherwise it clones to `.claude/.cache/baseline-repo`).
2. Report the actionable states. For each `local-modified` / `diverged` file, show `... diff <relpath>` output and classify per the rules above. Recommend the follow-up mode (`push`, `pull`, or `fork`) — don't execute it without confirmation unless the user already asked for a full sync.

**Push (upstream universal changes):**
1. Run `check`; collect `local-modified` + `diverged` files whose changes you classified universal (confirm classification with the user for anything borderline).
2. `python3 .claude/tools/baseline_sync.py materialize <relpath> ...` — writes reverse-substituted copies into the baseline clone's `template/`.
3. In the baseline clone: review the diff hunk-by-hunk, strip project-specific content, regenerate the manifest (`python3 tools/gen_manifest.py`), then commit (categorical messages) and push to the baseline's main branch.
4. Back in the project: `python3 .claude/tools/baseline_sync.py update-lock` so the lock records the new upstream hashes.
5. For `diverged` files, push only after reconciling with the upstream change (pull + re-apply, or manual merge in the baseline clone).

**Pull (adopt baseline updates):**
1. Run `check`; list `upstream-updated` files with one-line summaries of what changed (use `diff`).
2. `python3 .claude/tools/baseline_sync.py pull` (or with explicit relpaths to cherry-pick). `diverged` files: show the diff and merge by hand — never blind-overwrite local work.
3. Commit the pulled changes locally as `chore(harness): pull baseline updates`.

**Fork:**
1. `python3 .claude/tools/baseline_sync.py fork <relpath>` and note in the commit message WHY the file diverges — that rationale is the only record.

**Always — new-artifact sweep (also standalone as `candidates`):** run `python3 .claude/tools/baseline_sync.py candidates` (committed `.claude/` files unknown to the lock; state/caches/generated artifacts are pre-filtered). Judge each candidate ONCE:
- **Universal-shaped** (new hook/command/skill/rule with no project content) → add to the baseline: copy to the clone's `template/`, add it to the manifest layer map in `tools/gen_manifest.py` if pattern rules don't already cover it, regenerate the manifest, push, then `track` it in the lock.
- **Project-specific** → `python3 .claude/tools/baseline_sync.py ignore <relpath>` (records status `local`; the candidate never resurfaces).
The drift gate in `/clean_push`/`/commit_push` runs this same sweep whenever commits add `.claude/` files — empty `candidates` output means every committed artifact has been judged.

**Audit (maintainer-side separation health) — run against a baseline checkout (`harness-baseline/` in-repo, or a standalone clone):**

This is the maintainer counterpart to `check`: `check` finds *consumer* drift; `audit` proves the *baseline itself* stays project-agnostic. Run it before publishing and after any `push` that adds/moves template files.

1. **Mechanical pass.** From the baseline repo root, run `python3 tools/audit_baseline.py` (add `--strict` to also fail on WARN, `--json` for machine output). It gates on: source-project identifier leaks, secret/credential shapes, manifest⇄disk integrity, manifest staleness vs `gen_manifest.py`, and universal-tagged files that look substantively Jmodot-heavy. **Any ERROR is a publish blocker** — fix at the source (genericize, re-layer, or regenerate the manifest) and re-run to green.
2. **Judgment pass (what the script deliberately can't do).** The mechanical scan stays silent on game-domain nouns (`spell`/`critter`/`wizard`/`ingredient`) because they're too generic to flag without drowning in false positives — but a *universal*- or *godot*-layer file that bakes in the source game's content taxonomy, folder paths, or domain tables is still a leak. Sweep for it:
   - `grep -rInw "spell\|critter\|wizard\|potion\|ingredient\|synergy" template/.claude/commands template/.claude/rules template/.claude/skills` — for each hit, decide *illustrative-generic* (a one-off example teaching a concept — fine) vs *baked-in config* (keyword→category tables, hardcoded `Source/<Subsystem>/` paths, content-domain routing tables — genericize or move to a `watch`/adaptation-point file).
   - Cross-check the README's **Known adaptation points** list: every file that legitimately carries source-project shape *and is meant to be adapted per project* must be listed there. An adaptation-shaped file missing from that list is the finding.
3. Report ERROR/WARN/judgment findings tiered; fix mechanical blockers in-place, propose genericizations for judgment-level leaks, and only then proceed to `publish.sh` (which re-runs the mechanical gate as a backstop).
