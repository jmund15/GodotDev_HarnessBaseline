---
description: >-
  Auto-load when reviewing tests, writing tests under TDD, checking coverage, or assessing
  testability. Triggers: "review the tests", "TDD this", "are these tests good", "coverage
  check", "is this testable", "write the test first". SKIP for non-test production code
  (use `checklists:code_quality`).
---

# Shared Test Quality Checklist
<!-- Derived from Testing Skill. Sync when Skill updates. -->
<!-- Used by: /review_pr (test-analyzer), /session_audit (sa-intuitiveness-testability) -->

Review every changed test file and every changed production file against these items.

## Coverage

- [ ] **New public methods**: Every new public method has at least one test exercising it
- [ ] **Critical paths untested**: Error handling, edge cases, and boundary conditions without test coverage
- [ ] **Missing negative tests**: Validation logic without tests for invalid inputs

## Quality

- [ ] **Behavioral over implementation**: Tests assert observable outcomes, not internal state checks (e.g., test spell behavior, not private field values)
- [ ] **No documentation-only tests**: `AssertThat(true).IsTrue()` or equivalent gives false confidence — test real behavior
- [ ] **Test isolation**: No shared mutable state between tests (each test stands alone)

## Testability

- [ ] **Lifecycle-trapped logic**: Business logic embedded in Node lifecycle methods (`_Ready`, `_Process`) that could be extracted as pure static for unit testing without `[RequireGodotRuntime]`
- [ ] **Non-static pure functions**: Methods that don't use `this` but aren't marked `static` — harder to unit test and reason about
