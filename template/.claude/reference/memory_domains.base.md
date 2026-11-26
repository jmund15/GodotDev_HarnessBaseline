# Memory Domain Table

Domain → memory search seeds → companion skills. **This is the documented home for the domain map
that `plan_memory_reminder.py` reads from `adaptation.json`**: it matches the files a plan declares against `Paths`
(gitignore-style globs; a row with none never fires on a plan). A hook enforces, it never legislates (`instruction_quality` §3 *Single source of truth*), so the rule it applies lives here
where `/rule_consistency` can see it. `memory_domains.md` is composed from `memory_domains.base.md` and
`skills/project_subsystems/adaptation.json`: edit those, then run `python3 .claude/tools/baseline_sync.py compose`.

**Load this when** you need the seed for a domain you are entering and no reminder has fired: the plan
reminder fires only on a `.claude/plans/*.md` write whose declared files a row owns. Routine recall does not need this file: search
`.claude/auto-memory/` semantically with a paraphrase of what you are about to do (`CLAUDE.core.md`
*Memory*), and the hot-tier index surfaces passively at SessionStart.

## Seeds

Search terms below are **query seeds for semantic search**, not exact-match keys. For broad
discovery, search facets separately rather than concatenating them.

<!-- memory-domains-table -->
