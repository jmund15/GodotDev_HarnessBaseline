## Engineering Principles
- **Ideal architecture is the deliverable:** modular, scalable/extensible, clean/intuitive, loosely coupled, data-driven, no-redundancy design outranks working-code-fast at EVERY stage (design, plan, execute, review). Prototyping is the exception. `architecture_philosophy` owns the patterns.
- **Act on the cleanup you notice:** check a simpler design against its callers and tests. Do an in-file, behavior-preserving cleanup inline and name it in the close. Ask the owner through a question packet (§Preferences) before a cleanup changes a contract or widens the write set. Commit a confirmed cleanup on its own, before the work that needed it. A delegate's cleanup follows `skills/orchestration/reference/writer_returns.md`.
- **Modular when direction is known:** design for an evolution the user names, and propose the modular design first; do not invent future needs. Check the conversation, plan and design doc if the direction is unclear. If none names it, design for today and name the assumption.
- **Logs are Truth:** use E2E/Integration outputs and the project's runtime logs as evidence. Use available application runs, screenshots and input checks when they can test a runtime claim. Unsupported visual guesses are not evidence.

### Planning Phase Checklist
Before choosing a work path, triage the work by the fields in `skills/_brainstorm_shared/execution_depth.md` and record it; re-triage when a fact changes. An admitted plan file is approved by the owner, or by explicit unattended authority, before execution. **Do not enter Plan Mode as part of this project's planning flow.**

- Inventory the owning family through `/explore` before adding an authored or shared configuration surface (type, exposed field, behavior bool/enum). Extend an appropriate existing family rather than create a parallel one; assess its quality first.

## Semantic Search MCP (Natural-Language Discovery)
Use semantic search when you do not know the name or location in docs, memory, assets, configuration or scripts. Compose semantic search → LSP `findReferences` → Grep (literal anchors); Grep owns literal fields, IDs and patterns. If a search is empty, confirm scope/ignore behavior with git-aware enumeration before claiming absence. Use `/reindex_search` for scope changes or index drift; `reference/semantic_search.md` owns index details and result caveats.
