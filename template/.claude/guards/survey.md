# Guard: survey — read/search/discovery delegates

Read ONLY the section matching your model tier: `strict` = sonnet / haiku / deepseek · `terse` = opus · fable receives none. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## strict

- `.claude/guards/any.md` §strict applies to you as well — read it too.
- PascalCase identifier on `.cs`: anchor-then-navigate, never a bare Grep of the name alone. **Concurrent dispatch (the common case — any fan-out of 2+): the csharp-ls LSP is BANNED, so the route is `Grep("class X\b"|"interface X\b" -g "*.cs")` to anchor, then Read.** Serialized single-agent dispatch only: continue from the anchor with LSP `documentSymbol`/`findReferences`. [CLAUDE.md §9]
- A question that genuinely needs the LSP (exhaustive call-site enumeration, `incomingCalls`) is a GAP you report, not a number you estimate — the caller re-runs it serially. An estimated count reads as an enumerated one and is worse than the admitted gap.
- PascalCase on `.tres`/`.tscn`/`.gd`/`.md`/`.godot`/`.json`/`.yaml`/`.toml`/`.txt`: route to semantic-search, not Grep. [CLAUDE.md §9]
- Grep stays correct for literal field values, UID hashes, regex alternation, attribute markers, comment scans, `using` directives, and as the anchor step before LSP. [CLAUDE.md §9]
- 3+ files, or one file over 400 lines, for synthesis: bundle into one `read_files(paths=[...], question=...)`. Never chain naive Reads for a synthesis answer. [CLAUDE.md §9]
- An empty Grep/Glob is NOT evidence of absence. Before reporting "not found", confirm with `ls` on the directory or `git ls-tree`. [gotcha_grep_glob_miss_tracked_files.md]
- Your searches see only the checked-out worktree. Check unmerged branches (`git branch -a`, `git grep <pattern> <branch>`) before concluding a feature does not exist. [gotcha_survey_absence_feature_lives_on_unmerged_branch.md]
- A directory missing from `git status` or Glob may be gitignored rather than absent — run `git check-ignore -v <path>` before calling it missing. [gotcha_gitignore_build_glob_swallows_production_dirs.md]

## terse

- `any.md` §terse applies; CLAUDE.md §9's routing table governs every read/search call.
- Absence is never proven by an empty search: `gotcha_grep_glob_miss_tracked_files.md`, `gotcha_gitignore_build_glob_swallows_production_dirs.md`, `gotcha_survey_absence_feature_lives_on_unmerged_branch.md`.
