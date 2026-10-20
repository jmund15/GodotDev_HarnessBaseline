# Phase 3 — Pattern Analysis

Read at Phase 3 of [`../SKILL.md`](../SKILL.md).

**Rule:** Find a working example before forming a hypothesis. If none exists, that is itself an investigation lead.

## Find working examples

- Every consumer of the type/method, via the C# navigation route — [`rules/csharp_lsp.md`](../../../rules/csharp_lsp.md) and CLAUDE.godot.md *Godot Docs and C# Navigation*.
- `Grep` for similar patterns (other spells calling the same method without failing).
- Diff: what does the working call site do that the failing one doesn't?

## Identify differences

- Code-level: argument order, missing `await`, null vs default.
- Data-level: `.tres` defaults, missing `[Export]` assignment in `.tscn`, UID drift.
- Lifecycle-level: caller running in `_EnterTree` vs `_Ready` vs `_Process`.

## Understand dependencies

**Rule:** Before fixing X, list everything that depends on X — via LSP `findReferences`, so the list is non-speculative. If your fix changes X's contract, every dependent breaks.
