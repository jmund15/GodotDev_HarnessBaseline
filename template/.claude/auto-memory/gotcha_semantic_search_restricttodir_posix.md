---
name: gotcha_semantic_search_restricttodir_posix
description: "semantic-search restrictToDir matches the index's stored REPO-RELATIVE POSIX paths, not absolute OS paths. An absolute Windows path (C:\\...\\dir) silently returns ZERO results — a false 'not indexed' signal. Confirm absence with an unrestricted query before concluding a path isn't indexed."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 0154c3a7-691a-4c1b-acb2-f593c6f511b1
---

`mcp__plugin_semantic-search_semantic-search__search`'s `restrictToDir` parameter filters against the index's **stored repo-root-relative posix paths** (e.g. `.claude/auto-memory`). An **absolute OS path** (`C:\Users\...\.claude\auto-memory`) matches nothing and returns "No results found" — even when that directory is fully indexed.

**Why:** the scanner stores each `filePath` as a repo-relative posix string; `restrictToDir` is a prefix match against that, so an absolute or backslash path can never match.

**How to apply:**
- Always pass `restrictToDir` as a **repo-relative posix path**: `.claude/auto-memory`, never `C:\Users\...`.
- NEVER conclude "directory X isn't indexed" from a restricted query alone — a path-format mismatch looks identical to genuine absence. Confirm with an **unrestricted** query for a phrase distinctive to that directory.
- `searchDir` pointed AT a subdir builds a *separate* per-dir index there (`<dir>/.search-index/`); the main repo index (searchDir = repo root) already covers `.claude/` including `auto-memory/`.

**Concrete:** during the 2026-05-31 MCP→file memory migration, two probes with an absolute `restrictToDir` returned 0 results and nearly produced a false "auto-memory isn't indexed" conclusion that would have derailed the whole approach; an unrestricted query proved the dir was indexed at bm25=1.00.

Related: [[project_memory_single_store_two_tier]], [[feedback_tool_routing_discipline]].
