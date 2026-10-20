---
name: gotcha_merge_silently_drops_one_sided_add
description: "A merge can drop a one-sided addition from the incoming side with no conflict marker, and the resulting tree is indistinguishable from a deliberate deletion — only a four-way blob comparison at the merge commit tells them apart."
metadata: 
  node_type: memory
  type: project
  modified: 2026-08-16T23:08:59.394Z
---

A merge that reports success can still lose content the incoming side added. There is no conflict
marker, no warning, and nothing in the resulting tree that distinguishes "the merge dropped their
addition" from "our branch deliberately deleted it" — both are just *branch content differs from
theirs*.

**Why:** every tree-level check compares two endpoints. Against the merge base, a merge-borne drop
and an intentional deletion produce the identical diff, so a whole-tree sweep classifies the drop as
a deliberate branch change and reports clean. `git log -- <path>` makes it worse: default history
simplification hides merge-borne changes entirely, so the file appears untouched by any commit even
though its content changed. The loss surfaces only when a test that depended on the dropped content
fails — and only if such a test exists.

**How to apply:** the four-way blob comparison at the *specific merge commit* is the only test that
separates the cases. For merge `M` with parents `P1` (ours) and `P2` (theirs) and base `B`:

| B | P1 (ours) | P2 (theirs) | M (result) | reading |
|---|---|---|---|---|
| 0 | 0 | **1** | **0** | theirs ADDED it, the merge dropped it — a silent loss |
| 1 | **0** | 1 | 0 | we deleted it deliberately; the merge honoured that |

Get the parents with `git rev-parse M^1 M^2` and the base with `git merge-base P1 P2`; compare either
blob hashes (`git rev-parse M:<path>`) or an occurrence count of the specific token. **Name the right
merge** — a branch that merges the target repeatedly has several candidates, and the check is only
repeatable if it is aimed at the one that actually lost the content. Reach for `git log --full-history`
(and `-S<token>` to pickaxe) when plain `git log -- <path>` shows no commit touching a file whose
content demonstrably changed; that silence IS the signature of a merge-borne change.

Two corollaries worth carrying:
- **A restored file is not a cleared branch.** Fixing the one file a failing test pointed at leaves
  every silently-dropped file that no test covers still missing. Re-run the comparison across the
  merge's whole changed set, not just the symptom.
- **Whole-tree "is my branch consistent with the target" sweeps cannot find this class**, so a clean
  result from one is not evidence of absence. Say so rather than reporting it as an all-clear.

Related: [[feedback_verify_git_state_before_starting_work]], [[gotcha_main_submodule_pointer_offmaster_orphan_debt]].
