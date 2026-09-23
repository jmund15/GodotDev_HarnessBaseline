---
description: >-
  Use when reviewing or changing harness guidance, commands, hooks, rules or their model-facing output. Check clarity, single ownership, loading cost and observable enforcement.
---

# Instruction Quality Principles

Make instructions followable at the lowest useful context cost. Apply the sections relevant to the file class; do not load unrelated procedure bodies. Section numbers are cited across the harness — never renumber.

## Universal principles

### 1. Specificity over abstraction
Name the action, trigger and target. Use real paths, fields and commands. A line that changes no behavior is removable; project-generic prose needs a universal-baseline purpose. Give an abstract rule one concrete litmus, not a restating paragraph.

### 2. Internal consistency
One decision has one rule or decision tree. Check tables against prose, descriptions against bodies, examples against implementations. Collapse competing overrides instead of appending another exception.

### 3. Single source of truth
Keep each obligation in one canonical owner; other surfaces link to it. Do not list auto-loaded siblings merely to announce their existence. Hooks may inject observed state; injected rules cite their documented owner. Tier rails by the model that RECEIVES them — rails follow the receiving model, not just the parent session.

### 3a. Stateful-source authority
For any queryable-state workflow, snapshot the live owner, enumerate the complete intended population, assign one typed outcome per row, reconcile totals for rows and outcomes, and recheck revision against the source before reporting. A prior artifact or session summary is a seed, never authority; drift fails closed. Workflows whose subject is intent/history keep the conversation/transcript as their live source.

### 4. Cross-reference durability
Use named anchors rather than fragile line/section-number references for new links. Verify paths, anchors, registered commands and quoted source claims. A resolving peer file is not tracked doctrine: check git history. On a move, rename or rule change, update inbound callers, fixtures and batteries. Do not mirror counts another file owns.

### 5. Size proportional to load mode
Budget actual loaded bytes, including catalogs, invoked bodies and transitive references. A smaller file is not a saving if every caller reads its replacement unconditionally. A skill that only ever fires by name is a command — move it.

Always-loaded admission:
- **A1:** measure bytes and the before/after delta, not line count alone.
- **A2:** admit only decisions made before a trigger/search could deliver them. Same-moment detail gets a pointer.
- **A3:** enforcement sets depth: hard-denied rules need little prose; advisory rules carry the rule plus the cost of getting it wrong; unenforced, non-obvious rules need their reason/example.
- **A4:** do not duplicate unconditional startup injections; verify their model/load conditions before removing an owner.
- **A5:** inspect per-section load and before/after bytes. Each added standing line states the pre-trigger decision it must affect and why an existing or narrower surface cannot carry it. After writing, run the focused audit over the changed section. The addition need not displace text of equal size.

For triggered bodies, ~300 bytes/rule-unit is a useful target; >350 is an audit signal, not proof of bloat. At 32KB split detail layers into `skills/<name>/reference/*.md`, cited from the step that reads them; a skill past ~800 lines or a command past ~400 also splits. Preserve required obligations and reachability, not the original rule count. Every addition receives the focused audit; growth over 1.5KB or 10% also records its reason and hard-cap response. Remove only redundant, stale, duplicated, narrative or unreachable text, never a load-bearing rule to balance bytes. A still-exceeded hard cap splits or re-homes the detail and keeps the complete rule with its evidence. The CLAUDE.md byte band does not apply to skills; they are not always-loaded.

Keep verdicts in guidance; dates, measurements and incident narratives belong in cold evidence. A rule never carries its provenance: no "(owner decision)" or "by user decision" label, because the agent gains no action from it and the auto-mode classifier reads it as self-written consent. Who decided goes in the commit message or memory (`provenance_label_guard.py` blocks an added label). A scan-all catalog needs an index plus selected bodies; a reader who knows the domain needs domain-specific references. Generate selector indexes rather than maintain a second copy. Prefix-anchored path rules defer real cost; extension-wide globs merely postpone it. Memory promotion is a split: rule to its triggered owner, evidence retained. Check first whether an auto-loading skill already owns the domain; then it is an index cut, not a promotion. A relocated or cut rule has a home only when the destination states the same decision in its own sentence; shared vocabulary is not a home. `/codify` owns placement.

### 6. Conciseness — no editorial padding
Delete whole sentences that add no needed behavior. Judge against the weakest intended receiver, and test disputed reductions on the actual task. Cut lampshading, repeated summaries, historical narration and defensive scope prose. Keep a negative-scope line (`Does NOT`, `SKIP when`, a named exclusion or permission) that pre-empts a real, tempting wrong action; state it once, where the temptation arises. Keep precise lookup tables and necessary decision conditions; terseness must not erase them. Keep one compressed why-clause only where it combats a counterintuitive default. A `±` table cell or one parenthetical clause is the most evidence a verdict carries inline.

### 6b. Prose engineering — parseable clause structure
Put the action early. Use one idea and at most one em dash per sentence; flatten nested parentheses. Name the actor. State a point once rather than headline-and-restate it. A load-bearing condition gets its own clause. A classification note appended as a semicolon tail splits the action from its completion; give it its own clause. Bullet headlines state the actual fact, not a category or slogan. Telegraphic register and spec-list density are house style: flatten storms, do not prettify lists.

## Skill-specific

### 7. Description-as-trigger discipline
Descriptions state when and why to load the skill, plus real competing non-uses. Use 50–500 characters and check against sibling length (~150% of median is a review signal). Broad names need clear preconditions. A case the body handles belongs in positive scope, not SKIP.

### 8. Frontmatter convention
Skills use `description: >-`; commands use one action-first `description:` line, about 90 characters. Missing command descriptions expose the first body line in the catalog. Commands and skills can both be model-invocable; inspect registration and `disable-model-invocation` before calling either deferred.

## Command-specific

### 9. Idempotency / no-op gate
Measure current state before changing it. Define the no-change result and resume behavior; do not repeat completed work merely because the command was invoked again.

### 10. Procedure verifiability
Each work-starting step names a concrete operation and a checkable done-condition. Demand complete required coverage, not merely a list or a self-report. Procedure invocation is not completion. Prescribe evidence and gates, not unnecessary internal choreography.

### 11. Argument handling
State argument format, no-argument behavior and malformed-input behavior. Validate before work starts; examples must run against the real interface.

## Orchestration-specific

### 12. Orchestration-contract & known-failure-mode integrity
Match the actual engine's inputs, output schema and required pins/profile. Use bounded briefs and exact evidence paths; preserve full results without forcing them into the parent. Screen against `gotcha_workflow_args_generation_fidelity`, `gotcha_workflow_fanout_search_false_absence` and `gotcha_workflow_single_flight_concurrency`; match the engine's own arg guard and output schema by reading the script. Recovery must fit the tools the receiver actually has. `orchestration` owns dispatch policy; do not duplicate model/effort tables here.

## Hook-specific

§1–§6 apply to docstrings and every model-facing string a hook emits: each names an action or a condition, never a slogan.

### 13. Channel-contract validity
For PreToolUse/PostToolUse advisories, use `hookSpecificOutput.additionalContext` with exit 0. Plain stderr with exit 0 is not a model advisory. Block with exit 2 plus stderr, or with a `permissionDecision` JSON decision; `systemMessage` reaches the user. SessionStart/UserPromptSubmit stdout is model-facing. Verify the live event contract rather than assume other events behave identically. An allow decision covers the whole command: validate everything it executes.

### 14. Registration & liveness
Verify the actual settings registration and matcher. Prove clean → planted violation → restored clean through the real output channel. An empty match set is not evidence until the positive control fires. Every hook has a consumer; remove orphan registrations and expired diagnostics, because a settings entry pointing at a deleted file bricks every tool its matcher covers. A crash is not an allow result.

### 15. Bounded state & cost
Measure hook runtime and emitted context per event. Reuse existing dispatchers instead of multiplying subprocesses. Deduplicate repeated advice and cap/rotate append-only state. Shared writes are atomic; establish writer/read ordering. Internal timeouts fit the caller's deadline.

### 16. Fail posture & docstring/behavior parity
Enforcement may fail closed; advisory failures stay silent and non-blocking. Unknown observations are not healthy results. A trigger must see enough state to be right, or narrow it. Docstrings describe live behavior. Flag hardcoded environment assumptions that can drift on upgrade.

## Reference-content

### 17. Claim freshness
Verify decisive claims against current code, configuration and live tool schemas. Distinguish recorded observations from current facts and versions. Report sampling and unchecked scope; do not turn file existence or zero errors into broad correctness. Multi-file audits return evidence, not unsupported verdicts.

## Word choice

### 18. Leading words, and the negation trap
Prefer established technical terms to new labels: rename a coinage to a known term before documenting it, and define a necessary new term once. Use plain verbs such as “use” and “help.” Remove filler and vague success claims. Named no-ops, replaced with the concrete claim: additionally, crucial, delve, enhance, fostering, interplay, intricate, landscape, pivotal, showcase, testament, underscore, vibrant. Pair prohibitions with the correct action; retain genuine hard boundaries without repeating them everywhere.

## Composition with other tools

Use `/instruction_audit` for structural quality and hook validity, `/rule_consistency` for contradictions, `/test_skill` for content resistance, `/claudemd_compact` for CLAUDE.md size, `/memory_audit` for retention. `harness_growth_guard.py` reports size/density; it does not prove preserved quality. A review of actively changing inputs records drift and rechecks affected claims, not a clean verdict or a blind rerun.
