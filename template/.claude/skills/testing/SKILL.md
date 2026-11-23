---
name: Testing
description: >-
  Use to decide what to test, write a test first, judge whether a pass counts, or curate a suite.
  SKIP when to run which check (change_control).
---

# Testing

This skill holds the stack-neutral half: what deserves a test, test-first work, when a pass counts and how a suite stays fast. The stack half (runner commands, framework API, fixtures, the project's suites) is project files; the repo's `CLAUDE.md` build-and-test section names them. When each check runs is `change_control`'s.

| At this step | Read |
|---|---|
| Choosing a subject, a level, or what to delete from a suite | `skills/testing/reference/choosing.md` |
| Writing production code in a domain the project marks test-first | §Test first, below |

## Test first

The repo's `CLAUDE.md` names the domains where a failing test comes before the code. In them, code written before its test is deleted and restarted, not adapted: a test written against existing code passes at once and specifies whatever the code already does, bugs included. The order is red, confirm it fails for the expected reason, the least code that passes, then refactor. Phrasing that implies the code already exists means the deletion comes first.

A test that is hard to write is reporting a design that is hard to use: simplify the design rather than force the test.

| Excuse | Answer |
|---|---|
| "Too simple to test" | Simple code breaks, and the test is quick. |
| "Already tested by hand" | A hand run leaves no record and cannot re-run on the next change. |
| "I'll test after" / "keep it as reference" | A test written after passes at once and proves nothing; a kept draft gets adapted. |
| "Deleting this work is waste" | The hours are spent either way; unverified code is debt. |
| "The existing code has no tests" | Tests for the logic you change ship in the same change, not a later sweep. |

The domain of the file does not decide this. When a change prevents a known integration failure (an ordering race, a restart loop, stale state between components), a test that drives that seam comes first, whatever domain the files sit in. Litmus: if the change reintroduced the bug, would the suite catch it? "Only a manual check would" means write the seam test first.

## When a pass counts

- **The run names what it ran.** A filter that matched nothing, a skipped suite or a count far below the usual is no pass. Check the count and the list of tests run, not the exit code alone.
- **A test that drives a runtime lifecycle** (frames, spawned processes, a browser, awaited events), or a change to that lifecycle's timing or order, passes twice on the same tree before its pass counts. Two agreeing runs are the smallest sample that shows nondeterminism; a disagreeing pair is the defect (`debugging`).
- **A test that fails, then passes on a rerun, is not repaired.** It failed for a cause (wall-clock waits, shared state, load). Fix the cause, or delete the test when nothing it guards still matters.
- **An owner-reported number** (an authored value, an observed value, a verdict) becomes a test of the behaviour that value produces; an observed value or a verdict may be asserted directly. The test names where the owner said it. Never invent the number: without one, ask, or the behaviour stays owner-checked.

## Every test earns its run

A test names the failure it targets and why nothing cheaper catches it. A deterministic test runs in-process in well under a second. A slow suite is a symptom: too many tests, redundant ones, over-computational ones, or ones run more often than their value needs. Fix it at that cause: delete a case a cheaper one covers, rewrite a heavy case in-process, move a valuable heavy case to a scheduled run. Never trim a suite to meet a time.

Never leave a test broken, related to your work or not: a red suite hides whether the next change broke something.
