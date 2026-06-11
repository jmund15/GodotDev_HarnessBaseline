---
description: Verify all developer-tooling MCP servers and dev environment are active and reachable. Reports Active/Failed per tool.
disable-model-invocation: true
---

# /system_check

Idempotent diagnostic. Runs a minimal verification per tool and reports status. Use when something feels off — orphan-Godot, MCP disconnect, semantic-search drift, etc.

## Procedure

1.  **Filesystem:** List the project root directory (`Glob "*"` or `ls`).
2.  **Git:** Run `git status`.
3.  **Memory:** confirm `.claude/auto-memory/` present; `mcp__plugin_semantic-search_semantic-search__search` a known memory (e.g. query `tool routing`) — report hot topic-file + `archive/` counts.
4.  **Obsidian:** `mcp__obsidian__obsidian_list_notes` against `DevProjects/{{PROJECT_NAME}}` — confirm MCP responding.
5.  **WebFetch:** Fetch `https://example.com` (round-trip verification only).
6.  **WebSearch:** Minimal search for `current unix timestamp` — confirm matcher returns ≥1 result.
7.  **Godot:** `mcp__godot__run_project` briefly (≤3 s), then `mcp__godot__get_debug_output` — confirm no startup errors. Call `mcp__godot__stop_project` if step left it running.
8.  **Semantic-search:** `mcp__plugin_semantic-search_semantic-search__search` for a known symbol (e.g. `SpellArchitecture`) — confirm index responding; warn if zero results (likely stale, run `/reindex_search`).

## Output

Single status table:

```
Tool             Status   Notes
─────────────────────────────────────────────────────────
Filesystem       Active   <N files at root>
Git              Active   <branch>, <N changes>
auto-memory       Active   <N entities>
Obsidian MCP     Active   <N notes in PP scope>
WebFetch         Active
WebSearch        Active
Godot MCP        Active   <no startup errors>
Semantic-search  Active   <N hits for SpellArchitecture>
```

Failed tools should include the exact error message returned and a single-line remediation hint (e.g. "MCP disconnected — run `/mcp` to reconnect").

## Idempotency

Re-runnable freely. No side effects beyond a brief Godot process spawn (auto-terminated by `mcp__godot__stop_project` if step 7 left it running).
