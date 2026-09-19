# Memory Domain Table

Domain → memory search seeds → companion skills. **This is the documented home for the domain map
that `.claude/hooks/plan_memory_reminder.py` reads from `adaptation.json`** — a hook enforces, it
never legislates (`instruction_quality` §3 *Single source of truth*), so the rule it applies lives here
where `/rule_consistency` can see it.

**Load this when** you need the seed for a domain you are entering and the hook has not fired — it
fires only on a `.claude/plans/*.md` write. Routine recall does not need this file: search
`.claude/auto-memory/` semantically with a paraphrase of what you are about to do (CLAUDE.md §2
*Recall*), and the hot-tier index surfaces passively at SessionStart.

`adaptation.json` `memory_domains` (Design Doc §8) is the single source for the rows below; this
file's table is rendered from it by `baseline_sync.py compose`, never edited by hand. A project
adds or changes a domain by editing its `adaptation.json`, never this file's table directly.

## Seeds

Search terms below are **query seeds for semantic search**, not exact-match keys. For broad
discovery, search facets separately rather than concatenating them.

<!-- memory-domains-table -->

## Retired column — why there is no "Avoid (broad)"

The table carried an *Avoid (broad)* column warning that terms like `export` "matches >5 entities,
loads ~70KB". That described **keyword-matched memory entities**, a retrieval mechanism this harness
no longer uses: recall is semantic search over `.claude/auto-memory/`, where a broad term returns
ranked neighbours rather than dumping whole records. The warned-about failure cannot occur, so the
column was dropped 2026-08-17 rather than migrated.

The companion-skill column is likewise **not** reproduced in CLAUDE.md: skill descriptions are
auto-injected every session with their own trigger and SKIP text, so a domain→skill mapping there is
a second copy of something already in context (`/claudemd_compact` M5). It is kept here because
`adaptation.json` holds it as data, not human-readable prose, and this rendered table is its
documented home.
