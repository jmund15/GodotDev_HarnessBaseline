# Logic Domain — strict TDD, red flags and rationalizations

Read before writing or changing Logic-Domain production code; named from `SKILL.md` §Recipe index.

**Scope:** Logic Domain only (CLAUDE.md Project Guidelines' Domain Split names its subsystems). Gameplay has its own rubric — `reference/choosing.md` §Gameplay Domain flow, and CLAUDE.md *Development Philosophy: Hybrid TDD* owns the split.

## Logic Domain flow

```
1. RED    → [TestCase] defines expected behavior
2. VERIFY → Run to confirm failure
3. GREEN  → Minimum code to pass
4. REFACTOR
```

## The Iron Law (Logic Domain only)

```
NO PRODUCTION LOGIC-DOMAIN CODE WITHOUT A FAILING TEST FIRST
```

Production logic-domain code written before the test: **delete it and start over.** Don't "adapt" it while writing tests — you will rationalize back into the existing implementation.

**Retroactive trigger:** phrasing implying code already exists ("let me finish this," "get this working first," "I've already spent X hours") — **STOP**. The Iron Law fires retroactively; delete the implementation before any test work begins.

## Rationalizations to refuse

**Refusal stance for every row:** state the rule as non-negotiable, then prescribe the action. Don't justify it technically under pushback; don't frame Logic-Domain TDD as a tradeoff. Writing "the reason this is better is..." — **STOP**.

Catching yourself thinking any of these ⇒ **stop, delete, restart with a failing test**. The other seven excuses are tabled under `## strict` at the end of this file:

| Excuse | Reality |
|---|---|
| "Already manually tested" | Ad-hoc ≠ systematic. No record, can't re-run on the next change. |
| "Deleting X hours of work is wasteful" | Sunk cost — non-negotiable, not a tradeoff. Unverified code is debt; the hours are spent either way. |

**Coverage deferral is not an option:** lifting coverage for the logic you're modifying is part of the current task — a later sweep or a next-PR promise is not a plan, and the debt compounds.

## Stop signals

If the test feels hard to write, **listen to the test**: hard-to-test usually means hard-to-use, which means the design needs simplification. Don't power through — let the test drive the design.

## Cross-references

- `feedback_strict_tdd_for_integration_regressions.md` — even when domain classification says "Gameplay," if the bug class IS the integration (hot-loop, race, BB-flag-soup, perception-staleness), write the seam-level integration test BEFORE shipping.
- `reference/choosing.md` anti-pattern sections — how Logic-Domain tests fail in practice once written.
- `debugging` skill Phase 5 — surviving record of the Wave-2 hot-loop domain-misclassification case.

## strict

Read this section only if your session tier line says `strict`.

### Rationalizations to refuse — the remaining rows

| Excuse | Reality |
|---|---|
| "Too simple to test" | Simple code breaks. The test takes 30 seconds. |
| "I'll test after" | Tests written after pass immediately — that proves nothing. If code exists, delete it before any test work; do not adapt. |
| "Keep as reference, write tests first" | You'll adapt it. That's testing-after with extra steps. |
| "Tests after achieve the same goals" | Tests-after document existing behavior including bugs; tests-first specify intended behavior. Descriptive, not specificational. |
| "TDD will slow me down" | TDD is faster than debugging. The shortcut is the long way around. |
| "Existing code has no tests" | You're changing it — tests for the logic you modify ship in the same change, not a future sweep. |
| "It's a small refactor" | If logic changes, behavior changes. Test the change. |
