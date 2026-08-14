# Guard: any — applies to EVERY delegate

Read ONLY the section matching your model tier: `strict` = sonnet / haiku / deepseek · `terse` = opus · fable receives none. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## strict

- ONLY when your dispatch states you are running concurrently with other agents: do NOT run tests, builds, or `/regression_gate` — the GdUnit4 named pipe is machine-wide single-flight. Do NOT use the csharp-ls LSP (single-flight wrapper); use Grep/Read instead. If your brief mandates a test or build run, STOP and report that it needs a serialized dispatch. A solo delegate carries no such bar — the engines say so explicitly when it applies, and silence means it does not. [dispatch.js CONCURRENCY_GUARD; gotcha_workflow_single_flight_concurrency]
- Read-only when your brief marks you so: do not modify, create, or delete any file except a spill file your brief names for you. [dispatch.js readOnlyGuard]
- Your final message IS the deliverable: return exactly what the brief asked for, self-contained, no meta-commentary. [dispatch.js prompt assembly]
- These rails bind alongside your brief. Where a rail and the brief conflict, report the conflict in your deliverable rather than silently picking one — the orchestrator owns that call, and a silently-resolved conflict is invisible to it. [CLAUDE.md §Model Delegation, "The spec is the price"]
- Quote source material VERBATIM. Your own inference belongs in a separately labeled section — never edited into quoted content, never blended into a passage you are passing along. [feedback_delegate_output_trust.md]
- Distinguish what you verified from what you inferred. Never state an unverified claim as established fact. [feedback_delegate_output_trust.md]
- Close by naming what you could NOT satisfy. An unmet constraint reported is cheap; one hidden is not. [CLAUDE.md §Model Delegation, "The spec is the price"]

## terse

- Single-flight concurrency (only when your dispatch declares concurrency), read-only-if-marked, deliverable-in-final-message: per the `dispatch.js` / `review_fanout.js` guards.
- A rail that conflicts with your brief is reported, never silently resolved: CLAUDE.md §Model Delegation.
- Verbatim quoting; inference kept in its own labeled section: `feedback_delegate_output_trust.md`.
- Verified vs inferred stated explicitly: `feedback_delegate_output_trust.md`.
- Report what you could not satisfy: CLAUDE.md §Model Delegation.
