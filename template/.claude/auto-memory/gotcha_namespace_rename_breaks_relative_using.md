---
name: namespace-rename-breaks-relative-using
description: "Renaming a C# file's namespace can break a bare `using X;` that resolved via the old namespace's ancestry — and cascade into misleading CS0535 errors."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 2579f7bd-b562-4c93-bc71-5854ee89b61f
---

Renaming a C# file's `namespace` can silently break a bare `using X;` that only resolved because C# searches *enclosing* namespaces. A file under `{{PROJECT_NAME}}.Spells.*` with `using Foundation;` binds `{{PROJECT_NAME}}.Spells.Foundation`; move it to `{{PROJECT_NAME}}.Visual.*` and that lookup fails (CS0246).

**The trap:** when the now-unresolved type sits in an **interface method signature**, the root CS0246 in the interface cascades into misleading **CS0535 "does not implement member"** errors on *every* implementer — the build points at the implementers, not the real cause.

**Fix:** before/after any namespace move (R13 structure-audit alignment), fully-qualify ancestry-dependent usings: `using {{PROJECT_NAME}}.Spells.Foundation;`. This makes the file location-independent.

**Why this matters:** R13 namespace renames are a recurring audit fix; the misleading CS0535 cascade can send you chasing the implementers instead of the moved interface. When a namespace move yields a burst of "doesn't implement" errors, read the moved file's CS0246 first.

**Concrete:** `ISpellOneShotEffect` ns `{{PROJECT_NAME}}.Spells.Visual.OneShot` → `{{PROJECT_NAME}}.Visual.OneShot`, 2026-05-26 structure_audit — broke `using Foundation;`, produced 1 CS0246 + 5 cascading CS0535. Related: [[separate-preexisting-changes-before-commit]].
