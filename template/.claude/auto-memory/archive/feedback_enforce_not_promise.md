---
name: feedback-enforce-not-promise
description: "A promise of future compliance is not a guard — when behavior recurs despite doctrine, ship deterministic enforcement (hook), not a restated promise"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 1575807a-659b-4f41-9af8-9997b17e978c
  modified: 2026-08-14T23:39:12.931Z
---

When the same bad behavior recurs after doctrine was written, the correction is not another promise — it is an enforcement arm that makes the behavior impossible or self-correcting (a PreToolUse deny hook with an actionable reason, a committed wrapper, a lint gate). The user (2026-08-14) rejected "won't happen again" after the git-commit heredoc form recurred twice despite the CLAUDE.md §Shell Discipline rule.

**Why:** prose doctrine depends on the agent remembering it at emission time; a PreToolUse hook fires deterministically on every call.

**How to apply:** on any repeated violation of an established rule, ship the enforcement hook/guard in the same pass as the acknowledgment. Related: [[gotcha-auto-mode-classifier-fail-closed]].
