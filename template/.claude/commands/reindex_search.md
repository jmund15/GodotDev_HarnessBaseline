# Re-index Semantic Search

Refreshes the DreB semantic-search index (`.search-index/search.db`) so subsequent
`mcp__plugin_semantic-search_semantic-search__search` queries reflect the current code state.
For indexed file types and tool guidance, see CLAUDE.md §9.

## When to run

- After a meaningful code shift (rebase, branch switch, large multi-file edit, large `.tres` re-save).
- Automatically as Phase 8 of `/session_end` (kept fresh per work block — see `session_end.md`).
- On demand if `mcp__plugin_semantic-search_semantic-search__search` returns stale-looking results or has been silently degraded by index drift.

## Procedure

1. Verify the plugin is installed and the MCP tool is available. If `mcp__plugin_semantic-search_semantic-search__search` is not present in the deferred tool list, stop with a clear message — the command is a no-op when the plugin is uninstalled, do NOT error.

2. Trigger a clean rebuild by issuing a no-op search call with `rebuild: true`:
   - `query`: `"."`  (any non-empty string — content doesn't matter when rebuild is on)
   - `searchDir`: project root (absolute path, `{{PROJECT_ROOT}}` on local Windows)
   - `rebuild`: `true`
   - `limit`: `1`  (we don't care about results, just the rebuild side effect)

   The rebuild walks the working tree, drops the existing `.search-index/search.db`, and rebuilds AST chunks + embeddings + 6-signal scoring tables. Initial build is 10–60s depending on project size; subsequent rebuilds are similar (rebuild is full-walk, not incremental).

3. Report:
   - Time elapsed
   - Whether the index file (`.search-index/search.db`) was successfully recreated (use `Bash` `ls -la .search-index/`)
   - Any obvious errors from the search response

## Failure modes

- **Plugin uninstalled / tool not registered:** report and exit cleanly. This is not an error — the command is a no-op when the feature is disabled.
- **Re-index errors mid-walk:** report the error with the partial state. Do NOT block the calling command. `/session_end`'s Phase 8 should warn-and-continue, not abort the commit-push pipeline.
- **`.search-index/` permission errors:** check that `.search-index/` is in `.gitignore` (it is) and that no other process is holding the SQLite db open. On Windows, an editor-side index lock is rare but possible.

## Why `rebuild: true` instead of letting incremental do its job?

The upstream skill says incremental updates handle changed files automatically — but only for files within the indexed scope. After a branch switch or large `.tres` re-save (which Godot does on every editor reopen), the file timestamps don't always trigger reliable incremental detection. A forced full rebuild is the safest "I want a fresh state" mechanism. The cost is ~30–60s per session_end run, which is acceptable.

## Cross-references

- CLAUDE.md §9 (Semantic Search MCP) — composition pattern + cost & freshness notes.
- Memory entity `Semantic_Search_Composition_Pattern` — canonical pointer for subagent contexts.
- `/session_end` Phase 8 — calls this command in its end-of-session sweep.
- Upstream SKILL.md at `~/.claude/plugins/cache/dreb/semantic-search/<version>/skills/search/SKILL.md` — for parameter details and ranking model.
