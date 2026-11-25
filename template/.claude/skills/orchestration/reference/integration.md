# Integration: a running or returned dispatch

## While a job runs

A running Workflow or sidecar author job has no inbox, so it is amended per `SKILL.md` §4 (`S.md.amend.md` for `S.md`). `hooks/amend_delivery.py` delivers the full text to the job at its next tool call, again whenever the file changes, and blocks the job's stop on a revision it never received; the job reads nothing unless the amendment passes the client's 10,000-character hook-text cap, and then only the rest the delivery names. A message may resume a finished agent; do not reopen a completed audit or review files still being edited. Never edit a running fold's subject or writer's write set: wait for its result, or use a worktree.

## When it returns

`dispatch.js` checks declared returns and recognized claims/findings fences against `reference/evidence_contract.md` and returns a `verification` block with applied verdicts. A job declaring `fold: {findings, digest, snapshot}` or `ledger: {stage, subject, roster, scopeJson}` selects its review closure. The parent checks each required standalone findings/claims deliverable's schema and completeness and runs its applicable `tools/post_check.py` route when no checked block was produced. In review mode the report carries fold results, held items, failures and ledger rows; the parent rules these per `commands/agents/orchestrator_action_protocol.md` Step 1.5. Preserve original findings and provenance on integration (Step 1 there owns location versus defect identity). The Stop audit recomputes checked verdicts and blocks on a mismatch or an unapplied verdict.

A writer's change is judged as a review before you accept or report it (`CLAUDE.core.md` §Core Principles). Read its whole diff, tests included, against the rules its changed files fall under: each `rules/*.md` whose `paths:` match them, read now, because a diff read through Bash loads none of them. Judge it also against the class its fix must cover (every caller and route its rule reaches) and against the obvious design the owner's note asks for. The report's account of its own runs is a claim: restate a RED, a pass or a cost only from the run line it cites.

Compare intent with what ran. Before dispatch, write the announced job list (label, model, effort, transport, role per job) as the manifest seed; after the run, build the manifest (`orchestration_metrics.py --manifest-seed`). Record each `drift` entry and each label the manifest refuses in that job's rating note. A missing label, or a brief mandate with no returned seat, is UNCOVERED (`orchestration` §2's coverage rule).

Recover before another paid call (artifact, then transcript or `/salvage_fanout`), and confirm a job is terminal before replacing it. Rate each result on consumption (`/orchestration_metrics` §Incremental rating).
