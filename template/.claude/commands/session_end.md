---
disable-model-invocation: true
description: End-of-session routine — audit, autolearn, self-evaluate, commit & push
---

Run the full end-of-session pipeline. Execute each phase **sequentially** — complete one fully before starting the next. Announce each phase with a brief header so the user can track progress.

**Important:** Each phase is a full command. Follow the instructions in each command file completely — do not abbreviate or skip steps.

## Phase 1: Session Audit
**Goal:** Review all code changes from this session for code smells, sub-optimal design, and substantive improvements.

Invoke: `/session_audit`
- Follow ALL steps in the session_audit command exactly
- Present findings to user — this phase is advisory, not a hard gate
- If user approves any "Fix now" actions, apply them before proceeding
- If verdict is REVIEW RECOMMENDED, pause for user decision on whether to fix or defer

## Phase 2: Autolearn
**Goal:** Extract durable learnings from this session into auto-memory and Skills.

Invoke: `/autolearn`
- Follow ALL steps in the autolearn SKILL exactly
- Wait for user approval on any proposed changes before proceeding

## Phase 3: Self-Evaluate
**Goal:** Reflect on session performance, identify improvement opportunities, archive results.

Invoke: `/self_evaluate`
- Follow ALL steps in the self_evaluate command exactly
- Save the structured entry to the archive

## Phase 3.5: Routing Audit Aggregation
**Goal:** Aggregate the continuous routing-audit log produced by `routing_audit.py` (PostToolUse hook) into a stats JSON that `/eval_dashboard` reads. Rotate >30-day-old entries to monthly archive files.

Invoke: `/routing_audit`
- Equivalent to `python3 .claude/tools/aggregate_routing_audit.py` with default args (30-day active window, rotate older entries).
- Produces `logs/routing_audit_stats.json` (single source of truth for `/eval_dashboard` Routing Stability section).
- Prints a session-level summary: silent-miss count, top-3 missed rules, trend vs prior 4-week average.
- Non-blocking — warn-and-continue if the audit log doesn't exist yet (fresh project, audit hook not yet wired, etc.).

This phase complements `/self_evaluate` (which captures *agent* introspection) with *empirical* routing-decision data — together they feed `/eval_dashboard`'s Skill Hit Rates and Routing Stability sections.

## Phase 4: Sync Subsystem Registry (conditional)
**Goal:** Keep the `project_subsystems` SKILL registry in lockstep with the actual top-level folder layout when this session changed subsystem shape.

Invoke: `/sync_subsystems`
- Step 0 of the command is a signal gate — if the session produced no subsystem-shape change (new/renamed/removed top-level folder) and no subsystem-density change (≥5 files in one subsystem), the command prints "Skipping." and exits. Phase becomes a no-op.
- When the gate fires, walk the user through proposed registry adds/removes/renames/refreshes.
- Non-blocking — registry drift is informational, not a gate; warn-and-continue if anything errors.

> **Verification gate discipline:** No completion claims without fresh verification evidence. The phrases *"should work now"*, *"probably passes"*, *"seems to be fixed"*, and *"I think the regression is resolved"* are all unverified. The next phase runs the regression gate to produce that evidence — do not pre-announce its result. See `feedback_no_performative_agreement.md` for the same discipline applied to feedback reception.

## Phase 5: Regression Gate
**Goal:** Verify zero regressions before committing.

Invoke: `/regression_gate`
- Follow ALL steps in the regression_gate command exactly
- If the gate FAILS, fix failures before proceeding
- Record the pass/fail counts — they go into commit messages in Phase 7

## Phase 5.5: Roadmap Drift Check (conditional)
**Goal:** When this session executed against a plan, prompt to update that plan's target Part on the roadmap before commit. Closes the drift gap where Parts ship in code (via `/plan_handoff` execution) without the roadmap reflecting it.

**Detection (cheap, runs first):** glob `.claude/plans/*.md`. Zero plan files present → skip silently. Phase becomes a no-op.

**Reconciliation (only on hit):**

1. For each plan file in `.claude/plans/`, parse the header to extract the `**Roadmap:**` path + Part-ID (per the plan-file convention: header line `**Roadmap:** <path> — Part **<ID-or-name>**`).
2. Bundle-read the referenced `roadmap.md` files via `read_files` (single bundle call regardless of plan count).
3. For each plan whose target Part is still `plan-pending` or `in-progress` on its roadmap, emit a candidate:

   > `<plan basename> → <roadmap folder> Part <ID> '<Part name>' (status: <X>)`

4. Prompt per candidate: *"Plan execution complete? Mark Part complete on roadmap? (y / n / skip-all / archive-plan)"*
   - `y` → invoke `/update_roadmap` to transition Part to `complete`. Roadmap edit lands in the Phase 7 commit batch (categorize as `chore(roadmap)` or fold into the relevant feature commit per Phase 7 conventions).
   - `n` → plan still in execution / not yet shipped. Skip this candidate.
   - `skip-all` → skip remaining candidates this session.
   - `archive-plan` → plan was abandoned or superseded. Move to `.claude/plans/archive/` and skip Part update.

Non-blocking — drift check is informational, not a gate. Warn-and-continue on parse errors (e.g., plan file missing the `**Roadmap:**` header). Backstop: users can run `/update_roadmap` standalone for sessions that closed without firing this phase, or for Parts shipped without a plan file (manual implementations).

**Plan-file convention (load-bearing):** plan files must include a `**Roadmap:**` header line with the roadmap path AND a Part identifier (ID or name, bold-wrapped). Plans authored via Plan Mode follow this convention; plans authored manually must match it for Phase 5.5 to map them.

## Phase 6: Worklog Sweep
**Goal:** Catch any worklog items I missed adding inline, and sweep completed items into the Completed section before they ship in the upcoming commit.

Invoke: `/worklog sweep`
- Follow ALL steps in the `/worklog` command's `SWEEP` operation section
- Run BOTH halves: add-sweep (transcript scan for missed deferrals) and completion-sweep (git diff vs Active items)
- Each candidate is confirmation-driven — never auto-apply
- If the session genuinely produced no worklog-relevant work in either direction, this phase is a no-op (acceptable)
- Worklog edits made here are part of the upcoming Phase 7 commit (categorize as `chore(worklog)` or fold into the relevant feature commit)

This phase is the deterministic backstop for the auto-detect-and-confirm rule in CLAUDE.md. Run it even if you think you caught everything inline.

## Phase 7: Commit & Push
**Goal:** Commit all session changes and push to remote.

### 7a. Surface the Pre-Commit Checklist

Re-render the structured Pre-Commit Checklist that Phase 5 (`/regression_gate`) produced — this is the explicit gate decision before commit. The canonical format spec lives in [`/regression_gate` Step 7b](regression_gate.md). Re-render it here populated with current session state, including any `/session_audit` outcomes from Phase 1 and refactor-parity status from Phase 1.5.

**Decision rule:**
- **All items `[x]` or `[—]`:** proceed to 7b silently — verdict is APPROVE.
- **Any item `[ ]`:** STOP and surface the unchecked items to the user before commit. Use `AskUserQuestion` with these options per unchecked item:
  - **Resolve now** — re-run the relevant phase (e.g., re-run `/session_audit` if that's the unchecked item) before committing
  - **Acknowledge and proceed** — accept as APPROVE WITH NOTES; the unchecked-item rationale will be captured in the commit message footer
  - **Abort commit** — return to working state; do not commit

Do NOT proceed to 7b until every `[ ]` is resolved to `[x]`, `[—]`, or explicitly acknowledged.

### 7b. Commit & Push

Invoke: `/commit_push`
- Follow ALL steps in the commit_push command exactly
- Group changes into categorical commits
- Include regression gate counts in each commit message
- If any checklist items were acknowledged-but-unchecked in 7a, append a footer line to the relevant commit: `Pre-commit notes: <unchecked item> — <user rationale>`
- Push to current branch

## Phase 8: Refresh Semantic-Search Index
**Goal:** Keep `mcp__plugin_semantic-search_semantic-search__search` (DreB plugin, CLAUDE.md §9) fresh for the next session's NL-discovery queries.

Invoke: `/reindex_search`
- Non-blocking — warn-and-continue on failure (never abort session_end on a re-index error).
- If the plugin is uninstalled or the MCP tool isn't registered, the command is a no-op — proceed silently.
- Rationale: the working tree just stabilized (post-commit), so this is the right moment to capture a clean index. Keeps the next session's first semantic-search query from paying the 10-60s rebuild cost on top of cold-start latency.

This phase is hygiene, not a gate. Skip cleanly if anything errors.

## Between Phases
After each phase completes, print a brief status line:
```
--- Phase N complete. Moving to Phase N+1: [name] ---
```

If any phase encounters an issue requiring user input, pause and wait for resolution before continuing.

**Phase 5 is a hard gate.** If regression gate fails, do NOT proceed. Fix the failures first.
