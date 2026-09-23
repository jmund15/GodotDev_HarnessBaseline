@CLAUDE.core.md

# CLAUDE.md — Development Guidelines

<!-- PROJECT-OWNED — everything below is yours; it is never synced. Fill in at adoption. Shared doctrine lives in CLAUDE.core.md. Each section lives whole in one of the two files: this file restates no core section, and the core cites nothing here. -->

## Project Guidelines
**{{PROJECT_NAME}}** uses Godot, C# and the Jmodot framework; exact stack/version facts belong to `reference/project_stack.md`. Concept: <one-line game concept>.

## Development Philosophy: Hybrid TDD
**Logic:** <your pure-logic subsystems, e.g. `Jmodot.Core`, inventory, math/parsing and data structures> require a failing test before implementation, including behavior-changing `.tres` data. Suites: `<Tests/Logic/>`.

**Gameplay:** automate deterministic behavior, wiring and lifecycle with integration tests; inspect subjective feel. <Your gameplay subsystems, e.g. player entity, enemy AI, VFX, UI and physics> use this route. `testing` owns recipes; `/prototype` owns open feel probes and deliberately provisional designs. No testing-first exception for an obvious Logic change.

Refuse this premise and cite the rule:

| Rationalization | Rule to cite |
|---|---|
| "the logic is obvious, let's implement first then test" | §Hybrid TDD: no carve-out for self-evident logic |

## 3. Obsidian (The Design Source)
Read and search `DevProjects/{{PROJECT_NAME}}` and `DevProjects/Jmodot`, plus any vault path the user, a skill or a command names; native file tools work even while Obsidian is open. Search before choosing a path. Project-specific writes go to `{{PROJECT_NAME}}/Claude/`; reusable Jmodot guidance goes to `Jmodot/Claude/`. Load `obsidian_conventions`; `environment_bootstrap` owns machine paths. Never invent a formula absent from the design source.

## The Worklog (Live Todo Doc)
`DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` is authoritative; `.claude/worklog-titles.md` is the title-only lookup. `worklog_reference` and `/worklog` own operations. Do small in-context work now; propose genuine deferrals once, and complete an item when it finishes. The once-per-session relevance check fires when production scope becomes nameable; skip meta-only work and a single known mechanical fix.

## Project Domains
Add your content domains to `.claude/skills/project_subsystems/adaptation.json`'s `memory_domains` key; it is the only source, feeding both `reference/memory_domains.md` and `hooks/plan_memory_reminder.py` `DOMAINS`.

## Project-Specific Conventions
*   <add conventions that only make sense in this game — content taxonomies, naming, subsystem invariants>
*   Register subsystems in `skills/project_subsystems/SKILL.md` (consumed by `/sync_subsystems`, `/structure_audit`, and brainstorm scope litmus).
*   Capture the game's design bible in `skills/game_vision/SKILL.md`.
