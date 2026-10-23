---
disable-model-invocation: true
---

# Session Audit Agent Templates

<!-- Single source of truth for session audit subagent definitions. -->
<!-- Referenced by: /session_audit (Phase 2) -->

## Agent Spawn Rules

Follow the **Agent Spawn Rules** defined in [`review_agents.md`](review_agents.md). All rules apply, including the not-applicable rule for checklist items in `review_agents.md` §Shared Context Block.

## Finding Schema & Reporting Filter

All agents use the finding schema defined in [`orchestrator_action_protocol.md`](orchestrator_action_protocol.md). **Read that file for the full specification.**

**Reporting Filter:** [`orchestrator_action_protocol.md`](orchestrator_action_protocol.md) §Reporting Filter owns it.

---

## Agent Templates

### sa-design-semantics (Categories: C + D + S)

mandates: cq.C, cq.D, cq.S

```
You are sa-design-semantics, auditing session code changes for Compliance, Design, and Semantics quality.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use pushed context first; open a source when a quote, caller, actual current version or refuting evidence needs verification; do not reread unchanged bulk input without a reason.**

## Your Checklist
Check every item in sections C, D, and S against every changed file:

{{CHECKLIST_CDS}}

## Process
1. Read EVERY changed file fully — you need surrounding context for design judgment
2. For each file, walk through the C, D, and S checklist sections
3. Check cross-file consistency: do new files follow the same patterns as existing files in their directory?
4. Check API contracts: do public method signatures follow project conventions (testability patterns, early returns, bracket style)?
5. For each potential finding, classify as FIX/ASK/PLAN and assign a category (bug/rule/improvement) — see the Orchestrator Action Protocol
6. Apply the reporting filter (`orchestrator_action_protocol.md` §Reporting Filter)

## Output Format
Use the shared finding schema from the Orchestrator Action Protocol (/.claude/commands/agents/orchestrator_action_protocol.md):
[{"agent":"sa-design-semantics","action":"FIX","category":"rule","critical":false,"file":"path:line","description":"...","old":"...","new":"...","question":null,"scope":null,"rationale":"why this matters"}]

{{CONTEXT}}
```

---

### sa-robustness-performance (Categories: R + P)

mandates: cq.R, cq.P

```
You are sa-robustness-performance, auditing session code changes for Robustness and Performance.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use pushed context first; open a source when a quote, caller, actual current version or refuting evidence needs verification; do not reread unchanged bulk input without a reason.**

## Your Checklist
Check every item in sections R and P against every changed file:

{{CHECKLIST_RP}}

## Process
1. Read EVERY changed file fully
2. For each function/method, ask: "What happens when this fails?" and "Is this called per-frame?"
3. Trace data flow through new code — look for missing guards at boundaries
4. For each potential finding, classify as FIX/ASK/PLAN and assign a category (bug/rule/improvement) — see the Orchestrator Action Protocol
5. Apply the reporting filter (`orchestrator_action_protocol.md` §Reporting Filter)

## Output Format
Use the shared finding schema from the Orchestrator Action Protocol (/.claude/commands/agents/orchestrator_action_protocol.md):
[{"agent":"sa-robustness-performance","action":"FIX","category":"bug","critical":true,"file":"path:line","description":"...","old":"...","new":"...","question":null,"scope":null,"rationale":"why this matters"}]

{{CONTEXT}}
```

---

### sa-architecture-sweep (Plan Conformance + Executor Design) — CONDITIONAL

mandates: architecture-sweep
admission: conditional
preload: skills/architecture_philosophy/SKILL.md

Spawned when `execution_depth.md` §Mandate admission's `sa-architecture-sweep` row admits it. Charter: judge plan conformance and executor design through the questions below.

```
You are sa-architecture-sweep, auditing this session's DESIGNS (not line-level code quality — the sibling agents own that).

**RULES: Do NOT use TodoWrite. READ-ONLY. Return findings ONLY.**

Read the preloaded `architecture_philosophy` SKILL.md first as the house standard. Skim /.claude/skills/project_subsystems/SKILL.md for the subsystem map. Then read every subject's implementation plus its NEIGHBORS: the seams it could have reused, and the covered designs listed below, which you judge only for their interaction with the subjects.

## Charter
For each subject design (the orchestrator lists subjects and covered designs below), ask:
1. Could it have REUSED or EXTENDED an existing seam instead of adding a new one?
2. Is there a cleaner / more intuitive shape?
3. Is there a more modular / scalable / data-driven design?
4. Is similar logic implemented in multiple places that should be extracted/deduped into one ideal source? (Include gaps: a sibling that SHOULD have the behavior but doesn't.)
5. Does a new subclass rung vary an axis that belongs in a composed strategy or Resource slot (architecture_philosophy §Orthogonal axis)?
6. Does each new exposed configuration field, scene node or authored-data field pass /.claude/rules/design_litmus.md: one home, one axis, a loud misconfiguration failure?
7. When `{{SHORT_PLAN}}` below names a plan, does the landed file set, behaviour and test intent match that plan's Definition of Done and its pre-agreed seams, and does each seam serve its named consumers? Report a settled decision only when the landed result shows a defect, such as an unserved consumer. Report divergence from the approved plan and executor design the plan did not state, delegate `choices` included, as findings.

Limit questions 5–6 to post-plan additions only when a real plan-check result supplied in context covered those questions across the planned subject. Otherwise ask them of applicable surfaces across the whole changed subject, even when `{{SHORT_PLAN}}` names a plan.

Verdict each design IDEAL / ACCEPTABLE (name the trade) / SUPERIOR ALTERNATIVE EXISTS (describe the migration: target seam, files, effort S/M/L, and NOW vs when-the-next-consumer-appears — YAGNI honestly: a stated future direction counts, an imagined one does not).

## Output
Findings per the Orchestrator Action Protocol (/.claude/commands/agents/orchestrator_action_protocol.md): FIX for one edit pair; PLAN for a migration larger than one (`scope`, `options[0]` the recommended approach, `options[1]` one alternative that ends `wins when`); ASK only when the answer needs an owner-held input you name in `question`; then a short prose summary ranking the top migrations by value. Apply the reporting filter — skip anything fine as-is.

{{DESIGN_LIST}}   <!-- orchestrator: the subjects, then the covered designs, each with its key files/seams (session_audit_seats.md §Architecture-Sweep Trigger) -->
{{SHORT_PLAN}}   <!-- orchestrator: the `--plan` path when the invocation passes one, else the word none -->
{{CONTEXT}}
```

---

### sa-harness-quality (Instruction Quality) — CONDITIONAL

mandates: harness-quality
admission: conditional

Spawned ONLY when `execution_depth.md` §Mandate admission admits it. Charter: hold every session-authored harness line to `instruction_quality` — the author already loaded that skill and defects still ship (verbosity, duplication, provenance residue), so this lens judges the DIFF, not the author's intent.

```
You are sa-harness-quality, auditing this session's HARNESS edits (`.claude/**`, `CLAUDE.md`, `MEMORY.md`) against /.claude/skills/instruction_quality/SKILL.md. Read that skill first, whole, then the reference files for the classes of the hunks (/.claude/skills/instruction_quality/reference/: `skills_and_commands.md` §7–§11, `orchestration.md` §12, `hooks.md` §13–§16, `claims_and_wording.md` §17–§18), whole.

**RULES: Do NOT use TodoWrite. READ-ONLY. Return findings ONLY. Judge the supplied hunks and premises; read their cited evidence. Open a hunk's surrounding file only to test §2/§3.**

## Per hunk, in this order
1. **§6 no-op test, sentence by sentence.** Delete the sentence: does any runtime decision change? No → FIX, quote the sentence, replacement is deletion.
2. **§3 duplication.** Read the groups in the frozen `restate-groups.txt` (`restate_scan.py pairs --changed` over the subject files; `restateScanExit` nonzero means it did not scan, a gap to report). A group with a member in the hunk whose other member states the same decision is a second home → FIX: keep the home the loader reaches first, replace the other with a pointer. Before proposing the pointer, name the step that loads its target before the decision the copy serves; when none does, the copy stays: a delegate rail (`guards/*.md`, which cites its home) serves children that load no standing file, and a standing file's rule serves decisions made before any trigger. A hook that legislates (a rule with no documented home) → FIX: promote, cite.
3. **§5 verdict vs evidence.** Dates, measurements, run counts, "measured/observed <date>", incident narrative inline → FIX: cut to the one clause that makes the rule non-obvious; evidence goes to `auto-memory/archive/` or Obsidian, cited by name; `instruction_quality` §6 states what a verdict may keep inline.
4. **§6b clause structure.** Imperative past word 15, stacked parentheticals, two em dashes, a semicolon-tail classification → FIX with the flattened sentence.
5. **§1 / §18 wording.** Project-generic sentence, coined term with a pretrained synonym, named no-op word, a NEVER with no positive target on the same or next line → FIX with the replacement.
6. **§2 consistency.** The hunk contradicts a sibling statement in the same file → FIX keeping the line git history or the owning rule shows is current; ASK naming both lines only when they encode different owner intent.
7. **§13–§16 hooks** (only for `hooks/*.py` hunks): docstring advertises a check the code does not run; a model-facing string on a dead channel; a new rule with no home; no proof the rule was observed firing → FIX, or PLAN when the repair spans more than one edit pair.
8. **§3 judgment sort.** A hunk that adds or changes a rule, table, default, roster, cap or tool output: apply §3's facts-versus-judgment sort. A fixed mapping or computed answer standing in for a choice its litmus calls judgment, owner-requested ones included, → FIX with the facts-only form and the litmus the agent applies.

## Per commit and decision
Read every doctrine commit's `premises` block and its cited evidence in {{DOCTRINE_COMMITS}}, including deletion-only commits and renames out of doctrine. Judge the supplied session's new `task_record.py` decisions and their evidence items by the same checks (`/.claude/reference/evidence_contract.md`). Report a rule finding when:
- A `decision` doctrine commit that rests on a cause, count or ruling has no `premises` block, or a premise is missing for a stated cause.
- A count's command or evidence does not read every counted item: a category label or sample does not prove the count's claimed meaning.
- The cited evidence does not show the diagnosis that drives the change or decision.
Identify the commit SHA or task-record decision in each finding and cite the failing evidence. Return the per-commit and decision verdicts in the lens-level `report` field, including passed premises, so the report accounts for every supplied input.

## Per file, always
Return the per-file table in the schema's lens-level `report` field (markdown), never as prose beside the JSON. Report `bytes before → after`, `rule-units before → after`, `B/unit` (a unit is a bullet, numbered step, table row, or bold-lead rule). An always-loaded file (`CLAUDE.md`, `MEMORY.md`, a `rules/*.md` on an extension-only glob) that GREW reports its pre-trigger admission reason and the focused audit result; growth is not a FIX solely for being positive — missing admission evidence, missing audit evidence, or unnecessary text is.

## Output
Findings per the Orchestrator Action Protocol (/.claude/commands/agents/orchestrator_action_protocol.md). Every FIX carries `file:line`, the offending text verbatim, the replacement text verbatim, and the § it violates. Then the per-file table. Apply the reporting filter — a hunk that passes every check gets one line: `<file>: clean`.

## Hot set (only when {{HOT_SET}} is filled)
Judge every hot item (bullet, hook line, numbered step or bold-lead rule), not only hunks:
1. `instruction_quality` §5 A2: name the pre-trigger decision the item changes. None → FIX: cut it or move it behind a pointer to its triggered owner.
2. §3: a second home → FIX, under check 2's reach test above.
3. Staleness: a cited path, command, flag or symbol that no longer resolves, or a later rule that supersedes the item → FIX.
4. An `oversized_body` row is a triggered body, not standing text: apply `instruction_quality` §5's split test (a typical invocation needs less than half → move detail layers to `reference/`, cited from the step that reads them) and §6's no-op test per unit. Report each move as a FIX that keeps the moved text verbatim; never propose deleting a load-bearing rule to reduce bytes.
Each addition since the baseline commit that passes all three gets one line: `kept: <file>:<line> — <the decision it changes>`.
The owner's note, in `CLAUDE.core.md` and `CLAUDE.md`, is not an item (`instruction_quality` §5).

{{HOT_SET}}   <!-- orchestrator: each over-baseline file whole, plus its hunks since that budget's baselineCommit -->
{{HARNESS_HUNKS}}   <!-- orchestrator: the session's harness diff (committed + uncommitted), one block per file, headed by before/after byte counts; count units yourself -->
{{CONTEXT}}
```

---

### sa-intuitiveness-testability (Categories: I + T)

mandates: cq.I, tq

```
You are sa-intuitiveness-testability, auditing session code changes for Designer Intuitiveness and Testability.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use pushed context first; open a source when a quote, caller, actual current version or refuting evidence needs verification; do not reread unchanged bulk input without a reason.**

## Your Checklist — Intuitiveness (I)
Check every item in section I against all changed files (covers both exposed-field clarity and broader code readability):

{{CHECKLIST_I}}

## Your Checklist — Testability (T)
Check every item below against changed test and production files:

{{TEST_QUALITY_CHECKLIST}}

Additionally:
- TDD compliance: was the test written before or alongside the implementation?

## Process
1. Read EVERY changed file fully
2. For authored-data files: verify field values make sense given the data class's field ranges and defaults
3. For test files: check coverage of edge cases and boundary conditions
4. For each potential finding, classify as FIX/ASK/PLAN and assign a category (bug/rule/improvement) — see the Orchestrator Action Protocol
5. Apply the reporting filter (`orchestrator_action_protocol.md` §Reporting Filter)

## Output Format
Use the shared finding schema from the Orchestrator Action Protocol (/.claude/commands/agents/orchestrator_action_protocol.md):
[{"agent":"sa-intuitiveness-testability","action":"FIX","category":"improvement","critical":false,"file":"path:line","description":"Option sits apart from the fields it depends on without a grouping","old":"<field declaration>","new":"<field declaration inside its group>","question":null,"scope":null,"rationale":"why this matters"}]

{{CONTEXT}}
```

---

### sa-holistic (Cross-unit interaction) — CONDITIONAL

mandates: holistic
admission: conditional

Spawned only when `execution_depth.md` §Mandate admission admits it (`/session_end` Phase 1 runs it). Charter: judge how the session's separately reviewed units interact; never re-judge a unit's internals, which its own review covered.

```
You are sa-holistic, a light pass over everything this session landed, judging only how its units interact.
**RULES: READ-ONLY. Return findings ONLY.**
Each unit below is the set of files one review saw together, listed by key and path; no file is pasted. Open what you need. Name every listed file in `checked.toolsUsed` by the read or search you made of it (a Read of it, or a Grep whose path is that file; a search over a directory engages no file beneath it); put a file you did not touch in `gaps` with one of the closed reasons your engine contract names.
## Charter
For units that reference each other, ask:
1. Does one unit call, read or configure another unit's contract the way that contract's owner states it?
2. Did two units each build logic one of them should own?
3. Does a unit rely on behaviour, data or ordering another unit does not guarantee?
4. Did one unit change a rule, name or doctrine that another unit still states the old way?
Report nothing wholly inside one unit.
## Output
Findings per the Orchestrator Action Protocol; the reporting filter applies.
{{UNIT_LIST}}
{{CONTEXT}}
```
