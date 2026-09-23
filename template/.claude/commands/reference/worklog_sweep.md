---
disable-model-invocation: true
---

# `/worklog` — SWEEP

Read this file during `/session_end` Phase 6. Each pass runs the named operation's own recipe: `worklog_add.md`, `worklog_complete.md`, `worklog_promote_unblock.md`.

## Operation: SWEEP

Three passes over the recent session, each confirmation-driven — never auto-apply.

**Add-sweep:** scan the transcript for missed trigger phrases (`worklog_reference` — both regular-deferral and Future-Scope catalogs). Propose `Add to Worklog: <title> — <domain> · <class> · scope <n>?` (or `Add to Worklog Future Scope: ...` on a strong-trigger phrase), citing the turn it appeared in. On `y` (or `y, <override>`), run ADD.

**Completion-sweep:** read Active and diff its `[ ]` items against `git status` / `git log --since="session start"` and the session's tool calls. For each plausibly resolved item propose `Mark complete: <title> (<commit-ref>)?`. On `y`, run COMPLETE.

**Promotion-sweep (Future Scope ripening):** read `## Future Scope`. Scan each `FSLINE`'s title + parenthetical against `git log --since="session start"` (widen to `--since="2 weeks ago"` on the first session of the week), `git status --short`, and the session's tool-call topics (file paths, test names, system terms). On overlap propose `Promote from Future Scope: <title> — looks ripened (matched: "<short evidence>")?`. On `y`, run PROMOTE. On `n` / `n, still parked`, skip silently.

Matcher heuristics (better to miss than to spam):
- Require ≥2 distinct token matches OR one specific identifier match (file path, function name, version number, PR number).
- Skip generic words ("test", "audit", "review") as match anchors.
- Cap at 5 proposals per sweep; beyond that, propose the top 5 by match-strength and surface `(N more Future Scope candidates — run /worklog show all to review)`.
