---
name: Don't compress Socratic when prompt is rich
description: Rich front-loaded context (brainstorm prompt, /plan_part briefing, design doc) is starter material — NOT a license to skip a workflow's validation steps (Socratic phase, Plan Mode Phase 2 agents). Run them in full with personal verification.
type: feedback
originSessionId: 8459bfff-658d-4555-8a92-a8af8156825e
---
When a brainstorm prompt (e.g. `/architecture_brainstorm`) front-loads candidate patterns, class kinds, wiring questions, or named options, do NOT collapse the Socratic phase into a "user already pre-loaded most of this" shortcut. Run the full one-question-per-turn Socratic — even if questions feel partially answered by the prompt.

**Why:** The richness of the prompt is the user pulling raw material together; the brainstorm's job is to convert that into *verified, narrowed, reasoned* design commitments. Compressing Socratic on the grounds that "the user already framed it" loses:
- the verification step (is the user's framing actually load-bearing? does code match?)
- the divergence step (are there options the user didn't list that beat their A/B/C/D?)
- the narrowing step (which option for THIS class kind, with explicit reasoning vs all alternatives?)

Pattern-matching against starter context produces shallow recommendations that ratify the prompt instead of stress-testing it.

**How to apply:** Treat every clarifying question, named option, and open question in the prompt as a *seed* for a Socratic question, not a pre-answered fact. Verify each against actual code/memory before reflecting it back. If a question genuinely is resolved by the prompt + code reality, state that explicitly with the evidence — don't silently skip.

**Generalizes beyond Socratic:** the same shape recurs in Plan Mode — a rich `/plan_part` briefing is NOT a license to skip Plan Mode Phase 2 (Plan-agent dispatch). Any workflow with a verification phase downstream of front-loaded context (Socratic narrowing, Plan-agent validation, audit passes) must run that phase regardless of how complete the input looks. The richer the input, the stronger the *pull* to skip — and the easier it is to rationalize.

**Concrete:** During the 2026-05-17 `arch-rng-injection-patterns` brainstorm, agent proposed to "compress Socratic Step 2" because the prompt named Patterns A/B/C/D, 4 class kinds, and 4 wiring questions. User corrected: "I do NOT want you to skip/reduce step 2 UNLESS personally verified and justified. This is just a starter context to help, but you need to do your own reasoning and thorough review."

**Concrete:** 2026-05-20 `arch-rng-injection-patterns` P1 plan — agent drafted the plan solo after a rich `/plan_part` briefing, skipping Plan Mode Phase 2. User: "you shouldve ran the plan agent workflow, why did you not." The dispatched Plan agent then caught a stranded production consumer (`EncounterRuntime` raw `ToString()` seed-key) + a dead test seam that solo drafting missed.

**Related:**
- `feedback_session_start_hook_does_not_override_skill_procedure.md` — same shape at a different layer (hook ≠ procedural skip license; rich prompt ≠ Socratic skip license).
- `feedback_resolve_questions_in_plan_not_execution.md` — verification > deferral pattern.
