# Harness Baseline — Claude Code config for Godot + C# projects

The shared, project-agnostic core of the Claude Code harness developed in
**PushinPotions**, extracted as a starting point for every new Godot 4.x + C#
(+ Jmodot) project — and as the **single upstream** that keeps that shared core
in sync as it evolves across projects.

## What's in it

`template/.claude/` mirrors a consumer project's `.claude/` directory: 240 files
in three layers (see `baseline.manifest.json` for the per-file map; 6 of these are
`sync: seed`, counted in `universal` below):

| Layer | Files | Contents |
|---|---|---|
| `universal` | 160 | Session lifecycle (`/session_end`, `/self_evaluate`, `/autolearn`, eval dashboard), plan pipeline (`/plan_part` → `/plan_drive` → `/plan_check` → `/part_execute`), roadmap + brainstorm system, doc system (`/doc_*`), PR workflow, worklog system, tool-routing discipline (hooks + audits), memory system (two-tier auto-memory + seeded universal gotchas/preferences), agent templates, review fan-out workflows, instruction-quality tooling, the `/sync_baseline` machinery itself |
| `godot` | 38 | Testing skill (GdUnit4 + ISceneRunner), `/regression_gate`, Godot log analysis, `.tres`/`[Tool]` safety guards, C# LSP rules + adapter, scene/physics/C# pattern rules, cloud bootstrap (`cloud-install.sh`, session context loader), Godot-specific memory gotchas |
| `jmodot` | 42 | `jmodot` framework skill (+ subsystem docs), HSM/BT patterns, status-effect authoring, VFX patterns, logging methodology (JmoLogger), submodule procedure, Jmodot architecture rules in memory |

**Seed files** (`sync: seed` in the manifest) are copied once at bootstrap and then
project-owned: `CLAUDE.md` (PROJECT section + `BASELINE:core` region), `settings.json`,
`game_vision` + `project_subsystems` skill skeletons, `known_failure_modes` catalog,
`worklog-titles.md`.

**Deliberately excluded** (stays per-project): game-content skills/commands
(spell/entity authoring, content audits), project subsystem registries, game-design
docs, project memory (beyond the curated universal/godot/jmodot seed), and all
session state (`self_evaluate_archive.json`, plans, scratch, logs, caches).

## Placeholders

Template files use three substitution variables, applied by `bootstrap.sh`:

- `{{PROJECT_NAME}}` — Godot project name (also used for `app_userdata` log paths and the Obsidian `DevProjects/<name>` folder)
- `{{VAULT_ROOT}}` — absolute path to the Obsidian vault root
- `{{PROJECT_ROOT}}` — absolute path to the project repo on your dev machine

## Adopting into a new project

```bash
git clone <this-repo> harness-baseline
cd harness-baseline
./bootstrap.sh --target /path/to/NewGame --project-name NewGame \
    --vault-root "C:/Users/you/Documents/ObsidianVault" \
    --project-root "C:/path/to/NewGame"          # add --no-jmodot to skip that layer
```

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
  pattern lists when adding files of a new kind. **Layer defaults to `universal`
  on no pattern match**, so a new Godot/Jmodot file silently lands universal (and
  escapes `--no-jmodot` stripping) until you add its pattern — the audit catches this.
- `python3 tools/audit_baseline.py` (also `/sync_baseline audit`) — the separation
  gate. Verifies no source-project identifiers / secrets leak into `template/`, the
  manifest matches disk and the generator, and no universal file is substantively
  Jmodot-specific. ERROR exit blocks publish; run it after any template change.
  `publish.sh` runs it automatically as a backstop. Its judgment pass (in the
  `/sync_baseline audit` command) covers what the script can't: game-domain-noun
  leaks and the adaptation-points list above.
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
- `workflows/doc_architecture_audit.js` / `commands/doc_architecture_audit.md` —
  assumes the 4-doc Obsidian documentation system; adapt vocabulary if your doc
  tree differs.
- `cloud-install.sh` / `hooks/session_context_loader.py` — pin your Godot/.NET
  versions (config constants at the top of each).
