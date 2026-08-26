# Memory Domain Table

Domain → memory search seeds → companion skills. **This is the documented home for the domain map
that `.claude/hooks/plan_memory_reminder.py` encodes as `DOMAINS`** — a hook enforces, it never
legislates (`instruction_quality` §3 *Hook-only rules*), so the rule it applies lives here where
`/rule_consistency` can see it.

**Load this when** you need the seed for a domain you are entering and the hook has not fired — it
fires only on a `.claude/plans/*.md` write. Routine recall does not need this file: search
`.claude/auto-memory/` semantically with a paraphrase of what you are about to do (CLAUDE.md §2
*Recall*), and the hot-tier index surfaces passively at SessionStart.

`plan_memory_reminder.py` mirrors this table. Keep the two in sync; never add a domain to the hook
alone.

## Seeds

Search terms below are **query seeds for semantic search**, not exact-match keys. For broad
discovery, search facets separately rather than concatenating them.

| Domain | Search seed | Companion skill |
|---|---|---|
| Testing | "testing" or "GdUnit4" | `testing` |
| Exports | "Inspector" or "RequiredExport" | — |
| Refactoring | "refactor" | `refactor_procedure` (+ LSP for callers) |
| Debugging | "debugging" or "diagnose" | `debugging` |
| HSM / States | "HSM" or "transition" | — |
| Status Effects | "status effect" | `jmodot`, `status_effect_authoring` |
| MCP Tools | "MCP" or "UID" | — |
| Godot Physics | "physics" or "collision" | — |
| Godot Lifecycle | "disposal" or "lifecycle" | — |
| Data Files | "UID" | `architecture_philosophy` |
| Design Philosophy | "architecture", "design", or "modifier" | `architecture_philosophy` |
| Pooling | "pool" or "spawn" | — |
| AI / NPCs | "critter", "NPC", or "steering" | — |
| Spell Architecture | "spell" or "synergy" | `architecture_philosophy`, `spell_authoring` |
| Crafting | "ingredient" or "craft" | `spell_authoring` |
| VFX | "VFX" or "Modulate" | `vfx_patterns` |
| Jmodot / Framework | "Jmodot" | `jmodot` |
| Obsidian / Docs | "Obsidian" | `obsidian_conventions`, `worklog_reference` |
| Prototyping | "provisional" or "probe mode" | `prototype` |

## Retired column — why there is no "Avoid (broad)"

The table carried an *Avoid (broad)* column warning that terms like `export` "matches >5 entities,
loads ~70KB". That described **keyword-matched memory entities**, a retrieval mechanism this harness
no longer uses: recall is semantic search over `.claude/auto-memory/`, where a broad term returns
ranked neighbours rather than dumping whole records. The warned-about failure cannot occur, so the
column was dropped 2026-08-17 rather than migrated.

The companion-skill column is likewise **not** reproduced in CLAUDE.md: skill descriptions are
auto-injected every session with their own trigger and SKIP text, so a domain→skill mapping there is
a second copy of something already in context (`/claudemd_compact` M5). It is kept here because the
hook encodes it and the hook needs a documented home.
