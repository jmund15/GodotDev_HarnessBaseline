@CLAUDE.core.md

## Project Guidelines
<!-- PROJECT-OWNED — everything below is yours; it is never synced. Fill in at adoption. -->

**{{PROJECT_NAME}}**: Godot 4.x (<physics engine>), C# (.NET <version>), Jmodot framework. Concept: <one-line game concept>. Pin the exact engine and runtime versions here — the core's WebFetch section re-verifies against this section before any version-sensitive claim.

### Domain Split (feeds Hybrid TDD in the core)
*   **Logic Domain (Strict TDD):** <list your pure-logic subsystems, e.g. `Jmodot.Core`, `Inventory`, `Math/Parsing`, data pipelines>
*   **Gameplay Domain (Integration + Inspection):** <list your gameplay subsystems, e.g. player entity, enemy AI BT, content lifecycle, VFX, UI>

### Project Domains (extends the core's Proactive Context Loading)
Add your content domains to `.claude/skills/project_subsystems/adaptation.json`'s `memory_domains` key; it is the only source, feeding both `reference/memory_domains.md` and `hooks/plan_memory_reminder.py` `DOMAINS`.

### Obsidian vault folders
Read and search `DevProjects/{{PROJECT_NAME}}` and `DevProjects/Jmodot`, plus any other vault path the user, a skill or a command names. Project-specific writes go to `{{PROJECT_NAME}}/Claude/`; reusable Jmodot guidance goes to `Jmodot/Claude/`.

### Worklog
`DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` is authoritative.

### Project-Specific Conventions
*   <add conventions that only make sense in this game — content taxonomies, naming, subsystem invariants>
*   Register subsystems in `skills/project_subsystems/SKILL.md` (consumed by `/sync_subsystems`, `/structure_audit`, and brainstorm scope litmus).
*   Capture the game's design bible in `skills/game_vision/SKILL.md`.
