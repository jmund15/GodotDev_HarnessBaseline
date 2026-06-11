---
name: arch-rule-test-namespace-matches-gate-filter
description: "Test directory names AND namespaces must match one of regression_gate's three filter prefixes (Logic/Integration/Sanity) — any other top-level Tests/ folder is silently un-gated"
metadata: 
  node_type: memory
  type: project
  originSessionId: be7b1f7e-2ff8-4ef7-8d20-c1c5fbe62ecd
---

Tests under `Tests/<Folder>/` use namespace `{{PROJECT_NAME}}.Tests.<Folder>.*`, and `regression_gate`'s suite filters are `~Tests.Logic`, `~Tests.Integration`, `~Tests.Sanity`. **Any other top-level folder name is silently un-gated** — tests there compile and run when invoked manually, but never appear in the regression gate's pass/fail counts and never block commits.

**Why:** `Tests/Gameplay/Encounters/*` (37 tests) shipped never-gated on this branch. Six production-NRE failures (root cause: `EncounterRuntime` didn't propagate Definition to runtime-instantiated roots) went uncaught for a full session and only surfaced when a Gameplay-filtered pass was run independently. The prior session's "regression gate: 6048/291/27, all green" was technically true but materially incomplete — the broken tests existed but weren't in any suite the gate covers.

**How to apply:**
- When creating a new test file, choose its parent folder to align with one of the three filter prefixes: `Tests/Logic/`, `Tests/Integration/`, or `Tests/Sanity/`. The namespace must follow suit.
- If you genuinely need a fourth category, two parallel changes are required: (a) add the filter to `.claude/commands/regression_gate.md`'s suite-run loop, AND (b) add a baseline entry under `Tests/regression_baseline.json`'s `suites:` map. Don't ship the folder without both.
- Litmus before creating `Tests/<NewName>/`: would `dotnet test --filter "FullyQualifiedName~Tests.<NewName>"` show in any of the gate's three filter calls? If no → either rename, or add the filter+baseline pair.
- Reviewer-facing rule: any PR that adds a top-level `Tests/<X>/` folder for `X ∉ {Logic, Integration, Sanity}` is a merge-blocker until either renamed or the gate is extended.

**Concrete (2026-05-17):** `Tests/Gameplay/Encounters/` had 37 tests across 7 files. Running with the Gameplay filter surfaced 9 failures (6 new, 3 pre-existing) that the Logic/Integration/Sanity filters never touched. Fix landed as a folder rename (`Tests/Gameplay/Encounters/` → `Tests/Integration/Encounters/`) + namespace update, bringing Integration baseline 291 → 328. No new filter was added because the contents were genuinely integration-shape tests mis-categorized.

**Verified:** 2026-06-04 memory-claim audit — `regression_gate.md` ll.80-82 carries exactly three suite filters (`~Tests.Logic` / `~Tests.Integration` / `~Tests.Sanity`); no other top-level `Tests.*` prefix is run. Un-gating mechanism confirmed by reading the gate's filter strings.

Related: [[feedback_session_end_full_scope]] (the gate-aggregation problem at a different layer — both reduce to "if it's not in the gated count, it's not gated"). [[reference_worklog_system]] (similar architectural-coverage-gate hygiene).
