---
name: Refactor parity audit before merge
description: When code is deleted or replaced, compare old behavior with the replacement before merge.
type: feedback
---

When a refactor retires code, audit old behavior against the replacement before merge.

1. Enumerate deleted or replaced files from git history.
2. List each retired export, public method, lifecycle hook, subscription, engine side effect, state
   write, and emitted signal.
3. Point each item to equivalent replacement behavior or an approved removal in the PR description.
4. Scan changed code for `deferred`, `TODO`, `FIXME`, `follow-up`, `stub`, `not yet wired`,
   `no-op until`, and `placeholder`. A hit that replaces retired behavior blocks merge.
5. Playtest subjective behavior that automated checks cannot observe.

A new architecture does not excuse dropped wiring. “Varies per project” also does not excuse a
replacement that silently omits the old consumer seam.
