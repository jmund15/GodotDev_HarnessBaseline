# Semantic Search — Mechanism Reference

The routing verdict (USE / NOT-for, composition order) lives in `CLAUDE.coding.md` §Semantic Search MCP. This file holds the mechanism behind the tool and its result caveats.

- **Result caveats:** heading-mention chunks can outrank canonical definitions — sharpen with `restrictToDir`. For broad discovery, search facets separately.

- **Engine:** DreB `semantic-search` plugin (installed version: its cache folder name). Tree-sitter chunks the languages its scanner maps (GDScript, Python, JS/TS, C/C++, Go, Rust, Java); other text files and configs chunk as plain text. Ranking is embedding similarity plus 6-signal POEM re-ranking.
- **Indexed:** `.gd` `.py` `.js` `.ts` `.md` `.tres` `.tscn` `.godot` `.json` `.yaml` `.toml` `.txt` `.cfg` `.env`. **Not `.cs`:** the plugin's extension map (`dist/scanner.js` in its cache folder) has no C# entry, so a C# "where is X" question returns docs and scenes that mention X, never the class; anchor with Grep, then LSP (`CLAUDE.core.md` §9). Skipped: binaries, `.uid`, `.import`, anything gitignored.
- **Index file:** `.search-index/search.db` (gitignored). Each search scans for new, mtime-changed and removed files before ranking. Use `/reindex_search` for scope changes or index drift.
- `searchDir` is the project root, a submodule or worktree root inside it, or one of the design-source roots the project lists in `adaptation.json` `doc_roots` (`CLAUDE.md` §3 names the vault). The guard compares resolved paths; the vault and `DevProjects` parents and other external roots stay denied. Narrow a query with a POSIX `restrictToDir` relative to its index root. A repo index cannot search the vault. Vault searches create/update `.search-index/search.db` under the selected vault root; search each named root, not the whole vault. `hooks/semantic_search_scope_guard.py` normalizes ordinary repo subdirectories to the repo root plus their prefix.
- **Cleanup:** a `.search-index/` left under an ordinary subdirectory is stray; remove it unless its SQLite `-wal` or `-shm` file changed within a day. Where the project's SessionStart context loader handles it, removal is automatic.
