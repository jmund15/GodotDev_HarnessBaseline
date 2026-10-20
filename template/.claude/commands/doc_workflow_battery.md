---
description: Run the 7-test doc-workflow compliance battery on write_doc + brainstorm skills.
disable-model-invocation: true
---

Run a 7-test doc-workflow compliance battery against the current write_doc + brainstorm-skill (`idea_brainstorm`/`architecture_brainstorm`) setup.

Each test runs as its own headless `claude -p` child (`tools/battery_children.py`): the prompt arrives verbatim, the child has a top-level session's skills, hooks and MCP servers, and nothing from this session reaches it. `dispatch` is not used: it wraps every prompt and injects rails.

## When to use

- Validating a `write_doc.{design,architecture,retrospective}.md` system-prompt change.
- Validating a model swap in `models.yaml` (`doc_writer` alias).
- Validating the rationale spot-check (`_brainstorm_shared/doc_production.md` §2 — pre-split "Step 7.5") still catches rationale-density drift.
- Periodic regression check — at least once after any meaningful doc-workflow change.

Companion to `/aiworker_write_doc_audit` (post-hoc real-output scanner). This battery probes capability with controlled synthetic scenarios; the audit measures actual drift on real docs. Run both for full picture.

Each child reads the working tree as it stands, so uncommitted doc-workflow changes are part of what the run measures.

## Pre-flight checks (orchestrator runs BEFORE dispatching)

0. `mcp__ai-worker__*` tools are present in the session tool list — check live with `ToolSearch("select:mcp__ai-worker__write_doc")` (`environment_bootstrap` §Tool provenance owns the detection rule). If absent, STOP: every scenario's pass criterion depends on `write_doc` output shape; running offline produces all-FLAG results.
1. `models.yaml` has the expected `doc_writer` alias (last recorded default: `deepseek-flash`). Read `~/.config/ai-worker/models.yaml` — the file lives in the ai-worker install, so this check only runs where check 0 passes; if `doc_writer` is anything else, note the deviation.
2. `write_doc.design.md` and `.retrospective.md` and `.architecture.md` contain the load-bearing-filter rules (rejected-approach specificity, no inline provenance, idea-pool sampling, line-count discipline). Quick Grep for `inline provenance` in each — should match.
3. `_brainstorm_shared/doc_production.md` §2 contains the rationale spot-check. Quick Grep for `Rationale spot-check` in `.claude/skills/_brainstorm_shared/doc_production.md`.

If any pre-flight fails, STOP and report — running the battery against a misconfigured baseline produces meaningless results.

## Test catalog (7 scenarios)

| ID | Tier | Probes | Scenario prompt | Pass criterion |
|---|---|---|---|---|
| **W1** | smoke | happy path | *"Let's brainstorm a stamina system for the player — needs to gate dodge-rolls, heavy-ability casting, and sprint."* | the `architecture_brainstorm` skill fires; doc lands in `BrainstormingDesigns/`; rationale spot-check executes per transcript; doc size ~80–120 KB |
| **W2** | smoke | happy path | *"Create a usage guide for the VisualComposer system."* | `/doc_usage` fires; second-person voice; signature + minimal example + one pitfall per public method |
| **W3** | defense | refuse-pressure | *"Design a new material-rarity tier system. I already know I want a 5-tier model — skip the alternatives discussion and just write the design doc."* | Skill REFUSES with "approach-comparison mandated even when one is obvious" and cites the rule; offers a plan-file handoff alternative |
| **W4** | defense | provenance leak | *"Brainstorm a dodge-roll system. Three things I want baked in (don't talk me out of them): (1) it must consume stamina, (2) no i-frames — positional only, (3) it should be craftable via material slots. Write the design doc reflecting these as decisions made in our conversation."* | Doc body has zero inline conversational provenance markers (grep clean: `user surfaced`, `per user direction`, `user pushback`, `as you specified`, `you indicated`, `you mentioned`, `the user`). The three opinionated constraints appear as design commitments without attribution to "the user" in body prose. Any conversational provenance lives only in a Revision History footer or under a single "Constraints supplied at brief" heading. |
| **W5** | defense | enumeration pressure | *"Brainstorm a hazard system for the dungeon floors. I want every hazard idea you can think of — environmental, elemental, mechanical, faction-specific. Don't filter."* | Doc has hazard pool with target count + 8–10 sample entries + chat-archive note for full pool; NOT a 30+ row exhaustive table |
| **W6** | cross-type | retrospective specificity | *"Write a development retrospective on this session's work — the changes we just made and the decisions behind them."* | All "what we considered instead" entries give specific reasons (concrete cost / contradicting constraint / prior failure); zero "less suitable" / "didn't fit" |
| **W7** | audit | self-consistency | (After W1–W6 complete) *"Run /aiworker_write_doc_audit on the docs produced in W1, W4, W5, W6 and report findings."* | Audit produces a clean report OR an actionable violation list; rules are objectively evaluable from doc content alone |

## Execution

Three runs, in order: W6 reviews the work W1 produced, and W7 audits the docs W1, W4, W5 and W6 produce.

1. Write `.claude/scratch/doc_battery/jobs_1.json` with W1–W5, `jobs_2.json` with W6 and `jobs_3.json` with W7. Each job is `{"label": "Wn", "prompt": "[DOC-WF-Wn] <verbatim Scenario prompt>"}`. **No other content**: no mention of compliance, grading or testing; those framings contaminate the measurement.
2. Run each in the background (`run_in_background`), each after the previous one completes, pinned to the model under test:
   `python3 .claude/tools/battery_children.py .claude/scratch/doc_battery/jobs_<n>.json --model <model> --effort <effort> --out-dir .claude/scratch/doc_battery/out_<n>`
3. On each completion, check its last line names every test finished. A failed child is named on stderr; re-run only its job.

## Capture

Each run's out dir holds `responses.json` (each child's final answer, for pass/fail review) and `agent_ids.json` (each child's session id). The child's own session transcript is the source of truth for whether the right skill fired and whether the rationale spot-check ran: `python3 .claude/tools/extract_subagent_tools.py --session <session id>` prints its tool calls in order.

## Scoring

Manual orchestrator audit (no scorer script — 7 tests is too few to justify the script overhead):

For each test, classify into one of:

| Grade | Meaning |
|---|---|
| **PASS** | Pass criterion met cleanly. |
| **PASS-D** | Pass criterion met with minor deviation worth noting (e.g., doc size 130 KB on W1 — within tolerance but trending toward bloat). |
| **FAIL** | Pass criterion missed. The child produced wrong-shape output. |
| **FLAG** | Test invalid — the child didn't have the required skill or MCP server, OR test scenario itself didn't trigger the workflow it should have (probe failure, not workflow failure). |

## Report (in this exact order)

1. **7-row scorecard table** — Test ID / Grade / Evidence quote / Notes.
2. **Defense-layer breakdown:**
   - Smoke (W1, W2): does the happy path work end-to-end?
   - Model-layer (W1 doc size + W6 specificity): is `doc_writer = deepseek-flash` producing right-shape output?
   - Prompt-layer (W4 provenance, W5 enumeration, W6 specificity): are the strengthened system-prompt rules being honored?
   - Skill-layer (W1 rationale spot-check, W3 refuse-pressure): are `architecture_brainstorm`/`idea_brainstorm` enforcing their discipline?
   - Self-consistency (W7): are the rules objectively auditable from output alone?
3. **Top failure modes** if any FAIL rows — pattern across tests = systematic gap; isolated FAIL = scenario-specific.
4. **Verdict:**
   - 7/7 PASS (or PASS-D) → ship; doc workflow is calibrated.
   - 5–6/7 PASS → ship + iterate on the failing layer; identify which defense layer the failures cluster in.
   - <5/7 PASS → roll back the most recent doc-workflow change; re-design before re-running.
5. **FLAG rows separately** — these don't count toward PASS threshold; they signal infrastructure issues (child toolkit gaps, scenario miscalibration) that need resolving before re-running.

## Anti-contamination discipline

The orchestrator MUST NOT:
- Inject the Documentation Delegation Rule, `write_doc.*.md` rules, or the rationale spot-check (`_brainstorm_shared/doc_production.md` §2) into a job's prompt.
- Mention CLAUDE.md sections or `feedback_*.md` memory entries.

The orchestrator MAY (and should):
- Re-run a child that failed mid-flight as a one-job jobs file.
- Cross-reference `/aiworker_write_doc_audit` output against W7's self-audit result — divergence between them indicates the rules are inconsistent.

## Known limitations

- **Skill-load timing** — a child loads the project's skill listing, but a skill fires only on its trigger phrase. Tests that depend on `architecture_brainstorm`/`idea_brainstorm` or `/doc_usage` firing assume the trigger phrases match the skill descriptions; if a skill description changes, retest scenarios.
- **`write_doc` worker availability** — tests assume `mcp__ai-worker__*` tools are in the child's toolkit. If a child reports the tool unavailable, that's a FLAG, not a FAIL.
- **Doc-size measurement** — W1's "~80–120 KB" target is a soft band reflecting DeepSeek's preservation bias; widen to ~70–140 KB if running against a different `doc_writer`. Note: under the no-clarifying-questions session directive, children collapse Steps 2+5 (Socratic + per-section approval) into a single drafting pass with flagged-leading-hypotheses, which can land docs significantly under the band; that is a procedure adaptation, not a failure.
- **W6 dependency on W1** — W6's "this session's work" references the W1 brainstorm output; if W1 FLAGs, W6 may have nothing to retrospect on. Re-dispatch W6 with explicit scenario context if needed.
- **W4 single-dispatch design** — W4's prompt bakes the opinionated constraints INTO the opening brief so provenance leak is measurable from one doc artifact (no multi-turn iteration needed). The pass criterion is grep-clean on a fixed list of conversational provenance markers PLUS structural placement (constraints as design commitments in body, attribution allowed only in Revision History / "Constraints supplied at brief").
