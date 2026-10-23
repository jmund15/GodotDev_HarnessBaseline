---
allowed-tools: Bash(python3:*), Bash(gh:*), Read, Edit, Write, Grep, Glob
description: Check, pull, publish or fork shared harness configuration
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
- `doctor`: **Doctor**;
- `upgrade`: **Upgrade**, for a consumer whose lock is still schema 1 (run from a baseline checkout with `--project <consumer>`);
- `audit`: **Audit**, run in a baseline checkout, not a consumer project.

Any other argument: print these forms and stop before the first engine call.

## Context

- Lock summary: !`python3 .claude/tools/baseline_sync.py paths --status tracked | wc -l` tracked files (see `.claude/baseline.lock.json` for the full map)

## Classification Rules (the judgment core)

A change to a tracked file is **universal** (→ upstream it) when it would improve ANY project using this harness: tool-routing doctrine, workflow recipes, hook logic/thresholds, agent templates, generic checklists, bug fixes in scripts. It is **project-specific** (→ keep local) when it names this project's content domains, paths, subsystems, or design decisions.

- **A generic new artifact ships by default.** A new skill, command, workflow, hook or tool that names no project content is judged `push` at the lowest layer whose consumers have every file it needs. `keep-local` requires project content you can point to in the file. "Only this project uses it so far" or "it is new" is not a reason: an unshipped generic artifact leaves every other consumer without it. The one other exception is a standing context cost every consumer would pay, such as a listing entry or startup injection, sized against how many consumers would use it: ship it with its listing off (`skillOverrides` `user-invocable-only`) or keep it local, and name the measured bytes in the verdict's commit.
- **Mixed change in one file:** there is no mixed verdict. Fix it at the source first: move the project data behind a registry seam (`skills/project_subsystems/adaptation.json`) or split the file, then judge each side. Never push project content upstream "for convenience".
- **Repeated project-specific edits to the same tracked file** = the file wants to fork: propose `/sync_baseline fork <relpath>` instead of fighting the drift report every session.
- **Placeholders:** baseline copies use `{{PROJECT_NAME}}`, `{{VAULT_ROOT}}`, `{{PROJECT_ROOT}}` (see lock `substitutions`). `publish` reverse-substitutes automatically — verify the result didn't placeholder-ize a legitimate literal use of the project name. To add or remove a pair on an existing lock, run `baseline_sync.py sub --sub PLACEHOLDER=VALUE` or `sub --unset PLACEHOLDER`; `init --sub` runs only at init time, so the sole alternative on a populated lock is `init --force --sub`, which rebuilds every row.
- **Composed files:** `settings.json` is composed from `settings.base.json` (tracked), the `settings.<layer>.json` overlay of each adopted layer that ships one (tracked), and `settings.project.json` (local); `compose --template` renders the template's own composed outputs from its shipped inputs; `reference/memory_domains.md` from `reference/memory_domains.base.md` and `adaptation.json`. Edit an input, then run `compose`. `CLAUDE.md` is project-owned and imports `CLAUDE.core.md`, then `CLAUDE.<layer>.md` for each adopted layer above pure (all tracked); shared doctrine goes in the lowest of those whose consumers need it.

**Verdicts** (`judge <relpath> --verdict ...`): `push`, `keep-local`, `fork`. A `keep-local` verdict clears `check --strict` for the content it judged, so one verdict needs no fork. A row you are unsure of gets `--borderline`; show it to the owner, who confirms with `judge <relpath> --verdict <v> --confirm`. `push` runs the identity scan and refuses a hit; pass `--accept-hit <id>` only for a legitimate generic use. `push` also refuses a row the baseline lacks that has no layer; **Candidates** assigns one. `push` also refuses a row whose dependency is not in sync with the baseline and not judged `push` (a `forked` dependency, a `local` row the baseline ships, and a `local-modified` dependency the baseline copy of the row already cites pass), and names each; run `judge <dependency> --verdict push` first, or keep the row `keep-local` when the dependency never ships. `push` also refuses a tool row the baseline lacks when no shipped proof backs it; track the proof first, or judge it `push` in the same call. `keep-local` on a tracked row that never shipped sets it `local`. Every `judge`, `classify` and `ignore` prints the layer-closure findings its write adds; fix each before publish, which refuses them.

A delegated judging fan-out pins a fanout-tier row for every row's first verdict (a per-item rubric verdict, so cost leads). A stronger fanout-tier row re-reads only its `keep-local` and `--borderline` verdicts. The identity scan, layer closure and the dry run refuse a `push` that leaks an identity token, breaks a layer or fails a proof; a `push` of project-specific text with no identity token passes all three, and nothing re-reads it.

## Your task

**Check (default):**
1. `python3 .claude/tools/baseline_sync.py check --strict`. It fetches the lock's `baseline_ref` and reads `FETCH_HEAD`. `--baseline-dir <checkout>` reads that checkout's `HEAD` instead, and its `origin` must equal the lock's `baseline_repo`.
2. `triage --json` lists a batch of tracked rows whose content differs from both the lock's recorded `hash` and the last judged `sha`, plus forked rows never judged. Judge each per the rules above. Repeat until the array is empty. `--batch <n>` changes the batch size.
3. `candidates` lists index-tracked `.claude/` files without a lock row, excluding configured artifact classes and files outside the selected layer profile. Newly staged files can appear. Handle them as in **Candidates**.
4. Report each actionable state with its follow-up:
   - `upstream-updated`, `new-upstream`: **Pull**. A `new-upstream` file this project deliberately does not carry is declined with `classify <relpath> --status local`: the row records the absence, and `gc` keeps it;
   - `offered` (a manifest `sync: offer` file, such as another project's memory; never taken by a bare pull, never a `--strict` finding): adopt one with `pull <relpath>` when it applies to this project, adding its MEMORY.md pointer in the same turn when it is a hot memory; decline it with `ignore <relpath>`; otherwise leave it. A `removed-upstream` memory whose file name reappears `offered` under `archive/` was demoted upstream: `forget` the old row, then `pull` the archive path;
   - judged `push`: **Publish**;
   - `forked-upstream-moved`, `forked-base-unknown`: read `diff <relpath>`, then keep the fork (`fork <relpath>` records the new base) or un-fork (`pull --force <relpath>`, then `track <relpath>`);
   - `composed-drift`: `compose`;
   - `removed-upstream`: a tracked row is retired by `pull` (an unmodified file is deleted with its row, a modified one is kept as `local`); for a forked row, `forget <relpath>` when the project agrees, else republish the file;
   - `unpublished`: a tracked row the baseline never shipped; `pull` leaves it, and **Publish** ships it once judged `push`;
   - a row whose local file is gone: `gc` lists it; `gc --apply` drops `local` and `forked` rows, and `forget <relpath>` removes a `keep-tracked` row;
   - `unimported-layer-file`: add its `@CLAUDE.<layer>.md` line under `@CLAUDE.core.md` in `CLAUDE.md`;
   - `layer-closure`: fix each finding at its source by the options in **Candidates** *Universal-shaped*, before any **Publish**.
   - `unproven: <tool>`: a shipped tool row has no shipped proof. Add a proof named `test_<tool stem>*`, judge it `push`, and **Publish** it.

   Done when `check --strict` exits 0 and the `triage` and `candidates` result sets are empty. Empty listings may still print status text. Recommend the follow-up mode; don't execute it without confirmation unless the user already asked for a full sync.

**Doctor (what adoption left undone):** `python3 .claude/tools/baseline_sync.py doctor [--json]` prints one `[ok|pending|fail]` row per adoption item, with its fix. A `pending` row is owner content (seed files to fill in); any `fail` exits 1. Apply each fix and rerun until it exits 0. `init` and `pull` render the lane agents as `local` rows and write the root `.gitignore` harness block.

**Pull (adopt baseline updates):**
1. Run `check`; list `upstream-updated` and `new-upstream` rows with one-line summaries from `diff <relpath>`.
2. `pull <relpath>...` takes the chosen rows; bare `pull` takes every `upstream-updated` and `new-upstream` row and never an unrowed `sync: offer` path, which it counts as `offered`. A manifest `sync: seed` file is written only when absent and becomes a `local` row. A bare pull skips a new path whose local file already differs and lists it as `skipped (local file differs, no lock row)`; naming that path pulls it only with `--force`. `local-modified`, `forked` and `local` rows need `--force` and a clean path.
   - **Diverged rows:** where both sides changed (`diverged`), `pull --merge` 3-way merges each committed row over the upstream blob its `hash` records. A conflict leaves that row and its lock entry untouched, prints `conflict: <relpath> (<n> hunks) -> <marker copy>` and exits 1; resolve the markers in that copy, then `resolve <relpath>` installs it and records the upstream base. A `conflict:` line with no copy leaves the row untouched too: commit the local edits and rerun `pull --merge <relpath>`, or when no upstream blob matches the row's `hash`, merge by hand (base: the previous upstream blob, ours: `HEAD`, theirs: the upstream file) and run `update-lock <relpath>`; a binary row never merges, so keep the local copy or take upstream with `pull --force <relpath>`. Never blind-overwrite local work.
3. When a composition input changed, run `compose`; `compose --check` must exit 0.
4. `git add -f` each path `pull` printed as `ignored: <path> (add with git add -f)`, then commit the pulled files and the lock by pathspec as `chore(harness): pull baseline updates`.

**Publish (upstream universal changes):** every publication is one `publish` transaction: identity scan, manifest check, `audit_baseline.py --strict`, the template battery, then a PR. The PR merges after baseline CI passes unless the transaction used `--no-ci`, permitted only when the pinned baseline has no workflow. Report that exception as CI skipped, never passed.

- **Subject and proof travel together.** Publishing a row whose behavior an upstream proof asserts breaks that proof unless the proof publishes in the same transaction: the API it asserts arrives only with the publication. When the consumer's proof cannot publish (it names project content), the subject waits too, or the baseline copy is fixed in a companion PR that carries the subject.

1. Pick the source:
   - a universal change committed in this project: commit it, then `judge <rows you changed> --carry <base>..<head>` (`<base>` is the commit before yours). It carries each named row's push verdict along the range, applies the identity, layer and dependency refusals from **Verdicts**, and prints `carried:` or `not carried: <row> (<reason>)`; judge a `not carried` row with `--verdict push --commit <sha>` (reads that commit, not a peer's staged copy). Rows you do not name keep their verdicts. Then `paths --verdict push --judged-since <last complete journal's finished_at> --json > .claude/scratch/publish_rows.json` and `publish --from-commit <sha> --rows .claude/scratch/publish_rows.json --dry-run`;
   - planned universal work: `author start` prints a worktree on `author/<session8>`. Edit, run `python3 template/.claude/scripts/harness_tests.py --proofs-for <changed paths>` and commit there, then `publish --from-worktree <path> --dry-run`. The dry run's step 5 runs every check and the battery in the mode below; never run the full battery by hand first.
2. Show the owner the dry-run journal (`.claude/.cache/baseline-publish/<id>.json`): collected rows, their verdicts, and step 1–5 evidence. A red step 5 lists every red it found (manifest, audit, baseline tests, battery); fix them all before the redo. An invocation that already said to publish skips this pause. A dry run red in steps 1–5 is fixed and redone with `publish --resume <id> --dry-run` only while the source commit is unchanged. A changed source commit needs a fresh `publish`. Show the repaired dry run again; plain `--resume` refuses a red dry run.
   - Step 5 evidence names its battery mode. `mode=scoped` ran only the proofs bound to the published rows; `mode=full` ran the whole template battery, because a row touches hooks, scripts, settings, a module the runner or publisher imports, a root file other than a `.github/workflows/` file, or a deleted proof, or because `--full-battery` was passed. On an unpublished journal, adding `--full-battery` to `publish --resume <id>` resets step 5 and subsequent steps only when upgrading from scoped to full. An already-full resume retains green steps.
   - A Linux red surfaces as a red PR check; `baseline_linux_preview.py --worktree <path>` reruns the CI lanes locally.
3. `publish --resume <id>` pushes `publish/<id>`, opens the PR, applies the journal's CI policy from **Publish**, merges, moves the lock pin and checks every published row.
   - An identity hit (project name, abbreviation, home path, topology token or content noun) stops the scrub step with a hit id. Fix the source, or accept the hit as **Verdicts** describes.
   - A red CI run stops before the merge, leaves the PR open and records `ci_run_url`. Fix the source, commit, and run a fresh `publish`: a new source commit starts a new journal. Close the red PR with `gh pr close <number>`.
4. After a `--from-worktree` publication, `pull` the rows step 8 names.

   Done when every journal step is green and `check --strict` exits 0.

**Fork:**
1. `python3 .claude/tools/baseline_sync.py fork <relpath>` (records the upstream base) and note in the commit message WHY the file diverges — that rationale is the only record.

**Candidates (new-artifact sweep):** Judge each candidate ONCE:
- **Universal-shaped** (new hook/command/skill/rule with no project content) → `classify <relpath> --status tracked`, which scans the whole file for identity tokens, then **Publish**. A file the baseline does not have yet also takes `--layer pure|coding|godot`: the lowest layer whose consumers have every file it needs. Publish refuses a new row without one. `python3 .claude/tools/layer_closure.py --lock .claude/baseline.lock.json` scans supported static citations, imports and script paths for dependencies outside the file's shipped layer. Unreadable source files, Python parse failures and unsupported dynamic references remain unchecked. Read each of them by hand before accepting the layer choice. Fix a finding at the source (classify a universal-shaped target tracked at the citer's layer and publish both in one transaction, move the sentence to the target's layer, reword it to the lowest-layer seam, re-layer a file with `classify <relpath> --layer <l> --relayer`, or load a higher-layer module through `hooks/_optional_hooks.py`).
- **Split from an existing file** → `classify <relpath> --status <s> --from <source relpath>`.
- **Project-specific** → `python3 .claude/tools/baseline_sync.py ignore <relpath>` (records status `local`; the candidate never resurfaces).

**Upgrade (schema-1 consumer):** its own engine predates `migrate`, so run the engine from a baseline checkout and name the project:
`python3 <checkout>/template/.claude/tools/baseline_sync.py upgrade --project <consumer> --baseline-dir <checkout> --layers <adopted prefix> [--abbrev <ABBR>]`. Omitting `--layers` adopts all three layers; `--abbrev` records the project's abbreviations for the publish identity scan.
1. It refuses a schema-2 lock, an uncommitted `CLAUDE.md`, `settings.json`, `settings.project.json` or lock, and a `project_subsystems` adaptation contract that fails. Only `upgrade` and `migrate` accept a v1 lock; every other write refuses it.
2. It migrates the lock and replaces `CLAUDE.md`'s `BASELINE:core` region with layer imports. When no `settings.project.json` exists, it splits `settings.json` into the pulled `settings.base.json` and adopted layers' overlays plus a derived `settings.project.json` holding what those shared inputs do not provide, listing each shared entry the old file lacked. An existing project input leaves an unsplit `settings.json` row for triage unless it is already `composed`. Inspect the migration's split result. It then pulls, composes and prints `check --strict`. Other projects' `.claude/auto-memory/` files are manifest `sync: offer` rows: they stay unpulled and report `offered` (see **Check** step 4).
3. If it stops after migrating, finish from the consumer root with the checkout's engine: `pull --baseline-dir <checkout>`, then `compose`.
4. Commit the result, then run **Check** in the consumer: its triage clears the remaining judgment rows until `check --strict` exits 0.

**Audit (maintainer-side separation health) — run against a baseline checkout:**

This is the maintainer counterpart to `check`: `check` finds *consumer* drift; `audit` proves the *baseline itself* stays project-agnostic. Run it by hand after moving or adding template files.

1. **Mechanical pass.** From the baseline repo root, run `python3 audit_baseline.py --strict` from its `tools/` directory (`--json` for machine output). It gates on identity tokens across the whole tree (hashed per consumer in `identity_digests.json` in the baseline repository's `tools/` directory), secret and credential shapes, manifest⇄disk integrity, manifest staleness vs `gen_manifest.py`, layer tags, and layer closure (`template/.claude/tools/layer_closure.py` over the manifest). **Any ERROR blocks publication** — fix at the source (genericize, re-layer, or regenerate the manifest) and re-run to green.
2. **Judgment pass (what the script deliberately can't do).** The digests catch each consumer's listed content nouns, not a contributing game's taxonomy expressed in other words. A universal- or godot-layer file that bakes in content tables, folder paths or domain routing is still a leak. Sweep for it:
   - Build the noun list from the content domains of every project the baseline has absorbed code from (each contributor's `skills/game_vision` + `skills/project_subsystems` enumerate them), then `git grep -n -w -E "<noun1>|<noun2>|..." -- template/.claude/commands template/.claude/rules template/.claude/skills`. For each hit, decide *illustrative-generic* (a one-off example teaching a concept, fine to keep) or *baked-in config* (keyword→category tables, hardcoded `Source/<Subsystem>/` paths, content-domain routing tables). Move baked-in config to `adaptation.json` or a project-owned file.
   - Cross-check the README's **Known adaptation points** list: every file that legitimately carries source-project shape *and is meant to be adapted per project* must be listed there. An adaptation-shaped file missing from that list is the finding.
3. Report ERROR/WARN/judgment findings tiered; fix mechanical blockers in-place, propose genericizations for judgment-level leaks, and only then run **Publish**.
