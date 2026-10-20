---
description: Review tests, write tests under TDD, check coverage or assess testability; use `checklists:code_quality` for non-test production code.
---

# Shared Test Quality Checklist

Review every changed test file and every changed production file against these items.

When the godot layer is installed, the companion of this file (same name plus `_godot`) holds engine items. Read it too: each `## Godot <Section>` extends the section of that name below. Without it, only the items below apply.

**Prototype boundary:** a prototype that needs tests is no longer a prototype. Prototype code is out of test scope by definition; the moment a test is written against it, it is production-intent and the Logic-Domain Iron Law applies retroactively (testing skill, The Iron Law).

## Coverage

- [ ] **New public methods**: Every new public method has at least one test exercising it
- [ ] **Critical paths untested**: Error handling, edge cases, and boundary conditions without test coverage
- [ ] **Missing negative tests**: Validation logic without tests for invalid inputs
- [ ] **Horizontal slicing**: Tests authored layer-by-layer (all resources, then all systems) instead of behavior-by-behavior — detection: a layer's suite is green while no user-visible behavior is exercisable end-to-end (testing skill, Primary Observable Behavior (POB) Rule)
- [ ] **Lifecycle re-run**: a new or changed engine-lifecycle integration test cites two passing runs on one tree digest (testing skill, Verification rules)

## Quality

- [ ] **Implementation-coupled**: Tests assert observable outcomes, not internal state — detection: doubles or spies on internal collaborators, assertions on private state or on call counts of non-boundary methods. The tell is a test that breaks on a refactor that preserved behavior. A double of an internal collaborator also returns the caller's assumption, so a shape or contract mismatch passes green; require a proof that drives each such seam with the real collaborator
- [ ] **Tautological**: The assertion restates what the code says rather than what the behavior promises — detection: the expected value is recomputed with the production code's own arithmetic or constant, or the test name promises behavior the assertion never exercises. Expected values come from an independent source: a known-good literal, a worked example, the spec
- [ ] **No documentation-only tests**: Litmus — does every assertion consume a value produced by production code? Operands all derived from locally-constructed literals = documentation-only regardless of syntax, even with real setup
- [ ] **Test isolation**: No shared mutable state between tests (each test stands alone)
- [ ] **Owner-reported number**: an owner-reported number in the change's worklog item, checklist or task record has a test of the behaviour that value produces (an observed value or verdict may be asserted directly) whose `<summary>` names where the owner said it (testing skill, Verification rules)

## Curation

- [ ] **Subject selection**: The suite tests a reusable building block, OR is the sanctioned roster-wiring/representative-E2E slice for compositions — not a per-entity logic suite or per-instance resource pin (testing skill, Test Subject Selection)
- [ ] **Double already exists**: Any hand-rolled fake/spy/mock was checked against the project's shared test doubles first; second use of a private double promotes it there
- [ ] **Retirement rode along**: A change touching a suite retired its redundant/doc-only/duplicate coverage in the same change (testing skill, Retention & Curation Policy)

## Testability

- [ ] **Non-static pure functions**: Methods that don't use `this` but aren't marked `static` — harder to unit test and reason about
