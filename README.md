# Harness Baseline — layered Claude Code harness

The shared, project-agnostic harness core extracted from a game-development
harness as a starting point for any Claude Code project — game dev (Godot 4.x +
C#, Jmodot builds included), content production, or other — and as the **single
upstream** that keeps that shared core in sync as it evolves across projects.

## What's in it

`template/.claude/` mirrors a consumer project's `.claude/` directory: 555 files
in three archetypes (see `baseline.manifest.json` for the per-file map; 6 of these
are `sync: seed` — 4 counted in `pure`, 2 in `godot`):

| Layer | Files | Contents |
|---|---|---|
| `pure` | 295 | fully domain-agnostic; serves any Claude Code project including non-code content production: session lifecycle (`/session_end`, `/self_evaluate`, `/autolearn`, `/codify`, eval dashboard), doc system (`/doc_*`), worklog system (`/worklog` + relevance workflow), memory system + curated process/discipline auto-memory seed (hot + `archive/`), agent templates, review/explore/idea fan-out workflows, orchestration + delegation doctrine (`orchestration` skill, `rules/model_delegation.md`, `reference/model_ladder_evidence.md`, sidecar launchers for external models), instruction-quality tooling, harness proof runner (`scripts/harness_tests.py` + `tests/`), slimmed git commands (`/commit_push`, `/clean_push`, `/create_pr`), the `/sync_baseline` machinery itself |
| `coding` | 96 | any programming project, not content production: plan/roadmap pipeline (`/plan_part` → `/part_drive` → `/plan_check` → `/part_execute`), `/explore`, brainstorm redteam, heavy PR machinery (`/merge_pr`, `/pr_ready`, `/review_pr(s)`), tool-routing hook family, TDD/debugging/architecture skills, code-hygiene auto-memory |
| `godot` | 164 | Testing skill (GdUnit4 + ISceneRunner), `/regression_gate` + `verify.ps1`, Godot log analysis, `.tres`/`[Tool]` safety guards (format, script-strip, null-strip, uid-cache audit), test-double / RefCounted-free / gate-coverage guards, C# LSP rules + adapter, scene/physics/C#/HSM-BT pattern rules, cloud bootstrap (`cloud-install.sh`, session context loader), Godot-specific memory gotchas, the Jmodot framework skill + subsystem docs, status-effect/entity/sprite/shader/VFX authoring skills, logging methodology (JmoLogger), submodule procedure, and `/workstation_setup` |

A consumer subscribes to a prefix of `pure` → `coding` → `godot`.

**Seed files** (`sync: seed` in the manifest) are copied once at bootstrap and then
project-owned: `CLAUDE.md` (PROJECT section + `BASELINE:core` region), `settings.json`,
`game_vision` + `project_subsystems` skill skeletons, `known_failure_modes` catalog,
`worklog-titles.md`.

**Deliberately excluded** (stays per-project): game-content skills/commands
(ability/entity authoring, content audits), project subsystem registries, game-design
docs, project memory (beyond the curated pure/coding/godot seed), benchmark corpora and
campaign tooling, and all session state (`self_evaluate_archive.json`, plans, scratch,
logs, caches).

## Placeholders

Template files use four substitution variables, applied by `bootstrap.sh`:

- `{{PROJECT_NAME}}` — Godot project name (also used for `app_userdata` log paths and the Obsidian `DevProjects/<name>` folder)
- `{{PROJECT_NAMESPACE}}` — C# namespace / library prefix (defaults to the project name)
- `{{VAULT_ROOT}}` — absolute path to the Obsidian vault root
- `{{PROJECT_ROOT}}` — absolute path to the project repo on your dev machine

## Adopting into a new project

```bash
git clone <this-repo> harness-baseline
cd harness-baseline
./bootstrap.sh --target /path/to/NewGame --project-name NewGame \
    --vault-root "C:/Users/you/Documents/ObsidianVault" \
    --project-root "C:/path/to/NewGame"          # --layers pure,coding,godot to choose your prefix (default: all)
```

A content-production repo bootstraps with `--layers pure`.

Bootstrap copies the template, substitutes placeholders, and writes
`.claude/baseline.lock.json` (per-file hashes + your substitution map) so the sync
loop works from the first session. Then follow the printed next-steps checklist
(fill the CLAUDE.md PROJECT section, seed `game_vision` / `project_subsystems`,
create the vault `Claude/TODO/` folders, `/system_check`, `/reindex_search`), and
walk the **Known adaptation points** below.

## Keeping projects and baseline in sync

The contract: **the baseline never changes for project-specific edits; every
universal improvement flows back here, and every arrival at `main` goes through
one gated transaction.**

A new `.claude/` file is classified — `tracked`, `local`, `forked` or `composed`
— before its first commit, not judged later on drift. There are no `watch` rows:
every file the baseline tracks carries a decision tied to its content hash from
the moment it exists.

Mechanism (per consumer project):

- `.claude/baseline.lock.json` (schema 2) — the decision ledger. Each row's
  `status` (`tracked` / `local` / `forked` / `composed`), `judged` verdict
  (`push` / `keep-local` / `fork`) and content sha are set at classification time
  and re-evaluated only when the row's content actually changes.
- `.claude/tools/baseline_sync.py` — the mechanical engine:
  - `check` / `diff` / `pull` — read-side classification and sync against the
    pinned upstream commit;
  - `classify` / `judge` / `triage` — the decision ledger's write side: `triage`
    surfaces rows that need a verdict, `judge` records it;
  - `author start` — creates or reuses this session's baseline author worktree
    (`.claude/.cache/baseline-worktrees/<session>`) for universal work that is
    authored baseline-first, then pulled;
  - `publish` — the eight-step publication transaction (below);
  - `fork` / `track` / `ignore` / `forget` / `gc` / `migrate` / `paths` / `candidates`
    round out row lifecycle and lock maintenance.
- `/sync_baseline` — the judgment wrapper around the engine: runs `check`,
  surfaces `triage` batches for the model or owner to `judge`, and drives
  `publish` once verdicts are recorded.
- **The classification guard** (`hooks/baseline_classification_guard.py`) denies
  a `git commit` that adds an unclassified `.claude/` file, naming the `classify`
  command that unblocks it — the enforcement point moved from a drift check after
  the fact to the commit itself.

### Publication: one gated transaction

`baseline_sync.py publish (--from-commit <sha> | --from-worktree <path>) [--rows FILE] [--dry-run] [--no-ci]`
runs eight steps — `collect → classify → materialize → scrub → validate → publish
→ update-lock → check` — against one journal
(`.claude/.cache/baseline-publish/<id>.json`) written atomically before each step
starts, so any failure is resumable with `--resume <id>` and any failure before
step 6's merge leaves `main` untouched. `--dry-run` stops after step 5 (validate)
for owner review; `--resume` continues it. Step 5 runs the manifest, separation
and battery gates from *Maintaining this repo* below, plus the whole-tree
identity scan, before anything reaches a pull request.

- Publishing a consumer's own committed universal work: `--from-commit <sha>`,
  driven by rows whose lock verdict is `push`.
- Publishing baseline work authored directly in an `author start` worktree:
  `--from-worktree <path>` — this publishes that worktree's `HEAD` tree only,
  never its commit history, so a token that only ever existed in an earlier,
  since-amended commit never reaches the published tree.
- A repeated publish for the same source commit and row set is a no-op
  (`already published <id>`) once merged, or a named `--resume <id>` refusal
  while still in flight; a fresh publish on top of a moved baseline records
  `supersedes: <id>`.

Typical lifecycles:

- *Improved a hook / command / skill while working on game A* → classify at
  creation → commit → `judge --verdict push` → `publish --from-commit <sha>` →
  baseline updated → in game B, `pull` (run it occasionally, or when starting
  significant work).
- *Project-specific tweak to a tracked file* → `triage` surfaces it →
  `judge --verdict keep-local`, or `fork` if the divergence is permanent.
- *Universal work authored baseline-first* → `author start` → edit and commit in
  that worktree → `publish --from-worktree <path>` → `pull` in the consumer.
- *Hot memory demoted to `archive/` in a project* → the template mirrors the move
  (delete the hot copy, add the archive copy); `forget` the old row, `classify`
  the new one.

## Maintaining this repo

- `python3 tools/gen_manifest.py` after any add/remove/move under `template/` —
  the manifest drives bootstrap layer-filtering and consumer lock generation.
  Layer/seed assignment is pattern-based at the top of that script; extend the
  pattern lists when adding files of a new kind. **Layer assignment has NO
  fallback**; `gen_manifest.py` fails loudly listing any unclassified file, so
  every new file must be added to exactly one layer pattern list.
- `python3 tools/audit_baseline.py` (also `/sync_baseline audit`) — the separation
  gate. Verifies no source-project identifiers / secrets / concrete machine paths
  leak into `template/`, the manifest matches disk and the generator, and flags (INFO)
  a `pure` file naming >=4 godot/coding markers, or a `coding` file naming >=4 godot
  markers (layer-gate check). It also warns on pure-tagged files naming code/engine or
  consumer-domain nouns (core-domain-noun check). ERROR exit blocks publish; run it
  after any template change. Its judgment pass (in the `/sync_baseline audit`
  command) covers what the script can't: game-domain-noun leaks and the
  adaptation-points list below. The standing layer-gate INFO rows (`/explore`,
  `/merge_pr`, `/plan_check`, `/test_compact`, review agents, code-quality checklist,
  `debugging` and `parallel_agents` skills) are confirmed engine-agnostic tools whose
  bodies use Godot examples — they stay in their layer.
- Commit messages follow the same categorical convention as consumer projects
  (`feat`/`fix`/`refactor`/`chore`).
- Model/tooling evolution (new models, new plugin capabilities, superior
  workflows) lands here exactly like any universal improvement: change it in
  whichever project discovered it, upstream via `/sync_baseline push`, and other
  projects adopt via `pull`.

## Publishing

This repo is published; consumer locks' `baseline_repo` point at its remote and
`bootstrap.sh` records the clone's `origin` automatically. `publish.sh` remains for
lifting a fresh copy into a new empty remote (it re-runs the separation audit as a
backstop).

## Known adaptation points

A few included files are generic in shape but carry conventions as concrete
defaults — review them on first use in a new project. (`tools/audit_baseline.py`
keeps this list honest: its judgment pass flags adaptation-shaped files missing from here.)

- `reference/memory_domains.md` — the domain → search-seed → companion-skill table.
  `hooks/plan_memory_reminder.py` mirrors it as its `PROJECT-CONFIG` `DOMAINS` table;
  replace both with your project's content domains and keep the two in sync.
- `commands/doc_start_here_update.md` — the domain-classification table's first row
  (`PROJECT-CONFIG`) is your project's central content pipeline; replace it and add rows.
- `commands/agents/pr_test_checklist_conventions.md` — the merge-heuristics table's
  `PROJECT-CONFIG` rows map your content/entity scopes to checklist sections.
- `commands/agents/pr_classification.md` — the Logic/Gameplay domain table maps the
  source game's folder shapes to review domains; replace the folder lists with yours.
- `commands/agents/review_agents.md` — the `pool-lifecycle` agent's checklist names
  example pooled types (`IPoolable` family) in brackets; substitute your pooling types
  and prune any pattern your project lacks.
- `skills/worklog_reference/SKILL.md` — the domain-classification tables use example
  content domains; replace with your project's domains.
- `skills/project_subsystems/SKILL.md` (seed) — the subsystem registry that
  `/sync_subsystems`, `/structure_audit`, the structure rules, the testing skill's
  test-support path and the brainstorm scope litmus route through; fill it at adoption.
- `commands/agents/structure_audit_agents.md` + `skills/architecture_philosophy/structure_rules.md` —
  folder-layout rules read the registry above; prune the conventions to taste.
- `skills/architecture_philosophy/SKILL.md` — the design-philosophy skill reflects
  the source projects' architectural conventions; prune to taste on first use.
- `workflows/doc_architecture_audit.js` / `commands/doc_architecture_audit.md` —
  assumes the 4-doc Obsidian documentation system; adapt vocabulary if your doc
  tree differs.
- `cloud-install.sh` / `hooks/session_context_loader.py` / `commands/workstation_setup.md` —
  pin your Godot/.NET/LSP versions (config constants at the top of each; the
  workstation command carries them as defaults).
- `scripts/regression_gate.ps1` — `$script:DigestExcl` lists engine-regenerated
  artifact paths excluded from the tree digest; add your project's.
- `scripts/run_integration_batched.ps1` — `$quarantine` (`PROJECT-CONFIG`) is the
  filter appended to every Integration batch; ships empty.
- `hooks/test_suite_gate_coverage_guard.py` — `GATED` names the test tiers the gate
  filters on (`Logic`/`Integration`/`Sanity`) and `EXCLUDED` the deliberate non-gated
  folders; match them to your `Tests/` layout.
- `hooks/refcounted_free_guard.py` — `EXEMPT` (`PROJECT-CONFIG`) names the sanctioned
  teardown helper (`Tests/Framework/Helpers/TestObjectTeardown.cs` by convention).
- `hooks/duplicate_test_double_baseline.json` — ships empty (`{}`); regenerate with
  `duplicate_test_double_guard.py --write-baseline` once your project carries a
  test-double backlog it wants grandfathered.
- `hooks/check_logger_tag_prefix.py` + `skills/logging_methodology/SKILL.md` — the
  tag-constant class is named `InstrumentationTags`; rename in both if your project
  uses another holder.
- `tools/lens.py` — `REGISTRIES` pairs each agent-registry file with the ID prefix
  its lenses use. The shipped `review` entry assumes unprefixed IDs; set your own
  prefix if your `review_agents.md` namespaces them.
- pure files with example domain nouns as inline examples only (mechanism is
  domain-agnostic): `commands/agents/orchestrator_action_protocol.md`,
  `commands/autolearn.md`, `commands/reindex_search.md`,
  `skills/instruction_quality/SKILL.md`, `skills/parallel_agents/SKILL.md`,
  `workflows/review_fanout.js`, plus the `PROJECT-CONFIG` seams in the slimmed
  git commands (`commit_push`, `clean_push`, `clean_pull`, `create_pr`) and in
  `hooks/prompt_memory_loader.py`, `hooks/prompt_git_state_delta.py`
  (WATCHED_SUBMODULES), and `hooks/compound_cd_approver.py` (SAFE_SEGMENT_COMMANDS).
  Allowlisted in `tools/audit_baseline.py`'s core-domain-noun check — swap the
  examples for your domain's when you first touch each file.
