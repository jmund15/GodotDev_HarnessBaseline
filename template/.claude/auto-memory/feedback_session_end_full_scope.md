---
name: Session-end pipeline must scope to full session, not recent turn
description: /session_end audit/autolearn/self_evaluate must use the compaction summary as authoritative session scope, not just the post-compaction conversation.
type: feedback
originSessionId: f15a43c9-44a8-4801-b2d7-593b97f8e5e8
---
When `/session_end` runs after a compaction, the compaction summary IS the session
context for prior turns. Audit, autolearn, and self_evaluate must scope to the
**entire session** (compaction summary + post-compaction conversation), not just
the recently-visible turns.

**Why:** Sessions that span a compaction commonly contain the bulk of the work
*before* the compaction (multi-track plan execution, multiple user corrections,
~50+ tests). Scoping the audit to just the post-compaction conversation:
- Misses substantive code-quality concerns (audit reads only the latest sliver)
- Misses durable learnings that appeared as mid-session corrections
  (autolearn extracts only one rule when there should be 3-4)
- Mis-classifies the session as "Pattern C clean" when corrections actually
  occurred (self_evaluate logs zero corrections when the compaction summary
  records several)
- Runs too narrow a regression filter (Logic-only when fireball/spell/combat
  changed)

**How to apply:** At the start of `/session_end` Phase 1, read the compaction
summary (if any) and `git diff HEAD` + submodule diff + untracked-file list to
enumerate the **full session delta**, not the recent-turn delta. Treat the
compaction summary's "Files and Code Sections", "Errors and fixes", and "All
user messages" lists as authoritative for audit scope, autolearn corrections
mining, and self_evaluate's `corrections` array.

Caught 2026-05-01: scoped /session_end to just the cadence/cap tail-turn of
a multi-track Tier-1 Fireball session that had ~51 tests, ~10 new types, and
multiple architectural corrections pre-compaction.

**Phase 7 corollary — working-tree cleanliness, not only-files-I-edited:**
/session_end's Phase 7 invariant is that the working tree ends clean.
"Editor co-author noise" (Godot re-saving 100+ tracked files when opened
mid-session: load_steps strips, project.godot line reorders, .translation
re-encodings) IS session work even if I didn't intend the edits — it
occurred during the session's lifespan. Give it its own categorical
`chore(godot): sweep editor re-save noise` commit (per the existing
precedent on `main`) rather than excluding it from session scope. Caught
2026-05-02: previous /session_end left 132 unstaged tracked files because
I treated "I didn't intentionally edit these" as a valid exclusion filter.
It's not — anything tracked-and-modified at end-of-session belongs in a
commit (feature, fix, or chore), full stop.

**Execution-fidelity corollary — "MANDATORY" means what it says:** Command
files that mark sub-agent dispatch as MANDATORY/MUST/CRITICAL multiple times
are not negotiable. Inline-execution ("I read the files myself, surfaced
findings, here's the report") is a procedure violation, NOT a faster
substitute.

**Empirical signal:** Informal inline audit produced 4-6 findings across
~33 session-modified files. The same scope under formal 3-agent dispatch
(`sa-design-semantics` opus + `sa-robustness-performance` opus +
`sa-intuitiveness-testability` sonnet, three batches) produced 44 findings
including 4 CRITICAL bugs (pool-reset event-wipe scope, shared-snapshot
generation mutation, pre-Ready Stop NRE, Visual-drop on damage rebuild).
Consistent with "agents read, inline skims" — each agent has dedicated
context for one lens; orchestrator self-execution time-shares context
across all three lenses and skims under pressure.

**How to apply:**
- When a command file mandates sub-agent dispatch, spawn the agents in a
  single parallel message per the agent-file templates. No sequential, no
  consolidation, no inline-substitution. The 3-agent architecture is
  load-bearing.
- If scope exceeds the 20-.cs-file cap, ask once for batch strategy and
  proceed under the selected protocol.
- Caught 2026-05-02: third correction in same session — "this is ridiculous
  and shows you didn't actually follow instructions AT ALL" — after I had
  already failed twice on the same pipeline. The procedure was not flawed;
  the execution was. Don't redesign; execute.
