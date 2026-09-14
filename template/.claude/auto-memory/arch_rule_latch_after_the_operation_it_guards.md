---
name: arch-rule-latch-after-the-operation-it-guards
description: A do-once latch set before the operation it guards permanently admits whatever failed
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-18T05:54:56.936Z
---

A "run this once" memo — a `HashSet.Add`, a `_validated` bool, a dictionary insert — must be set
AFTER the guarded operation returns, never as the condition that triggers it.

`if (seen.Add(x)) { Validate(x); }` records `x` as seen, then validates. If `Validate` throws or
returns early, `x` stays recorded, so every later call takes the already-seen branch and skips the
check forever. The guard converts a loud, repeatable failure into a silent one-time warning, which
is the exact opposite of what a validation latch is for.

**Why:** the set-membership test and the memo write look like one atomic idea in that idiom, so the
ordering hazard reads as absent. It only appears once the guarded operation can fail — which is
often a LATER change, so the idiom is correct when written and becomes wrong when someone promotes
a warning to a throw. That makes it a review blind spot: the diff that introduces the bug does not
touch the latch.

**How to apply:** write it as `if (!seen.Contains(x)) { Operation(x); seen.Add(x); }` whenever the
operation can throw, log an error, or bail. When promoting any one-shot check from warning to throw,
grep for the latch that gates it and confirm the write happens after. Related:
[[feedback_fix_the_class_not_the_instance]].
