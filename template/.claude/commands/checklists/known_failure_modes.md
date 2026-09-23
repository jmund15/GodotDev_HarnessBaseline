---
description: >-
  Auto-load when investigating bugs, reviewing plans for landmines, or designing in
  well-trodden areas — a "have we hit this before?" lookup. Triggers: "is this a known
  failure", "have we seen this", "any past failures", "what could go wrong here",
  "regression class", "known landmine". SKIP for forward-looking compliance review (use
  `checklists:code_quality` or `checklists:test_quality` instead).
---

# Known Failure Modes — Case-History Index

<!-- Case-history catalog of memorialized regression patterns. -->
<!-- Sibling to code_quality.md / test_quality.md but DIFFERENT in kind: -->
<!--   * code_quality.md / test_quality.md = forward-looking reviewer rubrics (loaded into every audit agent's CONTEXT). -->
<!--   * known_failure_modes.md = backward-looking incident catalog (fetched ON-DEMAND, per entry). -->
<!-- Used by: /plan_check (plc-memory-alignment, always); /explore (exp-memory, floor lens). /session_audit is NOT a consumer — its `--include-failure-history` flag never existed. -->
<!-- PROJECT-OWNED SEED: the access contract ships; the entries are yours. An empty catalog is fine at project start. -->
<!-- Entry bodies live in `.claude/reference/known_failure_modes_entries.md` and are reached ONLY through `tools/kfm.py`. This file is the access contract; it stays small so invoking it costs nothing. -->
<!-- Maintenance: append-only via autolearn. When a new file-based memory entry codifies a recurring failure, autolearn proposes a corresponding entry there. Manual sweeps not needed. -->

The catalog maps memorialized failure patterns to **detection signals** critic agents use during code/plan review. Each entry is a one-screen lookup: incident → grep/LSP/structural signal → memory entry for full context → trigger scenario.

## Access — scan all, fetch few

The read pattern is scan every entry's trigger, read the body of the few that bear. `.claude/tools/kfm.py` serves those layers separately, so a reviewer never pays for every body to use a few. The index is generated at read time, never stored — a stored index drifts the first time autolearn appends without updating it.

| Command | Emits | Size |
|---|---|---|
| `python3 .claude/tools/kfm.py index` | one line per entry — `#ID name — trigger`, grouped by section | — |
| `python3 .claude/tools/kfm.py get <ID> [<ID> ...]` | full bodies of the named entries | — |
| `python3 .claude/tools/kfm.py get -s <section-substring>` | full bodies of one whole section | — |
| `python3 .claude/tools/kfm.py next-id` | lowest unused ID; flags duplicates | — |

Fetching is cheap; a missed entry is the failure this catalog exists to prevent. Fetch every trigger that reads ambiguous, and `get -s` the whole section when the work sits squarely inside one section's domain. Read `reference/known_failure_modes_entries.md` directly only when editing entries.

This is **case-history**, not a rubric. If you're looking for "what rules should this code follow?", read `code_quality.md` and `test_quality.md`. If you're looking for "what subtle past failure does this pattern resemble?", fetch from here.

The auto-memory files named on each entry's `**Memory**:` line are the canonical source of truth — catalog entries are *pointers + detection patterns*, not summaries.

**Memory-file resolution:** bare filenames on `**Memory**:` lines resolve under `.claude/auto-memory/` (hot tier) or `.claude/auto-memory/archive/` (cold tier); the very newest entries may live only in the harness user-scope memory dir (`~/.claude/projects/<project-slug>/memory/`) until consolidated. "Retired graph entity — no file" means the entry here is the surviving record.

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
4. Reference auto-memory source by file (preferred — `feedback_*.md` etc.) or by graph entity name (when no file exists).
5. Assign the next unused ID — `python3 .claude/tools/kfm.py next-id`. Never cite a remembered or cached maximum; duplicate IDs poison numeric citations in plan_check prompts.

