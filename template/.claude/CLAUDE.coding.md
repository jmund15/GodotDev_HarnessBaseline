## Engineering Principles
- **Ideal architecture:** `architecture_philosophy` owns the patterns: modular, scalable and extensible, loosely coupled, data-driven, without redundancy. That it outranks working code fast is `CLAUDE.core.md` §Core Principles *Goal*.
- **Modular when direction is known:** the evolutions in scope are the ones the user names (`CLAUDE.core.md` §Core Principles *Fit the work to its scope*): propose the modular design for them first, and invent no future need. Check the conversation, plan and design doc if the direction is unclear. If none names it, design for today and name the assumption.
- **Logs are Truth:** use E2E/Integration outputs and the project's runtime logs as evidence. Use available application runs, screenshots and input checks when they can test a runtime claim. Unsupported visual guesses are not evidence.

### Before adding a configuration surface
- Inventory the owning family first (`rules/design_litmus.md` #1): extend an appropriate existing family rather than create a parallel one, and assess its quality first.

## Semantic Search MCP (Natural-Language Discovery)
Grounding a design with explicit coverage goes to `/explore`. Inline, use semantic search when you do not know the name or location in docs, memory, assets, configuration or scripts; compose semantic search → LSP `findReferences` → Grep (literal anchors). Grep owns the occurrences of a known exact name, field, ID, path or pattern; which files discuss or implement a named concept is still a semantic search. If a search is empty, confirm scope/ignore behavior with git-aware enumeration before claiming absence. Use `/reindex_search` for scope changes or index drift; `reference/semantic_search.md` owns index details and result caveats.
