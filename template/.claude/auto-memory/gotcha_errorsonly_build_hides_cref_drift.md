---
name: errorsonly-build-hides-dangling-cref-comment-drift
description: "-consoleLoggerParameters:ErrorsOnly suppresses CS1574 + all warnings, so a doc-comment <see cref> to a removed member ships green. Grep the name in comments after removing a member."
metadata: 
  node_type: memory
  type: project
  originSessionId: e3900932-7a6f-4fc5-a912-bc227a3104f9
---

The project build (`dotnet build -consoleLoggerParameters:ErrorsOnly`, per
[[feedback_comment_discipline]] and the build-gotcha archive) suppresses **every
compiler warning**, including **CS1574** (XML-doc `<see cref="X"/>` cannot be
resolved). Consequence: when a refactor or mid-plan design pivot **removes a
member** that a sibling `<summary>`/`<see cref>` still references, the build stays
green, `/regression_gate` stays green, and the **doc comment silently lies** — it
names a member that no longer exists.

**Why:** Concrete incident — the charge-duration migration (`3d1ca0d5`) added then
removed `[Export] float ChargeDuration` from `ChargeBehavior` when the auto-parity
design (runner-base-as-fallback) replaced it. `ResolveChargeDuration`'s summary kept
`Falls back to the per-archetype <see cref="ChargeDuration"/> export` — a dangling
cref **and** a false fallback claim (the real fallback is the `exportFallback`
parameter). Build + gate were both green; only a `/session_audit` line-precision
diff read caught it. No automated gate sees this class.

**How to apply:** After removing/renaming a member during a refactor, `Grep` its bare
name across the touched files' **comments** (`<see cref="Name"`, `<paramref`, and prose
mentions) before committing. Prefer `<paramref name="..."/>` over `<see cref="...">`
when documenting a parameter's role — paramref can't dangle the way a cref to a deleted
member can. This is the doc-comment sibling of
[[feedback_consume_new_apis_or_migration_is_incomplete]] (audit call sites after an
API change) and [[feedback_refactor_parity_audit]] (behavior diff before merge) —
same "the migration isn't done until the trailing references are reconciled" shape,
applied to documentation rather than code.
