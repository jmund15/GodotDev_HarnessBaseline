---
description: Look up past failure patterns while diagnosing a bug, reviewing a plan or designing; use `checklists:code_quality` or `checklists:test_quality` for forward-looking compliance review.
---

# Known Failure Modes — Case-History Index

The catalog maps memorialized failure patterns to **detection signals** critic agents use during code/plan review. Each entry is a one-screen lookup: incident → grep/LSP/structural signal → memory entry for full context → trigger scenario.

## Access — scan all, fetch few

Reach entry bodies only through `.claude/tools/kfm.py`; read `reference/known_failure_modes_entries.md` directly only when editing. Scan every entry's trigger, fetch the body of each ambiguous one, and `get -s` the whole section when the work sits inside one. A missed entry is the failure this catalog exists to prevent. The index is generated at read time, never stored, because a stored index drifts the first time autolearn appends.

| Command | Emits |
|---|---|
| `python3 .claude/tools/kfm.py index` | one line per entry — `#ID name — trigger`, grouped by section |
| `python3 .claude/tools/kfm.py get <ID> [<ID> ...]` | full bodies of the named entries |
| `python3 .claude/tools/kfm.py get -s <section-substring>` | full bodies of one whole section |
| `python3 .claude/tools/kfm.py next-id` | lowest unused ID; flags duplicates |

The `kfm.py` docstring owns the flags.

The auto-memory files named on each entry's `**Memory**:` line are the canonical source of truth — catalog entries are *pointers + detection patterns*, not summaries.

**Memory-file resolution:** a bare filename on a `**Memory**:` line resolves under `.claude/auto-memory/` (hot), then `.claude/auto-memory/archive/`. An entry whose Memory line names a retired entity with no file is the surviving record. The store itself is owned by [`CLAUDE.core.md`](../../CLAUDE.core.md) §Memory.

**Entry IDs are unique but not ordered** (entries append per-section, so numbers do not ascend within a section). Resolve ambiguous numeric citations by entry name.

---

## How to add an entry (autolearn)

When `/autolearn` introduces a new file-based memory entry that codifies a recurring failure, append a corresponding entry to `.claude/reference/known_failure_modes_entries.md`. The proposal should:

1. Match the five-line entry format exactly — `kfm.py` parses it, and a malformed entry silently vanishes from the index:
   ```
   ### <ID>. <failure name>
   **Incident**: <date — one-line summary>
   **Detection**: <grep regex | LSP query | structural signal>
   **Memory**: `<auto-memory filename>.md`
   **Catches you when**: <the scenario, first clause first — kfm.py truncates the index trigger at the first ` — ` or sentence end>
   ```
2. Include a concrete detection signal — regex, LSP query, or structural pattern. If no detection signal exists, the entry isn't ready (the value of this catalog is the detection layer, not the prose).
3. Place under an existing `## ` section. Add a new section if none fits — `kfm.py` groups the index by whatever sections exist.
4. Reference the auto-memory source by file (`feedback_*.md` etc.).
5. Assign the next unused ID — `python3 .claude/tools/kfm.py next-id`. Never cite a remembered or cached maximum; duplicate IDs poison numeric citations in plan_check prompts.

