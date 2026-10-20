# Skill and command files — §7–§11

Read in full when the edit changes a skill or a command. `SKILL.md` §1–§6b apply to it too.

## Skill-specific

### 7. Description-as-trigger discipline
Descriptions state when and why to load the skill: trigger first, then every real competing non-use. Cut any clause a fresh agent does not need to choose it, including a sibling that names no real competitor. Broad names need clear preconditions. A case the body handles belongs in positive scope, not SKIP. `python3 .claude/tools/load_census.py --budgets --caps` reports description length as an audit signal.

### 8. Frontmatter convention
Skills use `description: >-`; commands use one action-first `description:` line, pure literal. Workflows set `meta.description` plus ` - ` plus `meta.whenToUse` when present, both pure literals. A description's content follows §7's litmus. Missing command descriptions expose the first body line in the catalog. Commands and skills can both be model-invocable; inspect registration and `disable-model-invocation` before calling either deferred. A skill's `paths:` surfaces its description, never its body.

## Command-specific

### 9. Idempotency / no-op gate
Measure current state before changing it. Define the no-change result and resume behavior; do not repeat completed work merely because the command was invoked again.

### 10. Procedure verifiability
Each work-starting step names a concrete operation and a checkable done-condition. Demand complete required coverage, not merely a list or a self-report. Procedure invocation is not completion. Prescribe evidence and gates, not unnecessary internal choreography: a brief to a downstream receiver carries context and gates only, and a line the receiver could work out itself is flow, so cut it.

### 11. Argument handling
State argument format, no-argument behavior and malformed-input behavior. Validate before work starts; examples must run against the real interface.
