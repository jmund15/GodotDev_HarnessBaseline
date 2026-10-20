---
name: session-start-hook-does-not-override-skill-procedure
description: "Session-start directives do not override active skill gates, including Socratic clarification and grouped review of taste-bearing or changed commitments before design-lock."
metadata: 
  node_type: memory
  type: feedback
---

Session-start hooks of the shape *"work without stopping for clarifying questions"* / *"make the reasonable call and continue"* govern **extra-procedural pauses** — places where, absent any active skill procedure, you would otherwise ask "should I do X or Y" outside a defined workflow. They do **NOT** override the load-bearing gates of an invoked skill's procedure. When the user types `/<skill>` they are paying for the procedure's gates; skipping them defeats the skill's value-add and reduces the agent to a fancy wrapper around whichever tool the skill happens to invoke (`write_doc` for brainstorming, `Edit` for an authoring procedure, etc.).

**Why:** 2026-05-12 brainstorming session — conflated the session-start hook with brainstorming Step 2 (*Socratic clarifying questions, one per turn*) and Step 5 (*section-by-section approval before moving on*). Skipped both gates, jumped Step 1 → Step 4 → `write_doc`, landing a v0.1 design doc the user had explicitly never been Socratically narrowed toward. The brainstorming skill's Anti-Patterns table names other rationalizations (enumeration-bypass, seed-idea-vagueness) but did **not** name this specific session-start-hook-as-license bypass vector — this entry is its canonical case. Multi-week prior commit history on the skill (`f4636f55`, `4204f79c`, `6d0277d4`, `b18b1a99`, `254bb462`) shows the user has been actively hardening this skill against bypass; my behavior was exactly what they were preventing. The directive in question has since been removed from the session-start hook, but the cross-system rule remains: any future hook with similar phrasing falls under the same scoping.

**How to apply:** When a session-start (or user-prompt-submit) directive seems to permit skipping a question or approval gate, follow the active skill's current procedure. Ask the Socratic questions it requires; review related commitments together, and obtain approval for taste-bearing or changed commitments before design-lock. Do not add section-by-section approval when the skill does not require it.
- *Would I ask this question whether or not the skill required it?* If yes, treat it as an extra-procedural pause and decide whether to continue.
- *Does the active skill require the question or gate?* If yes, follow that procedure unless its current rule gives an explicit exception.
- *Do the directives conflict and neither governs the case?* Raise the conflict rather than silently choosing.

Applies to active skill gates — a brainstorm skill's Socratic clarification, grouped commitment review/design-lock and divergence pipeline (uncertainty-flag + per-cluster pacing), an authoring skill's section approval, a refactor procedure's callers-first step, a debugging skill's phase discipline, a testing skill's strict TDD RED/VERIFY/GREEN.

Related: [[feedback_no_performative_agreement]] (acknowledge + investigate, no fix same turn), [[feedback_user_distress_lexicon]] (STOP-signal handling — relevant when the user reacts to a procedural bypass).
