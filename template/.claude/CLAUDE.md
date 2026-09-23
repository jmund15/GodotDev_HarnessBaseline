@CLAUDE.core.md

# CLAUDE.md — Development Guidelines

<!-- PROJECT-OWNED — everything below is yours; it is never synced. Fill in at adoption. Shared doctrine lives in CLAUDE.core.md and, for each adopted layer, CLAUDE.coding.md and CLAUDE.godot.md; bootstrap writes their imports above. Each section lives whole in one file: this file restates no imported section, and the imported files cite nothing here. -->

## Project Guidelines
**{{PROJECT_NAME}}** — <one-line concept>. Exact stack/version facts belong to `reference/project_stack.md`.

## Development Philosophy: Hybrid TDD
<Your test-first policy: which domains require a failing test before implementation, which are verified by integration tests or inspection, and the rationalization you refuse.>

## 3. Obsidian (The Design Source)
Read and search `DevProjects/{{PROJECT_NAME}}`, plus any vault path the user, a skill or a command names; native file tools work even while Obsidian is open. Search before choosing a path. Project-specific writes go to `{{PROJECT_NAME}}/Claude/`. Load `obsidian_conventions`; `environment_bootstrap` owns machine paths. Never invent a formula absent from the design source.

## The Worklog (Live Todo Doc)
`DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` is authoritative; `.claude/worklog-titles.md` is the title-only lookup. `worklog_reference` and `/worklog` own operations. Do small in-context work now; propose genuine deferrals once, and complete an item when it finishes. The once-per-session relevance check fires when production scope becomes nameable; skip meta-only work and a single known mechanical fix.

## Project Domains
Add your content domains to `.claude/skills/project_subsystems/adaptation.json`'s `memory_domains` key; it is the only source, feeding both `reference/memory_domains.md` and `hooks/plan_memory_reminder.py` `DOMAINS`.

## Project-Specific Conventions
*   <add conventions that only make sense in this project — content taxonomies, naming, subsystem invariants>
*   Register subsystems in `skills/project_subsystems/SKILL.md`; its YAML registry is the one source every subsystem-aware command reads.
