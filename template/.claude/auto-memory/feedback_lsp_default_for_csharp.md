---
name: LSP is default for C# symbol queries (not grep)
description: For C# symbol/type/caller questions, default to the LSP plugin; grep only for text-only files or initial anchor discovery
type: feedback
originSessionId: a9ba133c-6ad0-4ba2-aeba-a285db998042
---
For any C# semantic question — callers of a method, the type of an expression, file structure, rename blast radius, interface-implementation discovery — default to the `LSP` tool (via `ToolSearch("select:LSP")` if not yet loaded). Do NOT reach for Grep first.

**Why:** Observed 2026-04-19 that prior sessions relied almost exclusively on Grep for C# work even when the LSP plugin was enabled and healthy. Grep misses semantic cases that bite in refactors: explicit interface implementations, generic instantiations, operator overloads, symbols reached via inheritance or overrides. User explicitly flagged this as a pattern to break. LSP was under-used because (a) schema is deferred and needs a ToolSearch step, (b) grep is frictionless for initial discovery, (c) prior CLAUDE.md "goToImplementation broken" warning caused overgeneralization to "LSP unreliable" — which is false for `findReferences`, `hover`, `incomingCalls`, `documentSymbol`, `workspaceSymbol`.

**How to apply:**
- Trigger list — default to LSP for these questions:
  - "Who calls X?" → `findReferences` or `incomingCalls`
  - "What's the type/signature of X?" → `hover`
  - "What's in this file?" → `documentSymbol` (faster + more accurate than Read for structure surveys)
  - "What does this interface method actually dispatch to?" → `findReferences` (NOT `goToImplementation` unless re-verified working; test first on a known interface before trusting)
  - Rename / cross-project refactor planning → `findReferences` before editing
- Still use Grep for: `.tscn` / `.tres` files, StringName keys, editor UIDs, open-ended keyword discovery, or to find an initial anchor when no symbol coordinate is known yet.
- First LSP call per session costs ~20–30s (Roslyn indexing); subsequent calls are sub-100ms. Don't abandon LSP after one slow call — it's amortized.
- When no coordinate is known, grep-first is legitimate — but once grep returns a hit, pivot to LSP for follow-up semantic questions rather than greping again.
- Plugin is local-only (disabled on cloud via settings.local.json); on cloud sessions grep remains the only option.
