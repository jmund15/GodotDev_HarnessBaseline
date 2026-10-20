---
description: Refresh semantic search after file changes or index drift
---

# Re-index Semantic Search

Refreshes the DreB semantic-search index (`.search-index/search.db`) so subsequent
`mcp__plugin_semantic-search_semantic-search__search` queries reflect the current code state.
For indexed file types and tool guidance, see CLAUDE.core.md §9.

## When to run

- After a meaningful code shift (rebase, branch switch, large multi-file edit, large `.tres` re-save).
- Automatically as Phase 8 of `/session_end` (kept fresh per work block — see `session_end.md`).
- On demand if `mcp__plugin_semantic-search_semantic-search__search` returns stale-looking results or has been silently degraded by index drift.

## Procedure

1. Verify the plugin is installed and the MCP tool is available. If `mcp__plugin_semantic-search_semantic-search__search` is not present in the deferred tool list, stop with a clear message — the command is a no-op when the plugin is uninstalled, do NOT error.

2. Trigger a clean rebuild by issuing a no-op search call with `rebuild: true`:
   - `query`: `"."`  (any non-empty string — content doesn't matter when rebuild is on)
   - `searchDir`: project root — derive from `$CLAUDE_PROJECT_DIR` (or the session's working directory / repo root); do NOT hardcode, the path differs per machine.
   - `rebuild`: `true`
   - `limit`: `1`  (we don't care about results, just the rebuild side effect)

   The rebuild walks the working tree, drops the existing `.search-index/search.db`, and rebuilds AST chunks + embeddings + 6-signal scoring tables. Each rebuild is a full walk, not an incremental update.

3. Regenerate the abstraction-family manifest — the same staleness window as the index, so it refreshes on the same cadence:
   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/generate_family_manifest.py"
   ```
   Rewrites `.claude/generated/abstraction_families.md` wholesale (every first-party abstract class / interface with 1+ implementors, plus the shared test doubles). Engine-free and deterministic; the file is generated, never hand-edited. Report the family/implementor counts it prints. A failure here does NOT block — report it and continue, same as a partial re-index.

4. Report composition — what the corpus is now made of, top buckets by chunk count plus
   `.claude/auto-memory`'s share, read straight from the rebuilt db:
   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/tools/index_composition.py"
   ```
   Print the table as-is. It is the only routine signal that the corpus has drifted; recall
   degrades silently otherwise, so a number every rebuild is what makes drift visible.
   Flag a bucket that has become disproportionate against §Corpus hygiene below.

5. Report:
   - Time elapsed
   - Whether the index was actually **rebuilt** — `ls -la .search-index/` and confirm `search.db`'s mtime is *now*, not merely that the file exists. A failed rebuild leaves the stale db in place, so existence proves nothing. If the rebuild reports `EBUSY` but the mtime moved, report "incremental, not rebuilt" and skip the reap path below.
   - Any obvious errors from the search response

## Corpus hygiene — ranking is competitive

Every chunk in the index competes with every other. The ranker scores Pareto dominance across
six metrics, one of which is `gitRecency`, so **freshly-written bulk outranks stable content on
that objective regardless of relevance** — a thousand new junk chunks do not merely add noise,
they displace real answers.

**The invariant:** a directory of generated, per-session, or duplicated artifacts gets its
`.gitignore` entry when it is created, not after recall degrades. The indexer honors
`.gitignore`, so that entry is the whole control surface. Anything genuinely worth searching
stays tracked — the test is whether a *future* session would want to find it, not whether it
was expensive to produce.

Do NOT respond to a recall failure by adding query-phrasing guidance. Encoding a workaround for a structural fault outlives the fault and misleads after it
is repaired — check composition first.

## Failure modes

- **Plugin uninstalled / tool not registered:** see Step 1.
- **Re-index errors mid-walk:** report the error with the partial state. Do NOT block the calling command.
- **`EBUSY: resource busy or locked, unlink '...search.db'`** — another semantic-search MCP server holds the db open (a stranded server keeps the project file locked). A server whose parent is alive belongs to a live session, regardless of the executable name. `tools/reap.py` refuses every semantic-search server as shared infrastructure, so no session kills one.

  1. Enumerate holders and their parents:
     `Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -match 'semantic-search' } | ForEach-Object { $par = Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue; [PSCustomObject]@{ PID=$_.ProcessId; Started=$_.CreationDate; ParentAlive=[bool]$par; ParentName=$par.ProcessName } }`
  2. Report the holders and the db's mtime, record the phase blocked, and continue. The next run after the holder's session exits rebuilds.
  3. A true orphan (`ParentAlive=False`) that keeps the lock across sessions is the owner's to stop; name its PID in the report.
- **`FOREIGN KEY constraint failed`** — a stale rowid, not corruption: one indexer captured a file's id while another re-indexed that file (delete + re-insert allocates a new one). Retry; the next pass re-resolves it. `foreign_key_check` stays clean, and a genuine lock says `database is locked` instead. Repeats ⇒ enumerate holders as for EBUSY above. Report contention, never a broken capability.
- **`.search-index/` permission errors:** check that `.search-index/` is in `.gitignore`. Distinguish from the EBUSY case above — a true ACL error names a denial, not a busy resource.

## Why `rebuild: true` instead of letting incremental do its job?

Incremental updates cover only changed files within the indexed scope. Use a full rebuild when the index needs a fresh state.

## Cross-references

- Memory entity `Semantic_Search_Composition_Pattern` — canonical pointer for subagent contexts.
- Upstream SKILL.md at `~/.claude/plugins/cache/dreb/semantic-search/<version>/skills/search/SKILL.md` — for parameter details and ranking model.
