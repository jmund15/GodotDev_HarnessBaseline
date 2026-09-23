## Engineering Principles
- **Ideal architecture is the deliverable:** modular, scalable/extensible, clean/intuitive, loosely coupled, data-driven, no-redundancy design outranks working-code-fast at EVERY stage (design, plan, execute, review). Prototyping is the exception. `architecture_philosophy` owns the patterns.
- **Modular when direction is known:** design for an evolution the user names, and propose the modular design first; do not invent future needs. When unclear whether direction was stated, ask.
- **Logs are Truth:** you can't see runtime. Rely on E2E/Integration outputs and the project's runtime logs, not on visual guesses.

### Planning Phase Checklist
Use normal conversation: `/explore` → plan file → `/plan_check` → user approval or explicit unattended authority. **Do not enter Plan Mode as part of this project's planning flow.** Plan files and approval are mandatory wherever step 3's `/plan_check` threshold holds; smaller work follows `_brainstorm_shared/execution_depth.md`. Runtime instruction priority still applies.

1. Name the task and domains; search memory before drafting.
2. Before adding a configuration surface at any granularity (type, `[Export]`, parameter, behavior bool/enum, helper), inventory its owning family through `/explore`. Extend an appropriate existing family rather than create a parallel one; assess its quality first. Litmus for code: `rules/design_litmus.md` #1.
3. Run `/plan_check` for 3+ files, new concepts/surfaces, family refactors, replacements/deletions or always-loaded guidance. Harness plans are included. Resolve forks before execution; `skills/_brainstorm_shared/plan_file_format.md` owns the plan format.

## Semantic Search MCP (Natural-Language Discovery)
Use semantic search when you do not know the name or location in docs, memory, scenes, resources or scripts. Compose semantic search → LSP `findReferences` → Grep (literal anchors); Grep owns literal fields, UIDs and patterns. If a search is empty, confirm scope/ignore behavior with git-aware enumeration before claiming absence. Refresh through `/reindex_search` after changes; `reference/semantic_search.md` owns index details and result caveats.
