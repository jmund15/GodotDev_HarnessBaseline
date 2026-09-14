---
name: gotcha_session_start_submodule_autofix_detaches_head
description: "A post-resume compile error in untouched consumer code can come from the wrong submodule revision, not the file named by the compiler."
metadata: 
  node_type: memory
  type: project
  modified: 2026-09-11T06:29:35.575Z
---

A restored checkout can compile against a different framework revision than the work expects. The error names the consumer of the missing API, not the operation that changed the dependency revision. Check the intended branch and gitlink before editing that consumer.

**Why:** In an earlier startup implementation, automatic submodule synchronization moved a branch checkout to the parent's recorded SHA and detached HEAD. The branch retained the work, but the consumer then compiled against an older framework API. This is historical evidence, not a claim about the current startup hook; inspect its live policy before attributing a new failure to it.

**How to apply:** Compare `git -C Jmodot log --oneline -1`, the intended branch, and the parent's gitlink. Confirm the mismatch before choosing recovery; do not blindly update or switch a shared checkout.

Related: [[feedback_verify_git_state_before_starting_work]], [[archive_worktree_submodule_gotcha]].
