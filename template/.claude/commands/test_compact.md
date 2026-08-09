---
description: Audit the test suite for duplication and structural debt; propose compaction.
disable-model-invocation: true
---

Audit the test suite for duplication and structural debt; propose targeted compaction.

Takes no arguments — always audits the whole `Tests/` tree.

## Constraints
- **Retention Policy**: Keep all behavior tests. Delete only when: feature removed, test is genuinely redundant, or test checks implementation details (not behavior). See [Testing Skill](/.claude/skills/testing/SKILL.md).
- **Verification**: Full gate BEFORE and AFTER (Steps 1 and 5). Every count change must be attributable to an approved removal.
- **User Approval**: Propose ALL changes before executing. Group by category for efficient review.

## Procedure

### 1. BASELINE — Capture current test metrics

```bash
pwsh -NoProfile -File .claude/scripts/regression_gate.ps1 -NoBaselineUpdate
```
Run with `run_in_background: true` (the suites exceed the Bash 600000ms ceiling). Take per-suite counts from the `SUITE` lines. `-NoBaselineUpdate` is required here — a baseline capture must not ratchet.

Record the three counts and note any pre-existing failures; **do NOT fix them here** (separate task). A `VERDICT` other than `PASS` means you have no trustworthy baseline — resolve it before scanning, or the Step-5 comparison is meaningless.

**Reuse a gate run from earlier in this session if the tree hasn't changed since** — the counts are the same and the suites cost ~9 minutes.

### 2. SCAN — Identify compaction candidates
Search the `Tests/` directory for these categories, in priority order:

**Category A — Dead Weight (Safe to Remove)**
- `DoSkip = true` tests without an active reason or linked issue
- Tests for features confirmed removed (grep for missing types/methods)
- Diagnostic-only tests that log data but assert nothing meaningful
- Constant-mirroring tests that assert a field equals its default/constant value — these break on intentional changes and can never catch real bugs. Test behavioral consequences instead.
- The compiler-guaranteed / storage-only shapes (isinstance, enum ordinal, constructor-stores-field, bool set-get) — table and carve-outs in [Testing Skill](/.claude/skills/testing/SKILL.md). **Apply the carve-outs and the deletion gate**; matching on shape alone deletes real coverage.
- Repetitive fallback boilerplate: 3+ tests exercising the same "returns fallback" path with different null configurations → consolidate into one `[TestCase]`-parameterized test. Parameterize rather than inlining sequential asserts — a hard assert aborts the method, so inlining loses which permutation broke, and a dropped permutation is the usual consolidation defect.
- Stale NOTE/TODO comments referencing completed work

**Category B — Duplicates (Merge or Remove)**
- Files testing identical behavior at the same domain level (e.g., singular vs plural naming)
- Individual `[TestCase]` methods that are strict subsets of existing parameterized `[TestCase(a,b)]` rows
- Tests duplicated across Logic and Integration domains without added value at both levels

**Category C — Structural Consolidation (Refactor)**
- **Interface contract boilerplate**: Repeated `Implements_IFoo` / `Provision_ReturnsSelf` patterns → consolidate into parameterized `[DataPoint]` tests
- **Mock deduplication**: Private mock classes duplicated across files → extract shared mocks to `Tests/Framework/Mocks/`. **Do not hand-scan for these — enumerate them mechanically:**
  ```bash
  python3 .claude/hooks/duplicate_test_double_guard.py --json
  ```
  The guard groups by the base being doubled (`private sealed partial class FakeX : Node, IX` is attributed to `IX`, never `Node`) and buckets each family: **blocking** (at 3+ copies AND grown past `duplicate_test_double_baseline.json` — your change added one), **grandfathered** (at 3+ but committed as backlog), **advisory** (below the fail threshold). `Tests/Framework` is exempt — a double living there is the resolution, not a finding.

  Prioritize families with **no baseline entry at all** — those are wholly new duplication, not inherited debt. Consolidate by promoting one double to `Tests/Framework/Mocks` as a public double parameterised over whatever the variants differ on, then delete the copies. **After consolidating, rerun with `--write-baseline`** to lock the reduction in; the baseline only ever ratchets down through this command.

  > New files under `Tests/Framework/Mocks/` are untracked. A pathspec-scoped commit that stages only the edited test files silently drops the new mock and breaks the build for everyone else — stage the mock too (`feedback_pathspec_commit_stage_infra_deps.md`).
- **Setup boilerplate**: Repeated `new SpawnContext { ... }` or archetype loading → extract factory methods into fixtures/builders
- **Event assertion pattern**: `bool fired = false; comp.Event += () => fired = true; ...` → extract helper if 5+ instances

**Category D — File Organization (Low Priority)**
- Single-test files that naturally belong in an adjacent suite (merge only if same domain and same SUT)
- Misplaced files (Logic tests in Integration folder or vice versa)

**Category E — Test Hygiene (Performance & Stability)**
*Unlike A–D, this category MAY change test behavior (adding cleanup, removing attributes). Extra verification required.*
- **Missing orphan cleanup**: Tests creating Godot node types (`new HitboxComponent3D()`, etc.) without `QueueFree()` or `Free()` in teardown or `[AfterTest]` → add cleanup to prevent crash accumulation in batch runs. **NOTE:** Adding `[AfterTest]` with `GodotObject.Free()` requires `[RequireGodotRuntime]` at the CLASS level (GdUnit0500)
- **Missing `using` on ISceneRunner**: `ISceneRunner.Load()` calls without `using` keyword → add `using` to prevent memory leaks
- **`[RequireGodotRuntime]` removal — DO NOT.** Never strip this attribute from tests using `Vector3`, `Blackboard`, or any Godot-defined type. The GdUnit0501 analyzer enforces it at build time, so removal is a compile error. See `archive_gdunit4_lifecycle_attributes.md`.

**Category F — Production Resource Coupling (Fragility Audit)**
*Tests loading production `.tres`/`.tscn` files are fragile — designer tuning changes can break tests at any time.*
Scan ALL test files for `res://` paths that point OUTSIDE `res://Tests/`. Classify each by fragility:

- **FRAGILE (value assertions)**: Tests that `GD.Load` a production `.tres` and assert on specific numeric values (damage multipliers, priorities, stat values, health amounts, counts). These WILL break when designers rebalance. **Action:** Replace with frozen test data in `Tests/Fixtures/Data/` or remove the value assertion.
- **FRAGILE (config guards)**: Tests asserting configuration correctness that prevents game-breaking bugs (e.g., `DoNotInherit == true` on MultiShot, collision system config). These are *intentionally* fragile safety nets. **Action:** Keep, but document as intentional. Consider moving the guarded values to constants or comments so the intent is clear.
- **MODERATELY FRAGILE**: Tests asserting on types, identity names, or category membership from production data. **Action:** Evaluate case-by-case — some are legitimate integration smoke tests.
- **LESS FRAGILE (structural)**: Tests loading production scenes only to verify they instantiate without crashing, or using them as scaffolds for behavioral testing. **Action:** Generally acceptable for Integration/Sanity domains. Flag only if a test fixture equivalent exists.

**Key patterns to grep for:**
```
GD.Load.*res://(?!Tests/)           # Direct loads outside Tests/
ResourceLoader.Load.*res://(?!Tests/) # ResourceLoader loads
ISceneRunner.Load.*res://(?!Tests/)  # Scene runner loads
"res://(?!Tests/)                    # Any production res:// path string
```

**Special attention:** Check `Tests/Framework/Fixtures/` for centralized production path dictionaries (e.g., `GameplayTestFixture.ArchetypePaths`). These are coupling multipliers — every test inheriting from the fixture is transitively coupled.

**Correct pattern (already in use):** `Tests/Fixtures/Data/` contains frozen test archetypes (`test_light_arch.tres`, `test_medium_arch.tres`, …) with known stat values. New tests should use these instead of production resources.

**No-op exit:** If zero candidates surface across all categories A–F, report `Suite is compact — no action.` and exit before Phase 3.

### 3. REPORT — Present findings grouped by category
For each candidate, provide:
- **File path** and **test method name(s)**
- **Category** (A/B/C/D/E)
- **Proposed action** (Delete / Merge into X / Extract to Y / Parameterize / Add cleanup / Remove attribute)
- **Behavioral impact**: What coverage is preserved vs lost (should be NONE)

Format as a markdown table per category for efficient review.

### 4. EXECUTE — Apply approved changes
After user reviews and approves (may approve per-category):
- Apply changes incrementally by category
- `dotnet build` after each category — a red build localizes to that category
- If a count drops by more than the removals you approved, STOP and investigate

### 5. VERIFY — Final comparison

```bash
pwsh -NoProfile -File .claude/scripts/regression_gate.ps1 -NoBaselineUpdate
```

Compare per-suite counts against the Step-1 baseline. Report: tests removed, merged, parameterized, hygiene fixes applied, net change. Confirm zero behavioral coverage lost.

**This command is the one workflow that legitimately shrinks the suite, so read the gate's ratchet correctly:**

- `Tests/regression_baseline.json` **only ever ratchets up automatically.** A negative delta is never auto-applied — that rule exists so a real regression can't quietly lower the bar.
- A small compaction (under 10% of a suite) still reports `VERDICT=PASS` with a negative delta and leaves the baseline untouched. That is not "clean": the committed baseline now overstates the suite, and the gap persists into every future run, eating the Tier-2 WARN margin.
- So after an approved compaction, **lower the baseline deliberately**: confirm the drop equals exactly the removals you approved, then write the new counts and re-stamp `updated_on_commit`. Never fold this into a run that also has unexplained losses.
- A compaction large enough to cross `warn_ratio` (0.90) returns exit 3 / `VERDICT=WARN`. That is expected here, not a failure — acknowledge it with the user rather than re-running.
- Consolidating doubles (Category C) usually leaves counts flat; parameterizing (Category A) reduces method count while preserving cases. Predict the expected delta per category *before* running, and treat any unexplained difference as a lost test.

## Scope Exclusions
- Do NOT refactor production code (only test code)
- Do NOT fix pre-existing test failures (separate task)
- Categories A–D: Do NOT change test behavior or assertions — only structure and organization
- Category E: MAY change test attributes and add cleanup code, but MUST NOT change assertions or test logic
- Do NOT touch `Tests/Framework/` fixtures unless extracting shared mocks INTO them
