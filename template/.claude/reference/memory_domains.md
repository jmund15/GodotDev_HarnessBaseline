# Memory Domain Table

Domain → memory search seeds → companion skills. **This is the documented home for the domain map
that `.claude/hooks/plan_memory_reminder.py` reads from `adaptation.json`** — a hook enforces, it
never legislates (`instruction_quality` §3 *Single source of truth*), so the rule it applies lives here
where `/rule_consistency` can see it.

**Load this when** you need the seed for a domain you are entering and the hook has not fired — it
fires only on a `.claude/plans/*.md` write. Routine recall does not need this file: search
`.claude/auto-memory/` semantically with a paraphrase of what you are about to do (CLAUDE.md §2
*Recall*), and the hot-tier index surfaces passively at SessionStart.

## Seeds

Search terms below are **query seeds for semantic search**, not exact-match keys. For broad
discovery, search facets separately rather than concatenating them.

| Domain | Triggers | Memory keywords | Skills | Rules |
|---|---|---|---|---|
| Obsidian/Docs | Obsidian, design doc, lore, vault | Obsidian | worklog_reference, obsidian_conventions | — |
