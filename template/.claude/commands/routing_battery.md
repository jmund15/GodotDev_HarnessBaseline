---
disable-model-invocation: true
---

Run the routing-compliance battery against current hooks + doctrine. The test roster and count derive from the rubric `.claude/tests/routing_compliance.md` — never pin them here.

> **Workflow conversion — BLOCKED (platform capability).** The scorer (`score_routing_battery.py` via `extract_subagent_tools.py`) grades on each subagent's captured `agent_id` (the sole scoring signal) by globbing transcripts at `…/<session>/subagents/agent-<id>.jsonl`. The dynamic-Workflow `agent()` primitive does NOT expose the subagent `agent_id`/tool-trace to the script, and workflow subagents land under `subagents/workflows/<runid>/` — which the extractor's flat glob never matches. Keep this command's dispatch as model-driven `Task` fan-out until the platform exposes agent ids (and the extractor learns the nested path). See the `workflow-integration-mechanics` memory.

## When to use

- Validating a routing-doctrine change (CLAUDE.md §9, `csharp_lsp.md`, `feedback_tool_routing_discipline.md`).
- Validating a hook change (`tool_routing_nudge.py`, `tool_routing_post_grep.py`, `tool_routing_cumulative.py`).
- Validating a new tool addition (semantic-search reindex, MCP server change, etc.).
- Periodic regression check — at least once per quarter, more often if hooks churn.

**Do NOT run mid-session after the orchestrator has touched routing artifacts** — the orchestrator's context contaminates its own dispatched subagents (parent's CLAUDE.md content can leak into subagent reasoning even though they nominally re-load it). Always run from a **fresh session** opened in the {{PROJECT_NAME}} repo.

## Pre-flight checks (orchestrator runs these BEFORE dispatching)

1. **Derive the test count `N`** — count unique `[ROUTECHECK-XX]` IDs in `.claude/tests/routing_compliance.md` (44 as of 2026-07-04), then cross-check `N` against the key count of the `EXPECTED` dict in `.claude/tools/score_routing_battery.py`. Mismatch → rubric and scorer are out of sync — STOP and reconcile before running.
2. Confirm `agent_dispatch_preamble.py` is NOT wired in `settings.json` — that hook (if present) injects routing doctrine into subagent prompts and contaminates the test (rubric leakage). The hook should not exist; if it does, the run is invalid.

## Execution

Spawn subagents in **parallel waves** to respect the 15-concurrency cap: chunk the rubric's test IDs, in rubric order, into waves of ≤12 (at `N`=44 that is 4 waves of 12/12/12/8). Derive the wave assignment from the rubric each run — do not maintain an ID table here.

**Each wave: send all Agent tool calls in a SINGLE message** so they actually run in parallel.

Each subagent dispatch:
- `subagent_type="general-purpose"` — DO NOT specify the `model` parameter; subagents inherit the session's model.
- `prompt` = the verbatim "Prompt to paste:" content from the test fixture (which already includes the `[ROUTECHECK-XX]` prefix). **No other content.** No mention of routing-compliance, no mention of grading, no mention of testing, no header/format contract — any of those framings contaminate the measurement.
- `description` = short test ID (e.g., `"Routing test A1"`)

Wait for each wave to fully return before dispatching the next.

## Capture

For each subagent response, capture:
1. **The `agentId`** from the dispatch result (e.g. `agentId: a3c1609323939f093` in the agent's tool-result block). This is the **sole scoring signal** — the scorer reads the subagent's JSONL transcript directly via `extract_subagent_tools.py` and extracts the deterministic tool-call sequence. Without `agentId` capture, every test grades NO-TOOL-CALLS.
2. The full response text (used for override-justification detection and for the orchestrator-audit step on NO-TOOL-CALLS rows).
3. The tool-use trace shown in the UI (sanity check; the transcript should match it).

Persist results to two files:
- `.claude/tools/_battery_responses.json` — dict mapping `test_id → response_text`. Used for override-phrase detection AND as the input to the orchestrator-audit step.
- `.claude/tools/_battery_agent_ids.json` — dict mapping `test_id → {"agent_id": <id>, "session_id": <session>}`. The scorer reads transcripts via these IDs. The session_id is the parent session ID (visible in the SessionStart context); all subagents share it.

## Scoring

Run the scorer with both files (transcript-based extraction is the deterministic path):

```
python3 .claude/tools/score_routing_battery.py .claude/tools/_battery_responses.json .claude/tools/_battery_agent_ids.json
```

Or fall back to header-only scoring (back-compat, lossy):

```
python3 .claude/tools/score_routing_battery.py .claude/tools/_battery_responses.json
```

The scorer produces grades in `{PASS, PASS-O, PASS-NOMINAL, PASS-D, PASS-ND, MISSING-HEADER, NO-TOOL-CALLS, FAIL}` per the tier definitions in `routing_compliance.md` and `score_routing_battery.py`. PASS, PASS-O, and PASS-* variants count equally toward compliance. With agent IDs, MISSING-HEADER becomes near-zero — `NO-TOOL-CALLS` replaces it for the rare case where a subagent genuinely refused to act and made zero tool calls (e.g. when required MCPs aren't available in the subagent's tool surface).

## Orchestrator Audit (run BEFORE writing the report)

The scorer is transcript-only and graders any test with zero transcript tools as `NO-TOOL-CALLS`. That grade is a **trigger for human/orchestrator judgment**, not a final result. Before producing the report, the orchestrator MUST classify each `NO-TOOL-CALLS` row by reading the corresponding response from `_battery_responses.json` and applying these rules:

| Response shape | Final grade |
|---|---|
| Test is M-category or N-category, response prose names the expected tool (e.g. `write_doc`, `write_code`) without negation context | **PASS** — planning-style answer, this is the legitimate response shape for these categories |
| Response prose names the expected tool AND cites why the agent chose it despite normal routing (override-justified — K1/L6 shape) | **PASS-O** |
| Response says the agent identified the correct tool but the tool was unavailable in the subagent toolkit (e.g. *"`mcp__ai-worker__read_files` is not available in this agent's toolset"*) | **FLAG** — environmental issue, not a doctrine miss. Does NOT count as PASS toward compliance threshold but should be reported separately so the user can verify subagent toolkit before re-running |
| Response is a genuine refusal with no useful routing reasoning, OR the response describes a wrong tool, OR the response is contentless | **FAIL** |

The audit produces a per-row final grade that supersedes the scorer's `NO-TOOL-CALLS`. Track audit decisions explicitly in the scorecard's "Notes" column so re-runs can compare.

## Report (in this exact order)

1. **Full `N`-row scorecard table** (one row per rubric test) — use the template at the bottom of `routing_compliance.md` (the "Scorecard template" section). For each `NO-TOOL-CALLS` row, show both the scorer grade and the post-audit final grade.
2. **Per-category pass rate (A through N, plus L)** — both routing-only and combined. Use post-audit final grades.
3. **Signal-source breakdown** — `transcript / none` counts from the scorer. Transcript should be dominant; high `none` count means many tests need orchestrator audit (legitimate for M/N, problematic if it's hitting other categories — verify subagent toolkit was complete).
4. **L-category soft-gate result** — ≥7/10 PASS or PASS-O on L1–L10 = rule-internalization confirmed; <6/10 = agents follow dominant rules without understanding their boundaries.
5. **Orchestrator-audit summary** — count of rows reclassified by audit, broken down by final grade (how many `NO-TOOL-CALLS` flipped to PASS / PASS-O / FLAG / FAIL). This separates measurement-reclassification from raw scorer behavior.
6. **Top 3 failure modes by frequency**, with the test IDs that exhibited each. Include both routing failures and silent-wrong-symbol traps. Keep `FLAG` rows distinct from `FAIL` — they signal infrastructure issues, not doctrine misses.
7. **Compliance-threshold verdict** against the targets in `routing_compliance.md` §"Compliance threshold" (the authoritative bands — ship gate ~78% PASS+PASS-O, plus iterate/roll-back tiers with most-likely-cause notes; do not restate the numbers here, they scale with `N`). FLAG rows do NOT count toward PASS+PASS-O; they're a separate "needs verification" bucket.
8. **Hard-gate verification** — zero regressions on the 11 baseline-passing tests (B1, B2, A3, C1, F1–F4, G1, K1, K2). A regression here is worse than a non-improvement on the failure cases.
9. **Hook-fired-but-ignored count** — cases where a nudge appeared in the response but the agent ignored it. Distinct from FAIL; indicates the nudge channel works but the message isn't persuading the agent.

## Anti-contamination discipline

The orchestrator MUST NOT:
- Tell subagents this is a routing-compliance test.
- Inject any routing-doctrine reminders into the subagent prompts beyond what the fixture's standardized prefix already contains.
- Mention CLAUDE.md §9 / §7 / `csharp_lsp.md` / specific tool routing rules in the subagent prompts.
- Use the `Plan` or `Explore` subagent types — they have their own protocols and aren't equivalent to general-purpose.

The orchestrator MAY (and should):
- Capture full UI traces for each subagent (the scorer needs them when [TOOLS-USED:] headers are missing).
- Dedupe responses by test_id when persisting to JSON.
- Flag any subagent that errored mid-flight (rare sandbox issue with parallel dispatch) and re-dispatch that single test sequentially.

## Known limitations (don't be surprised by these)

- **Subagent tool-surface mismatch** — subagents inherit the dispatcher's MCP server registry but not always cleanly; tests requiring `mcp__obsidian__*` or `mcp__ai-worker__*` may hit refusals where the agent identifies the correct tool but cannot call it. These rows score `NO-TOOL-CALLS` from the scorer; orchestrator audit reclassifies them as `FLAG` (environmental issue, verify subagent toolkit before treating as doctrine miss). To rescue: dispatch the failing tests as fresh top-level Claude Code sessions instead of subagents.
- **Subagent session_id sharing** — hooks fire on the parent's session_id, so cumulative-counter state may misattribute in parallel-subagent dispatches. Mitigation in place: 3-second burst-suppression in `tool_routing_cumulative.py`. May still fire spurious cumulative nudges; don't penalize subagents that received an inflated count.
- **C2 documentSymbol coordinate gotcha** — agents may organically walk into the silent-wrong-symbol trap (column 53 → 216 wrong refs for ApplySynergies). This is a known LSP wrapper bug documented in `csharp_lsp.md`. If C2 fails via this path, the failure validates the documentation rather than indicating a doctrine miss — note in the report.
