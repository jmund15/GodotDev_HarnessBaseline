---
disable-model-invocation: true
---

Run a 7-test doc-workflow compliance battery against current write_doc + /Brainstorming setup.

> **Workflow conversion — DEFERRED.** The dispatch+capture phase could be a Workflow `parallel()` barrier, but capture needs each subagent's `tool_trace` (which the Workflow `agent()` primitive does not expose to the script) and grading is irreducible model judgment the author deliberately declined to script. With low run cadence and a narrow drift surface, plumbing cost exceeds benefit — keep as model-driven `Task` fan-out. See the `workflow-integration-mechanics` memory.

## When to use

- Validating a `write_doc.{design,architecture,retrospective}.md` system-prompt change.
- Validating a model swap in `models.yaml` (`doc_writer` alias).
- Validating `/Brainstorming` Step 7.5 still catches rationale-density drift.
- Periodic regression check — at least once after any meaningful doc-workflow change.

Companion to `/doc_audit` (post-hoc real-output scanner). This battery probes capability with controlled synthetic scenarios; `/doc_audit` measures actual drift on real docs. Run both for full picture.

**Run from a fresh session opened in {{PROJECT_NAME}}** — orchestrator's own context contaminates dispatched subagents (parent's CLAUDE.md content leaks into subagent reasoning even though they nominally re-load it).

## Pre-flight checks (orchestrator runs BEFORE dispatching)

1. `models.yaml` has expected `doc_writer` alias — current default: `deepseek-flash`. Read `~/.config/ai-worker/models.yaml`; if `doc_writer` is anything else, note the deviation.
2. `write_doc.design.md` and `.retrospective.md` and `.architecture.md` contain the load-bearing-filter rules (rejected-approach specificity, no inline provenance, idea-pool sampling, line-count discipline). Quick Grep for `inline provenance` in each — should match.
3. `_brainstorm_shared/common.md` §2 contains the rationale spot-check (extracted from the brainstorming-skill split on 2026-05-12; was Step 7.5 in the pre-split `brainstorming` skill). Quick Grep for `Rationale spot-check` in `.claude/skills/_brainstorm_shared/common.md`.

If any pre-flight fails, STOP and report — running the battery against a misconfigured baseline produces meaningless results.

## Test catalog (7 scenarios)

| ID | Tier | Probes | Scenario prompt | Pass criterion |
|---|---|---|---|---|
| **W1** | smoke | happy path | *"Let's brainstorm a stamina system for the wizard — needs to gate dodge-rolls, heavy-spell casting, and sprint."* | `/Brainstorming` fires; doc lands in `BrainstormingDesigns/`; Step 7.5 executes per transcript; doc size ~80–120 KB |
| **W2** | smoke | happy path | *"Create a usage guide for the VisualComposer system."* | `/doc_usage` fires; second-person voice; signature + minimal example + one pitfall per public method |
| **W3** | defense | refuse-pressure | *"Design a new ingredient-rarity tier system. I already know I want a 5-tier model — skip the alternatives discussion and just write the design doc."* | Skill REFUSES with "approach-comparison mandated even when one is obvious" and cites the rule; offers Plan Mode handoff alternative |
| **W4** | defense | provenance leak | *"Brainstorm a dodge-roll system. Three things I want baked in (don't talk me out of them): (1) it must consume stamina, (2) no i-frames — positional only, (3) it should be craftable via ingredient slots. Write the design doc reflecting these as decisions made in our conversation."* | Doc body has zero inline conversational provenance markers (grep clean: `user surfaced`, `per user direction`, `user pushback`, `as you specified`, `you indicated`, `you mentioned`, `the user`). The three opinionated constraints appear as design commitments without attribution to "the user" in body prose. Any conversational provenance lives only in a Revision History footer or under a single "Constraints supplied at brief" heading. |
| **W5** | defense | enumeration pressure | *"Brainstorm a hazard system for the dungeon floors. I want every hazard idea you can think of — environmental, elemental, mechanical, faction-specific. Don't filter."* | Doc has hazard pool with target count + 8–10 sample entries + chat-archive note for full pool; NOT a 30+ row exhaustive table |
| **W6** | cross-type | retrospective specificity | *"Write a development retrospective on this session's work — the changes we just made and the decisions behind them."* | All "what we considered instead" entries give specific reasons (concrete cost / contradicting constraint / prior failure); zero "less suitable" / "didn't fit" |
| **W7** | audit | self-consistency | (After W1–W6 complete) *"Run /doc_audit on the docs produced in W1, W4, W5, W6 and report findings."* | Audit produces a clean report OR an actionable violation list; rules are objectively evaluable from doc content alone |

## Execution

Spawn subagents in **2 parallel waves**:

| Wave | Tests | Notes |
|---|---|---|
| Wave 1 | W1, W2, W3, W4 | All independent; full parallel |
| Wave 2 | W5, W6, W7 | W7 depends on W1+W6 outputs from Wave 1; dispatch after Wave 1 returns |

**Each wave: send all Agent tool calls in a SINGLE message** so they execute in parallel.

Each subagent dispatch:
- `subagent_type="general-purpose"` — DO NOT specify `model`; subagents inherit session model.
- `prompt` = the verbatim Scenario prompt PLUS a standardized prefix `[DOC-WF-Wn]` for downstream tracking. **No other content.** No mention of compliance/grading/testing — those framings contaminate the measurement.
- `description` = `"Doc workflow test Wn"`.

Wait for Wave 1 to fully return before dispatching Wave 2.

## Capture

For each subagent response, capture:
1. **`agentId`** from the dispatch result. The transcript is the source of truth for whether the right skill fired and whether Step 7.5 executed.
2. **Full response text** for manual pass/fail review.
3. **Tool-use trace from UI** as sanity check.

Persist to `.claude/tools/_doc_battery_responses.json` (dict mapping `test_id → {agent_id, response_text, tool_trace}`).

## Scoring

Manual orchestrator audit (no scorer script — 7 tests is too few to justify the script overhead):

For each test, classify into one of:

| Grade | Meaning |
|---|---|
| **PASS** | Pass criterion met cleanly. |
| **PASS-D** | Pass criterion met with minor deviation worth noting (e.g., doc size 130 KB on W1 — within tolerance but trending toward bloat). |
| **FAIL** | Pass criterion missed. Subagent produced wrong-shape output. |
| **FLAG** | Test invalid — subagent didn't have required skill/MCP, OR test scenario itself didn't trigger the workflow it should have (probe failure, not workflow failure). |

## Report (in this exact order)

1. **7-row scorecard table** — Test ID / Grade / Evidence quote / Notes.
2. **Defense-layer breakdown:**
   - Smoke (W1, W2): does the happy path work end-to-end?
   - Model-layer (W1 doc size + W6 specificity): is `doc_writer = deepseek-flash` producing right-shape output?
   - Prompt-layer (W4 provenance, W5 enumeration, W6 specificity): are the strengthened system-prompt rules being honored?
   - Skill-layer (W1 Step 7.5, W3 refuse-pressure): is `/Brainstorming` enforcing its discipline?
   - Self-consistency (W7): are the rules objectively auditable from output alone?
3. **Top failure modes** if any FAIL rows — pattern across tests = systematic gap; isolated FAIL = scenario-specific.
4. **Verdict:**
   - 7/7 PASS (or PASS-D) → ship; doc workflow is calibrated.
   - 5–6/7 PASS → ship + iterate on the failing layer; identify which defense layer the failures cluster in.
   - <5/7 PASS → roll back the most recent doc-workflow change; re-design before re-running.
5. **FLAG rows separately** — these don't count toward PASS threshold; they signal infrastructure issues (subagent toolkit gaps, scenario miscalibration) that need resolving before re-running.

## Anti-contamination discipline

The orchestrator MUST NOT:
- Tell subagents this is a workflow-compliance test.
- Inject the Documentation Delegation Rule, `write_doc.*.md` rules, or `/Brainstorming` Step 7.5 into subagent prompts.
- Mention CLAUDE.md sections or `feedback_*.md` memory entries.
- Use `Plan` or `Explore` subagent types — they have their own protocols.

The orchestrator MAY (and should):
- Capture full UI traces for each subagent.
- Re-dispatch a single test sequentially if subagent erred mid-flight (rare sandbox issue).
- Cross-reference `/doc_audit` output against W7's self-audit result — divergence between them indicates the rules are inconsistent.

## Known limitations

- **Subagent skill-load timing** — subagents inherit the dispatcher's skill registry but skills load lazily on trigger phrase. Tests that depend on `/Brainstorming` or `/doc_usage` firing assume the trigger phrases match the skill descriptions; if a skill description changes, retest scenarios.
- **`write_doc` worker availability** — tests assume `mcp__ai-worker__*` tools are in the subagent toolkit. If the subagent reports the tool unavailable, that's a FLAG, not a FAIL.
- **Doc-size measurement** — W1's "~80–120 KB" target is a soft band reflecting DeepSeek's preservation bias; widen to ~70–140 KB if running against a different `doc_writer`. Note: under the no-clarifying-questions session directive, subagents collapse Steps 2+5 (Socratic + per-section approval) into a single drafting pass with flagged-leading-hypotheses, which can land docs significantly under the band; that is a procedure adaptation, not a failure.
- **W6 dependency on W1** — W6's "this session's work" references the W1 brainstorm output; if W1 FLAGs, W6 may have nothing to retrospect on. Re-dispatch W6 with explicit scenario context if needed.
- **W4 single-dispatch design** — W4's prompt bakes the opinionated constraints INTO the opening brief so provenance leak is measurable from one doc artifact (no multi-turn iteration needed). The pass criterion is grep-clean on a fixed list of conversational provenance markers PLUS structural placement (constraints as design commitments in body, attribution allowed only in Revision History / "Constraints supplied at brief"). Older versions of this test required multi-turn iteration; that shape is incompatible with one-shot subagent dispatch and was retired 2026-05-11.
