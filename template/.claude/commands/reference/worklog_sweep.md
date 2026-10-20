---
disable-model-invocation: true
---

# `/worklog` — SWEEP

Read this file during `/session_end` Phase 6. Each pass runs the named operation's own recipe: `worklog_add.md`, `worklog_complete.md`, `worklog_promote_unblock.md`.

## Operation: SWEEP

Three passes over the recent session, each confirmation-driven — never auto-apply.

**Add-sweep:** scan the transcript for missed trigger phrases (`worklog_reference` — both regular-deferral and Future-Scope catalogs). Propose `Add to Worklog: <title> — <domain> · <class> · scope <n>?` (or `Add to Worklog Future Scope: ...` on a strong-trigger phrase), citing the turn it appeared in. On `y` (or `y, <override>`), run ADD.

**Completion-sweep:** read Active and Ledger, then inspect commits back to each item's named Source or last decision when deciding whether it shipped or became stale; record the checked interval. Compare with `git status --short` and the session's tool calls. For each plausibly resolved item propose `Mark complete: <title> (<commit-ref>)?`. On `y`, run COMPLETE.

**Promotion-sweep (Future Scope ripening):** read `## Future Scope`. For each `FSLINE`, search from its recorded `added YYYY-MM-DD` creation stamp unless a later verified prerequisite assessment records complete coverage since then. Use session start only when that interval covers the relevant event; record the searched range. If no creation stamp exists, report that the item has no verified search start. On specific evidence that a prerequisite cleared, propose `Promote from Future Scope: <title> — <matched evidence>?`. On `y`, run PROMOTE. On `n` / `n, still parked`, skip silently.

Require a specific identifier or independent context that genuinely links an item to a change; skip generic words. Surface all supported proposals in a scannable batch. If the user stops, record the remainder as unaddressed; never treat an unshown item as assessed.
