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

2. Refresh the index with one search call:
   - `query`: `"."`  (any non-empty string; the results are not used)
   - `searchDir`: project root — derive from `$CLAUDE_PROJECT_DIR` (or the session's working directory / repo root); do NOT hardcode, the path differs per machine.
   - `limit`: `1`

   Every search compares each file's mtime with its stored record, then re-chunks and re-embeds only new or changed files and drops deleted ones, so the refresh costs the files changed since the last search. Add `rebuild: true` only for a named cause: a plugin or C# hook upgrade, a corrupt db, or a `.gitignore` change that drops indexed paths. A rebuild drops `search.db` and re-embeds the whole corpus, which takes tens of minutes at this repo's size.

3. Run the project's index prebuild commands — generated files that go stale on the same window as the index:
   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/tools/adaptation.py" run index_prebuild
   ```
   Report the counts the commands print and any failing command; neither blocks the re-index.

4. Report composition — what the corpus is now made of, top buckets by chunk count plus
   `.claude/auto-memory`'s share, read straight from the refreshed db:
   ```bash
   python3 "$CLAUDE_PROJECT_DIR/.claude/tools/index_composition.py"
   ```
   Print the table as-is. It is the only routine signal that the corpus has drifted; recall
   degrades silently otherwise, so a number every refresh is what makes drift visible.
   Flag a bucket that has become disproportionate against §Corpus hygiene below. Run `python3 "$CLAUDE_PROJECT_DIR/.claude/tools/index_composition.py" --set-baseline` when it prints `no baseline set`, or when a flag traces to an intended scope change (a newly indexed file type), and the corpus passes §Corpus hygiene.

5. Report:
   - Time elapsed
   - Whether the index took the session's changes: the search response carries no error, and `search.db`'s mtime (`ls -la .search-index/`) is *now* when any indexed file changed since the last search. An unchanged mtime after changed files means the refresh failed and the stale db stands.
   - Any obvious errors from the search response

## Corpus hygiene — ranking is competitive

Every chunk in the index competes with every other. The ranker scores Pareto dominance across
six metrics, one of which is `gitRecency`, so **freshly-written bulk outranks stable content on
that objective regardless of relevance** — a thousand new junk chunks do not merely add noise,
they displace real answers.

**The invariant:** a directory of generated, per-session, or duplicated artifacts gets its
`.gitignore` entry when it is created, not after recall degrades. A tracked path that git must
keep but search should not (tests, frozen fixtures, vendored code) goes in `.searchignore` instead;
a harness path goes in `.claude/searchignore.harness`, which `baseline_sync.py pull` writes into
`.searchignore`'s harness block. The indexer honors `.gitignore` and `.searchignore`, and they are the whole control surface. Anything genuinely worth searching
stays tracked — the test is whether a *future* session would want to find it, not whether it
was expensive to produce.

Do NOT respond to a recall failure by adding query-phrasing guidance. Encoding a workaround for a structural fault outlives the fault and misleads after it
is repaired — check composition first.

## Failure modes

- **Plugin uninstalled / tool not registered:** see Step 1.
- **Re-index errors mid-walk:** report the error with the partial state. Do NOT block the calling command.
- **`EBUSY: resource busy or locked, unlink '...search.db'`** — another semantic-search MCP server holds the db open (a stranded server keeps the project file locked). A `rebuild: true` call meets it when it unlinks the db; if the mtime moved anyway, report "incremental, not rebuilt" and skip the steps below. A server whose parent is alive belongs to a live session, regardless of the executable name. `tools/reap.py` refuses every semantic-search server as shared infrastructure, so no session kills one.

  1. Enumerate holders and their parents:
     `Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -match 'semantic-search' } | ForEach-Object { $par = Get-Process -Id $_.ParentProcessId -ErrorAction SilentlyContinue; [PSCustomObject]@{ PID=$_.ProcessId; Started=$_.CreationDate; ParentAlive=[bool]$par; ParentName=$par.ProcessName } }`
  2. Report the holders and the db's mtime, record the phase blocked, and continue. The next run after the holder's session exits refreshes.
  3. A true orphan (`ParentAlive=False`) that keeps the lock across sessions is the owner's to stop; name its PID in the report.
- **`FOREIGN KEY constraint failed`** — a stale rowid, not corruption: one indexer captured a file's id while another re-indexed that file (delete + re-insert allocates a new one). Retry; the next pass re-resolves it. `foreign_key_check` stays clean, and a genuine lock says `database is locked` instead. Repeats ⇒ enumerate holders as for EBUSY above. Report contention, never a broken capability.
- **`.search-index/` permission errors:** check that `.search-index/` is in `.gitignore`. Distinguish from the EBUSY case above — a true ACL error names a denial, not a busy resource.
- **The client aborts the call after its MCP idle timeout** (`sent no response or progress for <N>s`): the backend keeps indexing. Check `search.db`'s mtime later; a moved mtime with no error means the refresh finished, and an unmoved one after the backend exits is a failed refresh.

## Cross-references

- Memory entity `Semantic_Search_Composition_Pattern` — canonical pointer for subagent contexts.
- Upstream SKILL.md at `~/.claude/plugins/cache/dreb/semantic-search/<version>/skills/search/SKILL.md` — for parameter details and ranking model.
