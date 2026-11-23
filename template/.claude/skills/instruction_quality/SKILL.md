---
description: >-
  Use when writing or changing harness doctrine, a command, a skill, a hook or model-facing output.
---

# Instruction Quality

A file gives the reader that loads it what its decision needs at that moment, once, and nothing an editor or historian needs. Section numbers are cited elsewhere; never renumber.

### 1. Specificity
Name the action, trigger and target: the tool call or branch the rule changes, with real paths and commands. A line that changes no behaviour is cut. A number states its basis (a measured cost, a hard capacity, a limit a consumer enforces) and its reason; without one, write a litmus instead, because authors cut work to fit an invented number.

### 2. Consistency
One decision has one rule. Check tables against prose and descriptions against bodies. Collapse competing overrides; never append another exception.

### 3. Single source of truth
Each obligation has one owner; other files link to it. A triggered file never restates always-loaded text. A rule spanning a file's steps is stated once, in its header, and a new incident edits that statement.
A guard backs an instruction the agent already met before the action. Its deny cites that rule, names every unmet condition and one next step. It decides on what it observes (tool, path, command shape, live state), never on a reason the checked agent writes. When the observed facts settle the action, the tool takes it; when judgment is needed, the tool reports facts only, never a pick.
A correction takes its evidence's scope: text any model could misread is fixed for all; clear text one model breaks is fixed in that model's row, naming the weakness and the observation that retires it.

### 3a. Live state is the authority
A queryable-state workflow snapshots the live owner, covers the whole population and rechecks before reporting. A prior summary is a seed, never authority.

### 3b. A fix fits its cause
Write the class, not the instance: a reader from another project applies the rule without the incident's name. Never set scope the source did not (one incident is no "never"; a case list is not a definition). A gate keyed to one file is overfit when its rule covers a class. A repeated fix follows the redesign rule in `CLAUDE.core.md`.

### 4. Cross-reference durability
Link by name, not line or section number. Check paths with `python3 .claude/tools/verify_doc_citations.py --kinds path --only missing <files>`. On a move or rename, update inbound citers (`git grep -n -F <old>`).

### 5. Size follows load mode
Count loaded bytes, references included. Always-loaded text admits only a decision most sessions face before a trigger could deliver it. Over a byte bound, fix the cause (a restatement, a rule for deleted machinery, a long description), never a rule's reason. Split a triggered body when a typical use needs under half of it. The owner's notes stay verbatim. A rule never carries a provenance label: it gains the agent no action, and the auto-mode classifier reads it as self-written consent.

### 6. Conciseness
Keep a line only if it improves the reader's decision. Cut narration, repeated summaries, obvious negations and friction prose ("ask first" stays only before an irreversible or out-of-scope act). Keep a negative-scope line that pre-empts a real temptation, each row of a rationalizations table, lookup tables, and one why-clause where the default is counterintuitive.

### 6b. Prose
Action first. One idea per sentence. Name the actor. Flatten nested parentheses. State a point once.

### 7. Description as trigger
A skill description says when to load it, then each real competing non-use. Cut any clause a fresh agent does not need to choose it; shared frontmatter is paid on every call.

### 8. Frontmatter
Skills use `description: >-`; commands use one action-first `description:` line. `disable-model-invocation: true` hides a file from the model; never hide one a session needs.

### 9. No-op gate
A command measures current state first and defines its no-change result.

### 10. Verifiable steps
Each work step names an operation and a checkable done-condition. A step that runs a tool carries its copyable invocation. A brief carries context and gates, not steps the receiver can work out.

### 11. Arguments
State the format, the no-argument case and the malformed case. A refusal prints an `accepts:` line and an `example:` line.

### 12. Orchestration
Match the engine's real inputs and output schema by reading the script. Doctrine names the work shape, never a model or an effort value: model rows live in `.claude/seats.json`, and an effort scale differs by model.

### 13. Hook channels
An advisory is `hookSpecificOutput.additionalContext` with exit 0; a block is exit 2 with stderr, or a `permissionDecision`. An allow covers the whole command, so validate all it runs.

### 14. Registration
Prove a hook live: clean, planted violation, clean, through its real output. A settings entry naming a deleted file breaks every call its matcher covers.

### 15. Cost
Measure a hook's runtime and emitted text. Add to the event's one entry rather than a new process. Write shared state atomically.

### 16. Fail posture
A floor guard denies on its own failure; any other hook reports the lost check and continues. A docstring states live behaviour.

### 17. Claim freshness
Verify each claim a change rests on against current code and live schemas. Say what was sampled and what was not. A comment or reference file states only what its own file keeps true; a negative names the artifact that would falsify it.

### 18. Words
Use established terms over coinages; define a needed term once, in its owner. Pair each prohibition with the correct action.
