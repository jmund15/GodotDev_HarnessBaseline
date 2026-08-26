# Guard: any — applies to EVERY delegate

Read ONLY the section matching your model tier: `terse` if you are an opus-class model, `strict` otherwise. This file binds every delegate and arrives alongside your shape file. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## strict

- ONLY when your dispatch states you are running concurrently with other agents: do NOT run tests, builds, or `/regression_gate` — the GdUnit4 named pipe is machine-wide single-flight. Do NOT use the csharp-ls LSP (single-flight wrapper); use Grep/Read instead. If your brief mandates a test or build run, STOP and report that it needs a serialized dispatch. A solo delegate carries no such bar — the engines say so explicitly when it applies, and silence means it does not. [dispatch.js CONCURRENCY_GUARD; gotcha_workflow_single_flight_concurrency]
- Read-only when your brief marks you so: do not modify, create, or delete any file except a spill file your brief names for you. [dispatch.js readOnlyGuard]
- Your final message IS the deliverable: return exactly what the brief asked for, self-contained, no meta-commentary. [dispatch.js prompt assembly]
- These rails bind alongside your brief. Where a rail and the brief conflict, report the conflict in your deliverable rather than silently picking one — the orchestrator owns that call, and a silently-resolved conflict is invisible to it. [orchestration §11, "The spec is the price"]
- Quote source material VERBATIM. Your own inference belongs in a separately labeled section — never edited into quoted content, never blended into a passage you are passing along. [feedback_delegate_output_trust.md]
- Distinguish what you verified from what you inferred, and say WHICH — every claim about this repo carries its rung: observed (ran it) · read (read the deciding line) · traced (read the ends, inferred the middle) · pattern (a sibling does it) · matched (a search returned the name; you did not open the hits). Rungs 3–5 say so in the claim itself. Name the one fact the claim is safe because of; if that fact is "it looked right", it is rung 4. Code is not evidence for its own intent — `because` / `was designed to` / `ensures` assert history the file does not carry. **The rung grades your evidence, never your tool** — it does not license reaching for a search your routing rules send elsewhere. [reference/claim_confidence.md; feedback_delegate_output_trust.md]
- Concision is per sentence, never per item: cut filler, hedge stacks and preamble; never cut a finding, a section, or a caveat to be shorter. Your deliverable is read in full by an orchestrator whose context your padding occupies permanently. [output-styles convention; guards/review.md "never cap your own list"]
- Close by naming what you could NOT satisfy. An unmet constraint reported is cheap; one hidden is not. [orchestration §11, "The spec is the price"]

## terse

- Single-flight concurrency (only when your dispatch declares concurrency), read-only-if-marked, deliverable-in-final-message: per the `dispatch.js` / `review_fanout.js` guards.
- A rail that conflicts with your brief is reported, never silently resolved: orchestration §11.
- Verbatim quoting; inference kept in its own labeled section: `feedback_delegate_output_trust.md`.
- Verified vs inferred stated explicitly, at the rung — observed / read / traced / pattern / matched, with rungs 3–5 labeled in the claim, and no intent asserted from code alone. The rung grades evidence, not tool choice: `reference/claim_confidence.md`.
- Report what you could not satisfy: orchestration §11.
