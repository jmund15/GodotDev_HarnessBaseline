---
name: Session-end command over passive nudge
description: For registry/index/config drift detection, prefer a /session_end-conditional slash command with self-gating over a passive PostToolUse stderr nudge.
type: feedback
originSessionId: 232462ad-192f-44cd-b637-e19443c8e27a
---
For registry/index/config-drift detection, prefer a `/session_end`-conditional slash command (with internal git-diff signal-gating that no-ops when no relevant change happened) over a passive `PostToolUse` stderr nudge.

**Why:** Stderr nudges from `PostToolUse` hooks get walked past — empirically observed in the session this rule emerged from, multiple `[tool-routing]` accumulated-read nudges were ignored. Hooks also pay per-call I/O cost (re-parsing the registry file, classifying the command pattern) on every matching tool call, even when no signal is present. Session-end-conditional commands run once when state has actually changed, surface to a user who can act, and self-noop the rest of the time — strictly better cost profile + strictly better behavioral signal.

**How to apply:** When designing a self-maintenance mechanism, ask *"does the agent need to act mid-turn, or is end-of-session acceptable?"* If end-of-session works, route to a new `/session_end` phase invoking a slash command whose **Step 0** is a `git diff --name-status HEAD` signal gate (skip silently if no relevant change). Reserve `PostToolUse` hooks for things that MUST block or interrupt mid-session (e.g. `pattern_enforcer` rejecting forbidden patterns, `file_size_preblock` capping reads) — informational nudges don't meet the bar.

**Concrete:** 2026-05-13 `project_subsystems` rename refactor. First proposal wired a `subsystem_drift_nudge.py` PostToolUse(Bash) hook to detect new top-level folders. User pushed back, citing context bloat and the agent's empirically observed tendency to walk past stderr nudges. Replaced with `/sync_subsystems` invoked from `/session_end` Phase 4b, conditional on a Step 0 `git diff` gate detecting subsystem-shape or subsystem-density change. The slash command self-noops on every other session.
