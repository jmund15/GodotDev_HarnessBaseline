# Harness Baseline — layered Claude Code harness

The shared, project-agnostic harness core extracted from a game-development
harness as a starting point for any Claude Code project — game dev (Godot 4.x +
C#, Jmodot builds included), content production, or other — and as the **single
upstream** that keeps that shared core in sync as it evolves across projects.

## What's in it

`template/.claude/` mirrors a consumer project's `.claude/` directory: 368 files
in three archetypes (see `baseline.manifest.json` for the per-file map; 6 of these
are `sync: seed`, counted in `pure` below):

| Layer | Files | Contents |
|---|---|---|
| `pure` | 150 | fully domain-agnostic; serves any Claude Code project including non-code content production: session lifecycle (`/session_end`, `/self_evaluate`, `/autolearn`, eval dashboard), doc system (`/doc_*`), worklog system, memory system + curated process/discipline auto-memory seed, agent templates, review fan-out workflows, instruction-quality tooling, slimmed git commands (`/commit_push`, `/clean_push`, `/create_pr`), the `/sync_baseline` machinery itself, the DeepSeek sidecar trio (`external_models.json`, `claude_profile_functions.ps1`, `model_registry.py`) |
| `coding` | 79 | any programming project, not content production: plan/roadmap pipeline (`/plan_part` → `/plan_drive` → `/plan_check` → `/part_execute`), brainstorm redteam, heavy PR machinery (`/merge_pr`, `/pr_ready`, `/review_pr(s)`), tool-routing hook family, TDD/debugging/architecture skills, code-hygiene auto-memory |
| `godot` | 139 | Testing skill (GdUnit4 + ISceneRunner), `/regression_gate`, Godot log analysis, `.tres`/`[Tool]` safety guards, C# LSP rules + adapter, scene/physics/C# pattern rules, cloud bootstrap (`cloud-install.sh`, session context loader), Godot-specific memory gotchas, the Jmodot framework skill + subsystem docs, HSM/BT patterns, status-effect authoring, VFX patterns, logging methodology (JmoLogger), submodule procedure, and `/workstation_setup` |

A consumer subscribes to a prefix of `pure` → `coding` → `godot`.

**Seed files** (`sync: seed` in the manifest) are copied once at bootstrap and then
project-owned: `CLAUDE.md` (PROJECT section + `BASELINE:core` region), `settings.json`,
`game_vision` + `project_subsystems` skill skeletons, `known_failure_modes` catalog,
`worklog-titles.md`.

**Deliberately excluded** (stays per-project): game-content skills/commands
(spell/entity authoring, content audits), project subsystem registries, game-design
docs, project memory (beyond the curated pure/coding/godot seed), and all
session state (`self_evaluate_archive.json`, plans, scratch, logs, caches).

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
create the vault `Claude/TODO/` folders, `/system_check`, `/reindex_search`).

## Keeping projects and baseline in sync

The contract: **the baseline never changes for project-specific edits; every
universal improvement flows back here.**

Mechanism (per consumer project):

- `.claude/baseline.lock.json` — records the baseline repo/ref, the substitution
  map, and per-file state: `tracked` (hash-synced), `watch` (seed/adapted files —
  flagged on change, judged manually), `forked` (intentionally diverged), `local`
  (project-owned artifact acknowledged as not-for-baseline).
- `.claude/tools/baseline_sync.py` — mechanical three-way engine
  (`check` / `diff` / `pull` / `materialize` / `update-lock` / `fork`).
  Substitution-aware in both directions, so bootstrapped copies compare clean
  against placeholder templates.
- `/sync_baseline` — the judgment wrapper: classifies local changes
  universal-vs-project-specific, upstreams universal hunks (reverse-substituted)
  to this repo, pulls baseline updates into the project, proposes forks for files
  that keep diverging.
- **Drift gate in `/clean_push` and `/commit_push`** — when a commit touches a
  tracked file, the push workflow surfaces it and routes through `/sync_baseline`
  instead of letting shared doctrine fork silently. `CLAUDE.md` §10 carries the
  always-loaded version of this rule.

Typical lifecycles:

- *Improved a hook / command / skill while working on game A* → commit in A →
  drift gate flags it → `/sync_baseline push` → baseline updated → in game B,
  `/sync_baseline pull` (run it occasionally, or when starting significant work).
- *Project-specific tweak to a tracked file* → drift gate flags it → classified
  project-specific → either keep as standing local diff (stays visible in `check`)
  or `/sync_baseline fork <file>` if permanent.
- *New universal artifact born in a project* → `/sync_baseline push` "Always"
  clause: copy into `template/`, regenerate manifest, `track` it in the lock.

## Maintaining this repo

- `python3 tools/gen_manifest.py` after any add/remove/move under `template/` —
  the manifest drives bootstrap layer-filtering and consumer lock generation.
  Layer/seed assignment is pattern-based at the top of that script; extend the
  pattern lists when adding files of a new kind. **Layer assignment now has NO
  fallback**; `gen_manifest.py` fails loudly listing any unclassified file, so
  every new file must be added to exactly one layer pattern list.
- `python3 tools/audit_baseline.py` (also `/sync_baseline audit`) — the separation
  gate. Verifies no source-project identifiers / secrets leak into `template/`, the
  manifest matches disk and the generator, and flags (INFO) a `pure` file naming
  >=4 godot/coding markers, or a `coding` file naming >=4 godot markers
  (layer-gate check). It also warns on pure-tagged files naming code/engine or
  consumer-domain nouns (core-domain-noun check). ERROR exit blocks publish; run it
  after any template change. `publish.sh` runs it automatically as a backstop. Its
  judgment pass (in the `/sync_baseline audit` command) covers what the script
  can't: game-domain-noun leaks and the adaptation-points list above.
- Commit messages follow the same categorical convention as consumer projects
  (`feat`/`fix`/`refactor`/`chore`).
- Model/tooling evolution (new Claude models, new plugin capabilities, superior
  workflows) lands here exactly like any universal improvement: change it in
  whichever project discovered it, upstream via `/sync_baseline push`, and other
  projects adopt via `pull`.

## Publishing (one-time)

This directory ships inside the source project until it has its own repo:

```bash
# create an empty GitHub repo first, then:
./publish.sh git@github.com:<you>/harness-baseline.git
```

After publishing, point consumer locks' `baseline_repo` at the new URL
(bootstrap does this automatically via the baseline clone's `origin`).

## Known adaptation points

A few included files are generic in shape but carry the source project's conventions
as concrete examples — review them on first use in a new project. (`tools/audit_baseline.py`
keeps this list honest: its judgment pass flags adaptation-shaped files missing from here.)

- `hooks/plan_memory_reminder.py` — `PROJECT-CONFIG` domain table at the top:
  add your game's content domains.
- `commands/doc_start_here_update.md` — the domain-classification table's first row
  (`PROJECT-CONFIG`) is your game's content pipeline; replace it and add rows.
- `commands/agents/pr_test_checklist_conventions.md` — the merge-heuristics table's
  `PROJECT-CONFIG` rows map your content/entity scopes to checklist sections; the
  example checklist items use the source game's nouns illustratively.
- `commands/agents/review_agents.md` — the `pool-lifecycle` agent's checklist names
  example pooled types in brackets; substitute your pooling types and prune any
  pattern (e.g. sibling collision groups) your project lacks.
- `commands/agents/structure_audit_agents.md` + `skills/architecture_philosophy/structure_rules.md` —
  folder-layout rules reflect the source project's conventions; prune to taste.
- `commands/workstation_setup.md` — the machine-provisioning command carries the
  source machine's toolchain pins (GODOT_BIN, .NET SDK, LSP) as defaults; review
  them against your fresh-PC environment on first run.
- `skills/architecture_philosophy/SKILL.md` — the design-philosophy skill reflects
  the source project's architectural conventions; prune to taste on first use.
- `workflows/doc_architecture_audit.js` / `commands/doc_architecture_audit.md` —
  assumes the 4-doc Obsidian documentation system; adapt vocabulary if your doc
  tree differs.
- `cloud-install.sh` / `hooks/session_context_loader.py` — pin your Godot/.NET
  versions (config constants at the top of each).
- `commands/agents/pr_classification.md` — the Logic/Gameplay domain table maps the
  source game's folders to review domains; replace the folder lists with yours.
- `skills/sprite_authoring/SKILL.md` — the *Project Prototype Style* section is the
  source game's style spec (palette, faction looks, reference sprites); rewrite it
  for your game's art direction, keeping the pipeline mechanics.- pure files with source-domain nouns as inline examples only (mechanism is
  domain-agnostic): `commands/agents/orchestrator_action_protocol.md`,
  `commands/autolearn.md`, `commands/reindex_search.md`,
  `skills/instruction_quality/SKILL.md`, `skills/parallel_agents/SKILL.md`,
  `workflows/review_fanout.js`, plus the PROJECT-CONFIG seams in the slimmed
  git commands (`commit_push`, `clean_push`, `clean_pull`, `create_pr`) and in
  `hooks/prompt_memory_loader.py`, `hooks/prompt_git_state_delta.py`
  (WATCHED_SUBMODULES), `hooks/plan_memory_reminder.py` (DOMAINS), and
  `hooks/compound_cd_approver.py` (SAFE_SEGMENT_COMMANDS). Allowlisted in
  `tools/audit_baseline.py`'s core-domain-noun check — swap the examples for
  your domain's when you first touch each file.
