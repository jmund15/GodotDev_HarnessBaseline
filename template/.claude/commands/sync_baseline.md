---
allowed-tools: Bash(python3:*), Bash(git:*), Bash(git -C:*), Bash(gh:*), Read, Edit, Write, Grep, Glob
description: Sync the universal harness config with the shared harness-baseline repo — check drift, upstream universal improvements, pull baseline updates.
---

## Scope

The universal portion of this project's `.claude/` harness is shared with other projects via the **harness-baseline repo** (recorded in `.claude/baseline.lock.json`). The engine (`.claude/tools/baseline_sync.py`) is mechanical and records every decision in the lock; **the judgment call — which changes are universal doctrine vs project-specific — is yours, per the Classification Rules below.**

## Arguments

`$ARGUMENTS` selects the mode:
- none: **Check**;
- `pull [<relpath>...]`: **Pull**;
- `publish`: **Publish**;
- `fork <relpath>`: **Fork**;
- `candidates`: **Candidates**;
- `upgrade`: **Upgrade**, for a consumer whose lock is still schema 1 (run from a baseline checkout with `--project <consumer>`);
- `audit`: **Audit**, run in a baseline checkout, not a consumer project.

Any other argument: print these forms and stop before the first engine call.

## Context

- Lock summary: !`python3 .claude/tools/baseline_sync.py paths --status tracked | wc -l` tracked files (see `.claude/baseline.lock.json` for the full map)

## Classification Rules (the judgment core)

A change to a tracked file is **universal** (→ upstream it) when it would improve ANY project using this harness: tool-routing doctrine, workflow recipes, hook logic/thresholds, agent templates, generic checklists, bug fixes in scripts. It is **project-specific** (→ keep local) when it names this project's content domains, paths, subsystems, or design decisions.

- **Mixed change in one file:** there is no mixed verdict. Fix it at the source first: move the project data behind a registry seam (`skills/project_subsystems/adaptation.json`) or split the file, then judge each side. Never push project content upstream "for convenience".
- **Repeated project-specific edits to the same tracked file** = the file wants to fork: propose `/sync_baseline fork <relpath>` instead of fighting the drift report every session.
- **Placeholders:** baseline copies use `{{PROJECT_NAME}}`, `{{VAULT_ROOT}}`, `{{PROJECT_ROOT}}` (see lock `substitutions`). `publish` reverse-substitutes automatically — verify the result didn't placeholder-ize a legitimate literal use of the project name. To add or remove a pair on an existing lock, run `baseline_sync.py sub --sub PLACEHOLDER=VALUE` or `sub --unset PLACEHOLDER`; `init --sub` runs only at init time, so the sole alternative on a populated lock is `init --force --sub`, which rebuilds every row.
- **Composed files:** `settings.json` is composed from `settings.base.json` (tracked) and `settings.project.json` (local); `reference/memory_domains.md` from `reference/memory_domains.base.md` and `adaptation.json`. Edit an input, then run `compose`. `CLAUDE.md` is project-owned and imports `CLAUDE.core.md`, then `CLAUDE.<layer>.md` for each adopted layer above pure (all tracked); shared doctrine goes in the lowest of those whose consumers need it.

**Verdicts** (`judge <relpath> --verdict ...`): `push`, `keep-local`, `fork`. A row you are unsure of gets `--borderline`; show it to the owner, who confirms with `judge <relpath> --verdict <v> --confirm`.

## Your task

**Check (default):**
1. `python3 .claude/tools/baseline_sync.py check --strict`. It reads the lock's pinned baseline commit. `--baseline-dir <checkout>` reads that checkout's `HEAD` instead, and its `origin` must equal the lock's `baseline_repo`.
2. `triage --batch 20 --json` lists the rows that need judgment: tracked rows whose content differs from both the upstream hash and the last judged sha, and forked rows never judged. Judge each per the rules above.
3. `candidates` lists committed `.claude/` files without a lock row. Handle them as in **Candidates**.
4. Report each actionable state with its follow-up:
   - `upstream-updated`, `new-upstream`: **Pull**. A `new-upstream` file this project deliberately does not carry is declined with `classify <relpath> --status local`: the row records the absence, and `gc` keeps it;
   - `offered` (a manifest `sync: offer` file, such as another project's memory; never taken by a bare pull, never a `--strict` finding): adopt one with `pull <relpath>` when it applies to this project, adding its MEMORY.md pointer in the same turn when it is a hot memory; decline it with `ignore <relpath>`; otherwise leave it. A `removed-upstream` memory whose file name reappears `offered` under `archive/` was demoted upstream: `forget` the old row, then `pull` the archive path;
   - judged `push`: **Publish**;
   - `forked-upstream-moved`, `forked-base-unknown`: read `diff <relpath>`, then keep the fork (`fork <relpath>` records the new base) or un-fork (`pull --force <relpath>`, then `track <relpath>`);
   - `composed-drift`: `compose`;
   - `removed-upstream`: `forget <relpath>` when the project agrees, else republish the file;
   - a row whose local file is gone: `gc` lists it; `gc --apply` drops `local` and `forked` rows, and `forget <relpath>` removes a `keep-tracked` row;
   - `unimported-layer-file`: add its `@CLAUDE.<layer>.md` line under `@CLAUDE.core.md` in `CLAUDE.md`.

   Done when `check --strict` exits 0 and `triage` and `candidates` print nothing. Recommend the follow-up mode; don't execute it without confirmation unless the user already asked for a full sync.

**Pull (adopt baseline updates):**
1. Run `check`; list `upstream-updated` and `new-upstream` rows with one-line summaries from `diff <relpath>`.
2. `pull <relpath>...` takes the chosen rows; bare `pull` takes every `upstream-updated` and `new-upstream` row and never an unrowed `sync: offer` path, which it counts as `offered`. A manifest `sync: seed` file is written only when absent and becomes a `local` row. A bare pull skips a new path whose local file already differs and lists it as `skipped (local file differs, no lock row)`; naming that path pulls it only with `--force`. `local-modified`, `forked` and `local` rows need `--force` and a clean path. Where both sides changed, merge by hand (base: the previous upstream blob, ours: `HEAD`, theirs: the upstream file), then `update-lock <relpath>`; never blind-overwrite local work.
3. When a composition input changed, run `compose`; `compose --check` must exit 0.
4. Commit the pulled files and the lock by pathspec as `chore(harness): pull baseline updates`.

**Publish (upstream universal changes):** every publication is one `publish` transaction: identity scan, manifest check, `audit_baseline.py --strict`, the template battery, then a PR that merges only after baseline CI passes.

- **Subject and proof travel together.** Publishing a row whose behavior an upstream proof asserts breaks that proof unless the proof publishes in the same transaction: the API it asserts arrives only with the publication. When the consumer's proof cannot publish (it names project content), the subject waits too, or the baseline copy is fixed in a companion PR that carries the subject.

1. Pick the source:
   - a universal change committed in this project: `judge <relpath>... --verdict push --commit <sha>` (reads that commit, not a peer's staged copy), then `paths --verdict push --judged-since <last complete journal's finished_at> --json > .claude/scratch/publish_rows.json` and `publish --from-commit <sha> --rows .claude/scratch/publish_rows.json --dry-run`;
   - planned universal work: `author start` prints a worktree on `author/<session8>`. Edit, run `python3 template/.claude/scripts/harness_tests.py --proofs-for <changed paths>` and commit there, then `publish --from-worktree <path> --dry-run`. The dry run's step 5 runs the battery in the mode below; never run the full battery by hand first.
2. Show the owner the dry-run journal (`.claude/.cache/baseline-publish/<id>.json`): collected rows, their verdicts, and step 1–5 evidence. An invocation that already said to publish skips this pause. A dry run red in steps 1–5 is fixed and redone with `publish --resume <id> --dry-run`, then shown again; plain `--resume` refuses it.
   - Step 5 evidence names its battery mode. `mode=scoped` ran only the proofs bound to the published rows; `mode=full` ran the whole template battery, because a row touches hooks, scripts, settings, a module the runner or publisher imports, a root file or a deleted proof, or because `--full-battery` was passed. `publish --resume <id> --full-battery` reruns step 5 in full mode on an unpublished journal.
   - Run `python3 .claude/tools/baseline_linux_preview.py <id>` once steps 1–5 are green, in the background while the owner reviews, when step 5 ran in full mode or any published row is executable (`.py` `.sh` `.ps1` `.js` `.mjs`). It runs the baseline CI's Ubuntu steps in WSL, where a Windows-green proof can still fail on POSIX paths or real symlinks. Treat a red step like a red dry run. A scoped docs-and-data publication skips it: CI's Ubuntu run is its Linux check.
3. `publish --resume <id>` pushes `publish/<id>`, opens the PR, waits for CI, merges, moves the lock pin and checks every published row.
   - An identity hit (project name, abbreviation, home path, topology token or content noun) stops the scrub step with a hit id. Fix the source; pass `--accept-hit <id>` only for a legitimate generic use.
   - A red CI run stops before the merge, leaves the PR open and records `ci_run_url`. Fix the source, commit, and run a fresh `publish`: a new source commit starts a new journal. Close the red PR with `gh pr close <number>`.
4. After a `--from-worktree` publication, `pull` the rows step 8 names.

   Done when every journal step is green and `check --strict` exits 0.

**Fork:**
1. `python3 .claude/tools/baseline_sync.py fork <relpath>` (records the upstream base) and note in the commit message WHY the file diverges — that rationale is the only record.

**Candidates (new-artifact sweep):** Judge each candidate ONCE:
- **Universal-shaped** (new hook/command/skill/rule with no project content) → `classify <relpath> --status tracked`, which scans the whole file for identity tokens, then **Publish**. A file the baseline does not have yet also takes `--layer pure|coding|godot`: the lowest layer whose consumers have every file it needs. Publish refuses a new row without one. `python3 .claude/tools/layer_closure.py --lock .claude/baseline.lock.json` proves the choice: every tracked file cites, imports and runs only files its layer ships; a finding is fixed at the source (move the sentence to the target's layer, reword it to the lowest-layer seam, re-layer a file, or load a higher-layer module through `hooks/_optional_hooks.py`).
- **Split from an existing file** → `classify <relpath> --status <s> --from <source relpath>`.
- **Project-specific** → `python3 .claude/tools/baseline_sync.py ignore <relpath>` (records status `local`; the candidate never resurfaces).

**Upgrade (schema-1 consumer):** its own engine predates `migrate`, so run the engine from a baseline checkout and name the project:
`python3 <checkout>/template/.claude/tools/baseline_sync.py upgrade --project <consumer> --baseline-dir <checkout> --layers <adopted prefix> [--abbrev <ABBR>]`. Omitting `--layers` adopts all three layers; `--abbrev` records the project's abbreviations for the publish identity scan.
1. It refuses a schema-2 lock, an uncommitted `CLAUDE.md`, `settings.json`, `settings.project.json` or lock, and a `project_subsystems` adaptation contract that fails. Only `upgrade` and `migrate` accept a v1 lock; every other write refuses it.
2. It migrates the lock, replaces `CLAUDE.md`'s `BASELINE:core` region with layer imports, and splits `settings.json` into the pulled `settings.base.json` plus a derived `settings.project.json`, listing each base entry the old file lacked. It then pulls, composes and prints `check --strict`. Other projects' `.claude/auto-memory/` files are manifest `sync: offer` rows: they stay unpulled and report `offered` (see **Check** step 4).
3. If it stops after migrating, finish from the consumer root with the checkout's engine: `pull --baseline-dir <checkout>`, then `compose`.
4. Commit the result, then run **Check** in the consumer: its triage clears the remaining judgment rows until `check --strict` exits 0.

Every other write refuses a v1 lock.

**Audit (maintainer-side separation health) — run against a baseline checkout:**

This is the maintainer counterpart to `check`: `check` finds *consumer* drift; `audit` proves the *baseline itself* stays project-agnostic. Run it by hand after moving or adding template files.

1. **Mechanical pass.** From the baseline repo root, run `python3 audit_baseline.py --strict` from its `tools/` directory (`--json` for machine output). It gates on identity tokens across the whole tree (hashed per consumer in the baseline root's `identity_digests.json`), secret and credential shapes, manifest⇄disk integrity, manifest staleness vs `gen_manifest.py`, layer tags, and layer closure (`template/.claude/tools/layer_closure.py` over the manifest). **Any ERROR blocks publication** — fix at the source (genericize, re-layer, or regenerate the manifest) and re-run to green.
2. **Judgment pass (what the script deliberately can't do).** The digests catch each consumer's listed content nouns, not a contributing game's taxonomy expressed in other words. A universal- or godot-layer file that bakes in content tables, folder paths or domain routing is still a leak. Sweep for it:
   - Build the noun list from the content domains of every project the baseline has absorbed code from (each contributor's `skills/game_vision` + `skills/project_subsystems` enumerate them), then `git grep -n -w -E "<noun1>|<noun2>|..." -- template/.claude/commands template/.claude/rules template/.claude/skills`. For each hit, decide *illustrative-generic* (a one-off example teaching a concept, fine to keep) or *baked-in config* (keyword→category tables, hardcoded `Source/<Subsystem>/` paths, content-domain routing tables). Move baked-in config to `adaptation.json` or a project-owned file.
   - Cross-check the README's **Known adaptation points** list: every file that legitimately carries source-project shape *and is meant to be adapted per project* must be listed there. An adaptation-shaped file missing from that list is the finding.
3. Report ERROR/WARN/judgment findings tiered; fix mechanical blockers in-place, propose genericizations for judgment-level leaks, and only then run **Publish**.
