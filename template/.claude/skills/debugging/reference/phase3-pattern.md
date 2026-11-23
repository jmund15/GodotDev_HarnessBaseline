# Phase 3 — Pattern Analysis

Read at Phase 3 of [`../SKILL.md`](../SKILL.md).

**Rule:** Find a working example before forming a hypothesis. If none exists, that is itself an investigation lead.

## Find working examples

- Every consumer of the type or method, through the language's reference search where the project has one, else `git grep -n`.
- Similar call sites that use the same method without failing.
- Diff: what does the working call site do that the failing one doesn't?

## Identify differences

- Code-level: argument order, missing `await`, null vs default.
- Data-level: authored defaults, an unassigned configuration slot, a stale identifier.
- Lifecycle-level: the caller runs in a different phase (construction, initialization, per-frame or per-request).

## Understand dependencies

**Rule:** Before fixing X, list everything that depends on X from a reference search, so the list is not a guess. If your fix changes X's contract, every dependent breaks.
