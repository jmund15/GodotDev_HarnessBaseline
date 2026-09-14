# Semantic Search — Mechanism Reference

The routing verdict (USE / NOT-for, composition order, result caveats) lives in `CLAUDE.md` §8. This file holds the mechanism behind the tool.

- **Engine:** DreB `semantic-search` plugin with a local C# tree-sitter chunker. Ranking is embedding similarity plus 6-signal POEM re-ranking.
- **Indexed:** `.cs` `.gd` `.md` `.tres` `.tscn` `.godot` `.json` `.yaml` `.toml` `.txt`. Skipped: binaries, `.uid`, `.import`, anything gitignored.
- **Index file:** `.search-index/search.db` (gitignored). Stale after edits; `/reindex_search` rebuilds it (auto in `/session_end`).
