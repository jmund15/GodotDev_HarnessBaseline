# Semantic Search — Mechanism Reference

The routing verdict (USE / NOT-for, composition order) lives in `CLAUDE.md` §8. This file holds the mechanism behind the tool and its result caveats.

- **Result caveats:** heading-mention chunks can outrank canonical definitions — sharpen with `restrictToDir`. For broad discovery, search facets separately.

- **Engine:** DreB `semantic-search` plugin 2.14.0. Tree-sitter chunks the languages its scanner maps (GDScript, Python, JS/TS, C/C++, Go, Rust, Java); Godot text files and configs chunk as plain text. Ranking is embedding similarity plus 6-signal POEM re-ranking.
- **Indexed:** `.gd` `.py` `.js` `.ts` `.md` `.tres` `.tscn` `.godot` `.json` `.yaml` `.toml` `.txt` `.cfg` `.env`. **Not `.cs`:** the plugin's extension map (`dist/scanner.js` in its cache folder) has no C# entry, so a C# "where is X" question returns docs and scenes that mention X, never the class; anchor with Grep, then LSP (`CLAUDE.md` §9). Skipped: binaries, `.uid`, `.import`, anything gitignored.
- **Index file:** `.search-index/search.db` (gitignored). Stale after edits; `/reindex_search` rebuilds it (auto in `/session_end`).
- `searchDir` is the project root, or a submodule or worktree root inside it. Narrow a query with a repo-relative POSIX `restrictToDir`. `hooks/semantic_search_scope_guard.py` denies anything else at the call.
- **Cleanup:** at SessionStart, `hooks/session_context_loader.py` removes a `.search-index/` left under an ordinary subdirectory, unless its SQLite `-wal` or `-shm` file changed within a day.
