---
name: don-t-defer-immediately-addressable-work-to-the-worklog
description: "If you can do it now with no bad consequences, DO IT NOW. The worklog is for unavoidable rabbit-trails and git-topology-gated items, not a deferral bin."
metadata:
  node_type: memory
  type: feedback
  originSessionId: 84adc6c1-1ab3-4b4c-ade9-5bed49091669
---

When you notice a small change you could make right now without derailing the main session, **do it now**. Don't propose a worklog add as a substitute. This applies doubly to scope-1 items — they are trivial *by definition*, so the propose→confirm→write-Obsidian→write-mirror→read-next-session→eventually-do cost dwarfs just doing the fix now.

**Why:** The worklog exists for unavoidable rabbit-trail items that would derail main work if executed inline. Spurious adds clog the mirror, accumulate cognitive overhead, and create a false sense of progress (item-logged-not-done feels like progress, isn't). The recurring failure this prevents: treating "I noticed this" as automatically equivalent to "I should defer this."

**Do-it-now examples** (never propose-to-log these): untracked files referenced as authoritative doctrine (`git add` inline), one-line typo fixes, simple terminology sweeps, stale doc references, obvious missing comments.

**How to apply:** Before any worklog-add proposal, ask: (a) Can I do this now without derailing main work? (b) Are there any bad consequences? If `yes / no` → do it now.

**When the worklog IS appropriate:**
- Items requiring user judgment.
- Items whose scope would derail (>1 file / >30 min / multi-decision).
- Items needing later-phase information.
- Items the user explicitly asks to log.
- **Git-topology-gated items** — the one case a scope-1 item legitimately waits: when the answer to *"can I just do this now in the current branch / worktree?"* is "no, because of git topology." Examples: "bump submodule pointer on main after this PR merges" (gated on merge order); "cherry-pick this fix to release/2.x" (branch-specific); anything requiring a worktree context the current session lacks. Put the specific git-topology reason in the proposal text.

Companion: [[feedback_audit_in_plan_mode]].
