---
name: separate-preexisting-changes-before-commit
description: Bulk-mechanical commits — verify every working-tree change is yours via an edit-signature detector; session-start git status can miss pre-existing work.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 2579f7bd-b562-4c93-bc71-5854ee89b61f
---

The session-start `git status` is a point-in-time snapshot that can miss pre-existing uncommitted work in the tree. Before committing **bulk mechanical changes** (mass renames, structure audits, codemods, path/namespace rewrites), do NOT trust that every dirty file is yours.

Verify with an **edit-signature detector**: your codemod has a narrow, machine-checkable signature (e.g. only `res://` path strings or one namespace token changed). Grep added (`+`) diff lines that do NOT match that signature — any hit flags a pre-existing edit to exclude.

```
for f in $(git diff --name-only -- '*.cs'); do
  git diff -U0 -- "$f" | grep '^+' | grep -vE '^\+\+\+' | grep -vE '<your-signature>'
done
```

Then stage `git add -A` and `git reset` the exclude-set — never blind-commit `git add -A` after a bulk operation.

**Why:** Prevents fusing unrelated in-progress work into your commit (a logically-incoherent commit that's painful to split later).

**How to apply:** When a session's changes are provably mechanical, the signature filter turns an intractable "which of N changes are mine?" into a precise exclude list. Pair with the harness rule "before deleting/committing, look at the target."

**Signal:** 2026-05-26 structure_audit — session-start status showed only `? Jmodot`, but the tree held a Sanity-charter refactor + ThrustAttackAction test work; the detector isolated 7 pre-existing files from 224 audit changes cleanly. Related: [[refactor-parity-audit]].
