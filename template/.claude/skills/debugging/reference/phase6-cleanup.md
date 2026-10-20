# Phase 6 — Cleanup + Post-mortem

Read at Phase 6 of [`../SKILL.md`](../SKILL.md).

Required before declaring done:

- [ ] **Original repro no longer reproduces**: re-run the Phase 1 loop.
- [ ] **Regression test passes** (or absence of seam is documented in the Worklog).
- [ ] **All `[DIAG-...]` instrumentation removed**: `Grep` pattern `\[DIAG-` across source files, delete each line. Restore the Debug switch if you flipped it.
- [ ] **Throwaway fixtures / harnesses deleted** (or moved to a clearly-marked fixtures location with a comment).
- [ ] **The hypothesis that turned out correct is stated in the commit / PR message**, so the next debugger learns.
- [ ] **If the root cause is in a commit made before this task's work began, append one escape row:** `python3 .claude/tools/escape_log.py add --writer debugging --sha <that commit> --should-have-caught <mandate key or none>` (`review_ledger.py keys` lists the keys; `--help` lists the rest).

## Post-mortem to auto-memory (mandatory if non-obvious)

If the correct hypothesis surfaced a non-obvious gotcha that would bite again, save it: memory is the project's durable searched surface, commit messages are not. **Litmus:** *"Would forgetting this cause another debugging session like this one?"* → Yes = Memory. What to save and where it goes: `CLAUDE.core.md` §2 *Memory (One Store, Two Tiers)*; `/codify` owns placement details.

## Architectural recommendation, after the fix

An architectural cause goes to the Worklog as `arch` after the fix: [`phase5-fix.md`](phase5-fix.md) §An architectural cause goes to the Worklog as `arch`.
