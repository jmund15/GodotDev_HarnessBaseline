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
- `audit`: **Audit**, run in a baseline checkout, not a consumer project;
- `full`: **Check**, **Pull**, judging, **Candidates** and **Publish**, unpaused, to Check's done-condition.

Text after a form is owner notes: act on them too. Any other argument: print these forms and stop before the first engine call.

## Context

- Lock summary: !`python3 .claude/tools/baseline_sync.py paths --status tracked | wc -l` tracked files

## Classification Rules (the judgment core)

A change to a tracked file is **universal** (→ upstream it) when it would improve ANY project using this harness: tool-routing doctrine, workflow recipes, hook logic/thresholds, agent templates, generic checklists, bug fixes in scripts. It is **project-specific** (→ keep local) when it names this project's content domains, paths, subsystems, or design decisions.

- **A generic new artifact ships by default.** A new skill, command, workflow, hook or tool that names no project content is judged `push` at the lowest layer whose consumers have every file it needs. `keep-local` requires project content you can point to in the file. "Only this project uses it so far" or "it is new" is not a reason: an unshipped generic artifact leaves every other consumer without it. A proof whose every subject ships, and which plants its own fixtures, ships with its subject. The one other exception is a standing context cost every consumer would pay, such as a listing entry or startup injection, sized against how many consumers would use it: ship it with its listing off (`skillOverrides` `user-invocable-only`) or keep it local, and name the measured bytes in the verdict's commit.
- **Mixed change in one file:** there is no mixed verdict. Fix it at the source first: move the project data behind a registry seam (`skills/project_subsystems/adaptation.json`) or split the file, then judge each side. Never push project content upstream "for convenience".
- **Repeated project-specific edits to the same tracked file** = the file wants to fork: propose `/sync_baseline fork <relpath>` instead of fighting the drift report every session.
- **Placeholders:** baseline copies use `{{PROJECT_NAME}}`, `{{VAULT_ROOT}}`, `{{PROJECT_ROOT}}` (see lock `substitutions`). `publish` reverse-substitutes automatically — verify the result didn't placeholder-ize a legitimate literal use of the project name. To add or remove a pair on an existing lock, run `baseline_sync.py sub --sub PLACEHOLDER=VALUE` or `sub --unset PLACEHOLDER`; `init --sub` runs only at init time, so the sole alternative on a populated lock is `init --force --sub`, which rebuilds every row.
- **Composed files:** `settings.json` is composed from `settings.base.json` (tracked), the `settings.<layer>.json` overlay of each adopted layer that ships one (tracked), and `settings.project.json` (local); `compose --template` renders the template's own composed outputs from its shipped inputs; `reference/memory_domains.md` from `reference/memory_domains.base.md` and `adaptation.json`. Edit an input, then run `compose`. `CLAUDE.md` is project-owned and imports `CLAUDE.core.md`, then `CLAUDE.<layer>.md` for each adopted layer above pure (all tracked); shared doctrine goes in the lowest of those whose consumers need it.

Before judging, read [Verdicts](reference/sync_baseline_publish.md#verdicts).

## Your task

**Check (default):**
1. `python3 .claude/tools/baseline_sync.py check --strict`. It fetches the lock's `baseline_ref` and reads `FETCH_HEAD`. `--baseline-dir <checkout>` reads that checkout's `HEAD` instead, and its `origin` must equal the lock's `baseline_repo`.
2. `triage --json` lists a batch (`--batch <n>`) of tracked rows changed since both the upstream copy at their `base` (else the lock's `synced_commit`) and the last judged `sha`, forked rows never judged or changed since their verdict, and `local` harness-source rows never judged or changed since their `keep-local` verdict (`.jsonl` data logs, memories and seeds excepted). A row's `identity` lists every hit `push` refuses; a project-name literal in the diff that `identity` omits is a publish placeholder, not a leak. Judge each per the rules above until the array is empty.
3. `candidates` lists index-tracked `.claude/` files without a lock row (a `tests/test_*` file only when a shipped tool it tests exists), excluding configured artifact classes and files outside the selected layer profile. Newly staged files can appear. Handle them as in **Candidates**.
4. Report each actionable state with its follow-up:
   - `upstream-updated`, `new-upstream`, `diverged`: **Pull**. A `new-upstream` file this project deliberately does not carry is declined with `classify <relpath> --status local`: the row records the absence, and `gc` keeps it;
   - `offered` (a manifest `sync: offer` file, such as another project's memory; never taken by a bare pull, never a `--strict` finding): adopt one with `pull <relpath>` when it applies to this project, adding its MEMORY.md pointer in the same turn when it is a hot memory; decline it with `ignore <relpath>`; otherwise leave it. A `removed-upstream` memory whose file name reappears `offered` under `archive/` was demoted upstream: `forget` the old row, then `pull` the archive path;
   - judged `push`: **Publish**;
   - `forked-upstream-moved`: a bare `pull` combines upstream's change with the fork and keeps it forked on the new base (a conflict is handled as for `diverged`); `fork <relpath>` records the new base without taking upstream's change; `pull --force <relpath>` then `track <relpath>` un-forks;
   - `forked-base-unknown`: read `diff <relpath>`, then keep the fork (`fork <relpath>` records the new base) or un-fork (`pull --force <relpath>`, then `track <relpath>`);
   - `composed-drift`: `compose`;
   - `removed-upstream`: a tracked row is retired by `pull` (an unmodified file is deleted with its row, a modified one is kept as `local`); for a forked row, `forget <relpath>` when the project agrees, else republish the file;
   - `unpublished`: a tracked row the baseline never shipped; `pull` leaves it, and **Publish** ships it once judged `push`;
   - `base-unknown`: no baseline commit's rendered copy matches the row's legacy `hash` (or it recorded none), so its reconcile point is unknown and no drift can be computed. Re-judge it: `pull <relpath>` takes upstream, `judge <relpath> --verdict keep-local` keeps the local copy; either settles the row at the pin;
   - `local-shadows-upstream`: a `local` row for a file the baseline ships, so no upstream change ever reaches it. A copy that should follow upstream takes `pull --force <relpath>`, a commit, then `classify <relpath> --status tracked --force` (its identity scan reads the committed copy); a deliberate divergence takes `fork <relpath>`, which records its base so upstream moves surface;
   - a row whose local file is gone: `gc` lists it; `gc --apply` drops `local` and `forked` rows, and `forget <relpath>` removes a `keep-tracked` row;
   - `unimported-layer-file`: add its `@CLAUDE.<layer>.md` line under `@CLAUDE.core.md` in `CLAUDE.md`;
   - `layer-closure`: fix each finding at its source by the options in **Candidates** *Universal-shaped*, before any **Publish**.
   - `unproven: <tool>`: a shipped tool row has no shipped proof. Add a proof named `test_<tool stem>*`, judge it `push`, and **Publish** it.

   Done when `check --strict` exits 0 and the `triage` and `candidates` result sets are empty. Empty listings may still print status text. Recommend the follow-up mode; don't execute it without confirmation unless the run is `full`.

**Doctor (what adoption left undone):** `python3 .claude/tools/baseline_sync.py doctor [--json]` prints one `[ok|pending|fail]` row per adoption item, with its fix. A `pending` row is owner content (seed files to fill in); any `fail` exits 1. Apply each fix and rerun until it exits 0. `init` and `pull` render the lane agents as `local` rows and write the root `.gitignore` harness block.

**Pull (adopt baseline updates):**
1. Run `check`; list `upstream-updated` and `new-upstream` rows with one-line summaries from `diff <relpath>`.
2. `pull <relpath>...` takes the chosen rows; bare `pull` takes every `upstream-updated` and `new-upstream` row, never an unrowed `sync: offer` path (it counts those `offered`). A `sync: seed` file is written only when absent, as a `local` row. A bare pull skips a new path whose local file already differs and lists it as `skipped (local file differs, no lock row)`; naming that path pulls it only with `--force`. `local-modified`, `forked` and `local` rows need `--force` and a clean path. A bare `update-lock` refuses: name the rows it takes.
   - **A bare pull settles every row before it writes.** A row changed on both sides merges over its recorded `base` (or the lock's `synced_commit`). A merged fork stays `forked` with its base moved to the upstream it merged.
   - If any row cannot settle, the pull exits 1 and writes no live file. For a hunk conflict, resolve the copy named on the `unsettled:` line under `.claude/scratch/baseline_merge/`, then rerun bare `pull`. The rerun uses the resolution while the local and rendered upstream text are unchanged. Commit or discard dirty edits before retrying. A binary conflict needs a decision: `fork <relpath>` keeps local, or `pull --force <relpath>` takes upstream. A named pull is the explicit partial choice: it never merges, and a `diverged` row needs `--force`. Never blind-overwrite local work.
3. Run `compose --check` after every pull; it must exit 0. A `drift:` line means a composition input changed: run `compose`. A `retired:` line names a key in your `adaptation.json` that a shared reader no longer reads and what replaces it: edit the file as the line says.
4. After all rows land, `pull` runs `harness_tests.py --for <paths it wrote or retired>` and scans the remaining local hooks/tools/scripts Python callers for names missing from pulled modules. A red proof exits 1 and leaves the full delivered set in place. `check --strict`, the next settled pull and the close name it until a proof passes. It prints `proofs: green`, or `red proof(s) after pull (exit <code>): <names>` with a rerun command. Fix each red proof or `fork` its row, then rerun that command. A `local reference(s) to an attribute a pulled module no longer defines` line also exits 1 and leaves the caller unchanged. Fix the call or `fork` the module.
5. `git add -f` each path `pull` printed as `ignored: <path> (add with git add -f)`, then commit the pulled files and the lock by pathspec as `chore(harness): pull baseline updates`.

**Publish (upstream universal changes):** follow [Publish](reference/sync_baseline_publish.md#publish).

**Fork:**
1. `python3 .claude/tools/baseline_sync.py fork <relpath>` (records the upstream base) and note in the commit message WHY the file diverges — that rationale is the only record.

**Candidates (new-artifact sweep):** Judge each candidate ONCE:
- **Universal-shaped** (new hook/command/skill/rule with no project content) → `classify <relpath> --status tracked`, which scans the whole file for identity tokens, then **Publish**. A file the baseline does not have yet also takes `--layer pure|coding|godot`: the lowest layer whose consumers have every file it needs. Publish refuses a new row without one. `python3 .claude/tools/layer_closure.py --lock .claude/baseline.lock.json` scans supported static citations, imports and script paths for dependencies outside the file's shipped layer. Unreadable source files, Python parse failures and unsupported dynamic references remain unchecked. Read each of them by hand before accepting the layer choice. Fix a finding at the source (classify a universal-shaped target tracked at the citer's layer and publish both in one transaction, move the sentence to the target's layer, reword it to the lowest-layer seam, re-layer a file with `classify <relpath> --layer <l> --relayer`, or load a higher-layer module through `hooks/_optional_hooks.py`).
- **Split from an existing file** → `classify <relpath> --status <s> --from <source relpath>`.
- **Project-specific** → `python3 .claude/tools/baseline_sync.py ignore <relpath>` (records status `local`; the candidate never resurfaces).

**Upgrade** and **Audit:** read [`reference/sync_baseline_maintainer.md`](reference/sync_baseline_maintainer.md) and run the form from it.
