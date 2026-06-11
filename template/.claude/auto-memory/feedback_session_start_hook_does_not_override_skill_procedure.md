---
name: session-start-hook-does-not-override-skill-procedure
description: "Session-start \"no clarifying questions\" / \"make the reasonable call\" hooks govern extra-procedural pauses only; they do NOT excuse skipping a skill's load-bearing Socratic / section-by-section gates (e.g., architecture_brainstorm Step 2 + Step 5, idea_brainstorm Step 3 divergence pipeline)."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 18ee6400-24e7-4c7e-a5bd-51c7abbd0371
---

Session-start hooks of the shape *"work without stopping for clarifying questions"* / *"make the reasonable call and continue"* govern **extra-procedural pauses** — places where, absent any active skill procedure, you would otherwise ask "should I do X or Y" outside a defined workflow. They do **NOT** override the load-bearing gates of an invoked skill's procedure. When the user types `/<skill>` they are paying for the procedure's gates; skipping them defeats the skill's value-add and reduces the agent to a fancy wrapper around whichever tool the skill happens to invoke (`write_doc` for brainstorming, `Edit` for status_effect_authoring, etc.).

**Why:** 2026-05-12 brainstorming session — conflated the session-start hook with brainstorming Step 2 (*Socratic clarifying questions, one per turn*) and Step 5 (*section-by-section approval before moving on*). Skipped both gates, jumped Step 1 → Step 4 → `write_doc`, landing a v0.1 design doc the user had explicitly never been Socratically narrowed toward. The brainstorming skill's Anti-Patterns table names other rationalizations (enumeration-bypass, seed-idea-vagueness) but did **not** name this specific session-start-hook-as-license bypass vector — this entry is its canonical case. Multi-week prior commit history on the skill (`f4636f55`, `4204f79c`, `6d0277d4`, `b18b1a99`, `254bb462`) shows the user has been actively hardening this skill against bypass; my behavior was exactly what they were preventing. The directive in question has since been removed from the session-start hook, but the cross-system rule remains: any future hook with similar phrasing falls under the same scoping.

**How to apply:** When a session-start (or user-prompt-submit) directive seems to permit skipping a question, and an active skill explicitly mandates that question, **the skill's procedure governs**. Litmus to disambiguate:
- *"Would I ask this question whether or not the skill required it?"* → If yes, the session-start hook governs — decide and continue.
- *"Is this a procedural gate the active skill explicitly mandates (e.g., 'Socratic Q', 'section-by-section approval', 'write the failing test FIRST')?"* → If yes, the skill governs — ask anyway.
- *"Are the two directives in direct conflict where neither obviously dominates?"* → Raise the conflict to the user; don't silently pick. The two surfaces cover different cases, so a genuine conflict is a signal that one of them needs tightening.

Applies to all skills with load-bearing procedural gates — `architecture_brainstorm` Step 2 / Step 5, `idea_brainstorm` Step 3 (divergence pipeline; uncertainty-flag + per-cluster pacing), `status_effect_authoring` section approval, `refactor_procedure` LSP-callers-first, `debugging` 4-phase discipline, `testing` strict TDD RED/VERIFY/GREEN. Skill ancestry note: the original `brainstorming` skill that motivated this entry was split on 2026-05-12 into the two phase-specific skills above; the principle is unchanged.

Related: [[feedback_no_performative_agreement]] (acknowledge + investigate, no fix same turn), [[feedback_user_distress_lexicon]] (STOP-signal handling — relevant when the user reacts to a procedural bypass).
