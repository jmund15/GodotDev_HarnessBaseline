---
name: Invoke named slash-command skill, not manual equivalent
description: When the user references a named slash command (/plan_check, /regression_gate, /worklog) and it exists in the available-skills list, invoke it via the Skill tool — don't substitute a manually-executed equivalent procedure
type: feedback
originSessionId: e9092f23-933b-4528-b9e3-ec8a6b452b62
---
When a user references a named slash command (`/plan_check`, `/regression_gate`, `/worklog`, etc.) and that skill is in the available-skills list, invoke it via the Skill tool — even if an equivalent procedure was just executed manually in the same session.

**Why:** Skills produce canonical artifacts (formatted verdict header, tiered findings, named report sections, structured output) that the user and downstream workflows expect. Manual equivalents lose this structure even when they cover the same scope — the user sees prose where they expect a `╔═══ PLAN CHECK ═══╗` header, the next session's reader can't grep for the canonical section names, dashboard tooling can't parse the output. Substituting "I'll do the equivalent procedure manually" for the canonical skill silently degrades the artifact.

**How to apply:** When the skill is listed (always-available or surfaced via `<system-reminder>`), prefer Skill invocation. If a previous turn ran an equivalent manual procedure, re-invoking the named skill is still right — the canonical artifact is the deliverable. When the skill is genuinely not loaded and manual execution is the only option, name the substitution explicitly ("running the equivalent procedure manually since X skill isn't available in this context") so the divergence is auditable.

**Concrete:** 2026-05-17 plan-mode session, post-plan-amendment verification step. Ran a manual 2-agent Task dispatch covering the same scope as `/plan_check` (memory-gotcha audit + existing-abstraction discovery), then surfaced findings as inline prose. User had to explicitly request "please run the ACTUAL /plan_check" to get the canonical artifact + Phase 4 confirmation flow, despite the manual run having covered the same ground.
