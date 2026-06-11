---
disable-model-invocation: true
---

Audit the test suite for duplication and structural debt; propose targeted compaction.

Tests accumulate organically during TDD — periodic compaction keeps the suite navigable.

## Constraints
- **Retention Policy**: Keep all behavior tests. Delete only when: feature removed, test is genuinely redundant, or test checks implementation details (not behavior). See [Testing Skill](/.claude/skills/testing/SKILL.md).
- **Verification**: Run the FULL suite BEFORE and AFTER changes. Compare pass counts. Zero regressions.
- **User Approval**: Propose ALL changes before executing. Group by category for efficient review.

## Procedure

### 1. BASELINE — Capture current test metrics
Run the full suite in batches (Logic, Integration, Sanity) using the Testing Skill invocation rules — always include `--filter`/`--settings .runsettings`, never `--no-build`, Bash `timeout=600000`. Record:
- Total test count per domain
- Total pass/fail/skip counts
- Note any pre-existing failures (do NOT fix them here — separate task)

### 2. SCAN — Identify compaction candidates
Search the `Tests/` directory for these categories, in priority order:

**Category A — Dead Weight (Safe to Remove)**
- `DoSkip = true` tests without an active reason or linked issue
- Tests for features confirmed removed (grep for missing types/methods)
- Diagnostic-only tests that log data but assert nothing meaningful
- Constant-mirroring tests that assert a field equals its default/constant value — these break on intentional changes and can never catch real bugs. Test behavioral consequences instead.
- Stale NOTE/TODO comments referencing completed work

**Category B — Duplicates (Merge or Remove)**
- Files testing identical behavior at the same domain level (e.g., singular vs plural naming)
- Individual `[TestCase]` methods that are strict subsets of existing parameterized `[TestCase(a,b)]` rows
- Tests duplicated across Logic and Integration domains without added value at both levels

**Category C — Structural Consolidation (Refactor)**
- **Interface contract boilerplate**: Repeated `Implements_IFoo` / `Provision_ReturnsSelf` patterns → consolidate into parameterized `[DataPoint]` tests
- **Mock deduplication**: Private mock classes duplicated across files → extract shared mocks to `Tests/Framework/Mocks/`
- **Setup boilerplate**: Repeated `new SpawnContext { ... }` or archetype loading → extract factory methods into fixtures/builders
- **Event assertion pattern**: `bool fired = false; comp.Event += () => fired = true; ...` → extract helper if 5+ instances

**Category D — File Organization (Low Priority)**
- Single-test files that naturally belong in an adjacent suite (merge only if same domain and same SUT)
- Misplaced files (Logic tests in Integration folder or vice versa)

**Category E — Test Hygiene (Performance & Stability)**
*Unlike A–D, this category MAY change test behavior (adding cleanup, removing attributes). Extra verification required.*
- **Missing orphan cleanup**: Tests creating Godot node types (`new HitboxComponent3D()`, etc.) without `QueueFree()` or `Free()` in teardown or `[AfterTest]` → add cleanup to prevent crash accumulation in batch runs. **NOTE:** Adding `[AfterTest]` with `GodotObject.Free()` requires `[RequireGodotRuntime]` at the CLASS level (GdUnit0500)
- **Missing `using` on ISceneRunner**: `ISceneRunner.Load()` calls without `using` keyword → add `using` to prevent memory leaks
- ~~**`[RequireGodotRuntime]` removal**~~: **DO NOT** attempt to remove this attribute from tests using Vector3, Blackboard, or any Godot-defined type. GdUnit0501 analyzer enforces it at build time — removal causes compile errors. See Memory: `GdUnit4_Gotchas`.

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

**Correct pattern (already in use):** `Tests/Fixtures/Data/` contains frozen test archetypes (`test_light_arch.tres`, `test_medium_arch.tres`) with known stat values. New tests should use these instead of production resources.

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
- Build after each category (`dotnet build`)
- Run full suite after all changes to verify zero regressions
- If any test count drops unexpectedly, STOP and investigate

### 5. VERIFY — Final comparison
- Run full suite in batches
- Compare pass counts to baseline from Step 1
- Report: tests removed, tests merged, tests parameterized, hygiene fixes applied, net change
- Confirm zero behavioral coverage lost

## Scope Exclusions
- Do NOT refactor production code (only test code)
- Do NOT fix pre-existing test failures (separate task)
- Categories A–D: Do NOT change test behavior or assertions — only structure and organization
- Category E: MAY change test attributes and add cleanup code, but MUST NOT change assertions or test logic
- Do NOT touch `Tests/Framework/` fixtures unless extracting shared mocks INTO them
