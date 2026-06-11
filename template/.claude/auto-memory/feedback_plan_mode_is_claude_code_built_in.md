---
name: Plan Mode is a Claude Code built-in
description: Local skills/commands document the handoff to Plan Mode, never Plan Mode internals. Zero references to EnterPlanMode/ExitPlanMode in `.claude/` is intentional.
type: feedback
originSessionId: 10c65425-68c7-4266-a24a-b35e9a15e00d
---
**Plan Mode is a Claude Code built-in feature**, not a PP-local skill or command. Verified by exhaustive grep: zero references to `EnterPlanMode` or `ExitPlanMode` exist anywhere in `.claude/commands/` or `.claude/skills/`. The only mentions live inside session-archive JSON files (recording user actions), not as documented harness behavior.

**Implication for skill / command authoring:**

- ✅ Reference Plan Mode as an external downstream tool. Example: *"After approval, the user can enter Plan Mode for formal implementation planning OR proceed inline OR invoke `/worklog plan`."*
- ❌ Do NOT document Plan Mode's procedure, gestures, or internals locally. The canonical docs live in Claude Code itself.
- ✅ Skills can describe the *handoff*: what they hand off to Plan Mode (e.g., the `brainstorming` skill produces a design doc; Plan Mode reads it and produces an implementation plan).
- ❌ Skills MUST NOT call Plan Mode on the user's behalf. Plan Mode is user-invoked.

**Why this matters:** documenting Plan Mode locally creates two sources of truth for a feature whose canonical docs live elsewhere. As Claude Code evolves Plan Mode, local docs would silently drift.

**Caught:** 2026-04-29 verification pass before authoring the `brainstorming` skill. Original plan said "two-tier brainstorming → Plan Mode workflow"; verification surfaced that Plan Mode has no local representation, so the skill must describe the handoff abstractly rather than referencing local Plan Mode docs.

**Three-tier composition** (codified in `brainstorming/SKILL.md`):
```
Brainstorming  →  Plan Mode  →  Implementation
(this skill)     (Claude Code   (write code,
                  built-in)      run tests,
                                 commit)
```

**Source:** distilled from Batch C readiness verification of the superpowers-cherry-pick adoption. Adopted 2026-04-29.
