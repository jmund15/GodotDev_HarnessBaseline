---
paths:
  - ".claude/CLAUDE.md"
  - ".claude/auto-memory/MEMORY.md"
  - ".claude/commands/**"
  - ".claude/skills/**"
  - ".claude/agents/**"
  - ".claude/rules/**"
  - ".claude/reference/**"
  - ".claude/guards/**"
---

# Harness Authoring (fires when a `.claude/` markdown surface is open)

Sibling of `harness_tooling.md` (which covers `.py`/`.ps1`). Full standard: `instruction_quality` skill — load it before editing; these are the rules that bite before you would think to.

## Commands and skills

- **Command descriptions stay one action-first line** (`instruction_quality` §8 owns the cap) — skill descriptions legitimately run longer for trigger surface (`feedback_command_descriptions_one_line`).
- **Commands and skills are one mechanism — both model-invocable unless `disable-model-invocation: true`.** Skills take `description: >-`, commands one line, and `paths:` surfaces the description only, never the body (`feedback_skill_vs_command_frontmatter_convention`).

## Any harness markdown

- **CLAUDE.md carries the owner's introductory note (e.g. "A Note from <owner>") verbatim, whatever heading names it.** Never condense, reword or relocate it; any owner-defined reference codes it establishes stay there, not in a rule.
- **A loader-facing artifact holds only facts and actions needed to use it now.** Move editing, maintenance, ingest, regeneration and retirement steps to the triggered command, skill or rule. Historical evidence is exempt; a decision label is not (`instruction_quality` §5).
- **Brief a downstream receiver with context and gates only, never its internal flow.** Litmus per bullet: could the receiver work this out itself? Then it is flow prescription — cut it (`feedback_briefing_context_gates_not_flow`).
- **A harness rule names the tool call, dispatch or routing branch it changes.** These files are executed by the model, not read by the user; a rule phrased as a user habit has no decision point — translate it to its agent-facing form (`feedback_harness_rules_are_agent_actionable`).
- **Mechanism-layer files name roles, never models.** Model attributes and their evidence live only in `reference/model_ladder_evidence.md` §Role guidance, and universal delegation doctrine never lives in one command (`feedback_orchestration_skill_model_agnostic`).
- **A correction takes the scope of its evidence.** Before writing one, decide whose defect it is. Text another model could misread is a harness defect: fix the text for every model. Clear text that one model still breaks is that model's defect: land the fix in its channel (registry `driverNotes` or `railTier` on its row or transport, or its ladder `±` cell), and make it universal only when a second model fails the same way (`feedback_model_agnostic_core_scoped_overlays`). A `railTier` above `detailed` carries `railTierEvidence`; `model_registry.py` validates its accepted values.
- **Prescribe verification and artifacts, never cognition.** Fixed step orders, option counts and round caps compensate for weak models — free them; grounding, taste-forks, independent red-team and execution gates stay hard contract (`feedback_prescribe_verification_not_cognition`).
- **A comment states a fact its file can keep true.** Prose in `.claude/reference/` is read as current, so it must not assert world state another artifact owns. A negative names the artifact that would falsify it; closing a campaign includes the row it scores (`gotcha_registry_prose_asserts_world_state`).

<!-- retire-when: review-by: 2027-03-07 -->
