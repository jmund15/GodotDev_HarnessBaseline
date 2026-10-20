---
description: >-
  Use when reviewing or changing harness guidance, commands, hooks, rules or model-facing output.
---

# Instruction Quality Principles

Make instructions followable at the lowest useful context cost: a file gives the reader that loads it everything its decision needs at that moment, in one reading, and nothing an editor, historian or other task needs. Apply the sections relevant to the file class; do not load unrelated procedure bodies. Section numbers are cited across the harness — never renumber.

## Universal principles

### 1. Specificity over abstraction
Name the action, trigger and target: the tool call, dispatch or routing branch the rule changes, phrased for the agent's decision point, never as a user habit. Use real paths, fields and commands. A line that changes no behavior is removable; project-generic prose needs a universal-baseline purpose. Give an abstract rule one concrete litmus, not a restating paragraph. A number in a rule (a cap, count, threshold or budget) states its basis: a measured cost, a hard tool or runtime capacity, or a limit a consumer enforces. Without one, write the concern as a litmus the author applies while working; an invented number reads as precise, and authors cut work to fit it instead of judging. A bound written without a digit takes the same test: an enumerated fixed set ("the design and grounding seats"), a tool that always emits one shape, a "never again". `/instruction_audit`, plan review and drive slices run `python3 .claude/tools/extract_numbers.py` over changed rule text: every flagged number needs a row, and a rule row quotes its basis sentence from the text; otherwise the row names the number's non-rule class. `hooks/rule_number_guard.py` lists each flagged number without a row at commit; judge each, and the reviewers who run the same check judge them again.

### 2. Internal consistency
One decision has one rule or decision tree. Check tables against prose, descriptions against bodies, examples against implementations. Collapse competing overrides instead of appending another exception.

### 3. Single source of truth
Keep each obligation in one canonical owner; other surfaces link to it, and the target states the rule: two surfaces that each defer to the other leave it with no owner. A triggered file never restates a rule that CLAUDE.md, CLAUDE.core.md or the runtime system prompt already carries. Inside one file, a rule that spans its steps is stated once, in the header, and no step restates it. A new incident edits that statement. It never adds a local copy at the step where the incident happened. A boundary a hook or guard enforces (a permission denial, ownership, push or commit authority) keeps its one documented home; no command or step restates it, because a restatement adds hesitation, not safety.

That home reaches the agent before the action the guard blocks: a guard backs up an instruction the agent already had, so a denial reads as a mistake it recognizes, never as the first place it meets the rule. A guard that decides on live state needs that state visible before the call (an injected line or a named pre-check step). A deny the agent could not have anticipated is a reach defect: land the rule where the decision is made, or drop the guard.

The deny cites the rule it enforces. It states the condition this call failed and the next step, nothing else: every other allowed or denied case stays in that rule's home. The next step does what the guard exists for and names one way to take it, never an alternative the guard also accepts: the agent knows its own tools. After a load deny, that is checking the call against the loaded text and re-issuing it with the changes it calls for; after other denies it may be using a different command, making a fix, waiting out a limit or asking the owner. An unchanged retry fits only a guard whose purpose leaves the call itself correct (a wait, a fix made elsewhere); anywhere else it reduces the guard to a checkbox. A guard with several preconditions names every unmet one in a single deny; naming only the first walks the agent into a second deny on the retry. A guard decides on what it observes: a tool name, a path, a command shape, live state. A reason, class or exception line the checked agent writes into the call is never a pass condition, because the agent that made the mistake writes it. A quoted owner statement overrides a deny only where the guard can bind the quote to the target it unlocks; the agent otherwise picks which statement to quote, so a guard protecting another session's work admits only facts it observes, and a stuck peer job is stopped by its own launcher or session.

Before building a guard, or any tool output, report field or advisory an agent acts on, sort the decision. A fact an agent decides on reaches it before the decision: a startup or per-prompt line, or the step that governs the action. A note on a call a hook already admits arrives after that decision, so it is moved to the earlier surface or deleted, never added. When the facts the tool observes settle the right action, the tool that takes the action computes and applies it (an engine default, a pick, a route), and no deny or prompt makes the agent redo it. When the action needs judgment, the tool reports facts only (the measure, what changed, what is uncovered, the evidence) and never the action: no roster with blank pins, no "owed" or "run X", no default pin, ranked pick, listing order or verdict flag standing in for the choice, because an agent carries out a computed recommendation for a judgment call without adding its own judgment. Which row serves a job is judgment; a choice no judgment separates (one row on two plans, a mechanically checked step on one plan) is the tool's. Litmus: could two correct agents, given the same facts, act differently because of something the tool cannot read (how deep a defect can hide, whether one read settles it, what a step catches against what it costs)? Then it is judgment. An owner-set input (an admission floor, the rigor level itself) is a fact the tool reports; the judgment it informs stays with the agent. A guard denies only an action that is wrong in every case its predicate matches. Whether a document an action needs is in context (a skill, a reference its rule says to read first) is a fact the guard observes, never the judgment that document informs. A deny requiring that recorded load is the last resort behind the earlier surface that names it, and it stands once agents have skipped that surface: an advisory arrives with the call and cannot deliver the document whole.

Do not list auto-loaded siblings merely to announce their existence. Hooks may inject observed state; injected rules cite their documented owner. Tier rails by the model that RECEIVES them — rails follow the receiving model, not just the parent session. `/rule_consistency` runs the near-duplicate scan that finds a second home.

A correction takes the scope of its evidence. Text another model could misread is a harness defect: fix the text for every model. Clear text that one model still breaks is that model's defect: land the fix in its channel (registry `driverNotes` or `railTier` on its row or transport, or its ladder `±` cell), and make it universal only when a second model fails the same way. A model-channel fix names the weakness it compensates and the observation that retires it: a `driverNotes` entry carries both as `weakness` and `removeWhen` (validated by `model_registry.py`), and a `railTier` or `±` fix records the same pair in its evidence entry. A new or changed guard, review pass or stage, which serves every model, names the same pair in its home, so it leaves once the models it serves stop showing that weakness. A `railTier` above `detailed` carries `railTierEvidence`; `model_registry.py` validates its values.

### 3a. Stateful-source authority
For any queryable-state workflow, snapshot the live owner, enumerate the complete intended population, assign one typed outcome per row, reconcile totals for rows and outcomes, and recheck revision against the source before reporting. A prior artifact or session summary is a seed, never authority; drift fails closed. Workflows whose subject is intent/history keep the conversation/transcript as their live source.

### 3b. A fix fits its cause
These checks apply to every rule or mechanism written to fix a failure, whoever writes it (`/codify`, a plan, a drive, a fold) and whoever reviews it. With §1's basis rule, §3's facts-versus-judgment sort and §3's *A correction takes the scope of its evidence*, they are the whole fit test.
- **Overfit-to-Specific.** A rule that names a specific file, PR, ability, commit, model, run, phrasing or session date goes stale with that detail. Write the class the detail belongs to ("don't gate after touching `<name>.<ext>`" → "treat a data file that changes Logic Domain behavior as code for the gate"); the detail moves to the evidence line. Litmus: could a reader from another project apply the rule without that name?
- **No scope the source never set.** A never, only or always about a mechanism, drawn from one incident of it, bans design space the incident never tested. An owner's correction sets scope the same way: its examples stay examples, and a rule written from it, or a restatement of it to the owner, closes a set only where the owner did. A list of cases written as the definition ("`±` = X / Y") closes the set as surely as "only" does; write the principle the cases share, with the cases as open examples.
- **The mechanism takes the same gates.** A hook or gate predicate keyed to one engine, command or file is overfit when its rule covers a class: key it to data every member of the class carries.

### 4. Cross-reference durability
Use named anchors rather than fragile line/section-number references for new links. Verify paths, anchors, registered commands and quoted source claims. A resolving peer file is not tracked doctrine: check git history. On a move, rename or rule change, update inbound callers, fixtures and batteries. A sweep for a rule class seeds from the always-loaded set as well as the entrypoints: injected files are never linked, so a reference walk cannot reach them. `python3 .claude/tools/load_census.py --json <new-output.json>` writes that set (`standing` rows with their `@` imports, `rules` entries), and a delegated sweep's brief carries the list. Do not mirror counts another file owns.

### 5. Size proportional to load mode
Budget actual loaded bytes, including catalogs, invoked bodies and transitive references. A smaller file is not a saving if every caller reads its replacement unconditionally. A skill that only ever fires by name is a command — move it.

CLAUDE.md carries the owner's introductory note verbatim, under whatever heading: never condense, reword or relocate it, and any reference codes it defines stay there.

Always-loaded admission:
- **A1:** measure bytes and the before/after delta, not line count alone.
- **A2:** admit only decisions made before a trigger/search could deliver them. Same-moment detail gets a pointer.
- **A3:** enforcement sets depth: hard-denied rules need little prose; advisory rules carry the rule plus the cost of getting it wrong; unenforced, non-obvious rules need their reason/example.
- **A4:** do not duplicate unconditional startup injections; verify their model/load conditions before removing an owner.
- **A5:** inspect per-section load and before/after bytes. Each added standing line states the pre-trigger decision it must affect and why an existing or narrower surface cannot carry it. The addition need not displace text of equal size. Loaded-set byte caps and their recorded baselines live only in `load_census.py` `BUDGETS`; this section states no cap or baseline, only the per-body triggers for a review. Growth past a recorded baseline blocks nothing; `load_census.py` `over_baseline` names the budgets the close's hot-set review judges.

For a triggered body, a rule unit states one decision, its trigger and one non-obvious clause; the rest is evidence for cold storage. Split a body when a typical invocation needs less than half of it: move the detail layers into `skills/<name>/reference/*.md`, and cite each from the step that reads it. Preserve required obligations and reachability, not the original rule count. Every decision addition receives the focused audit, and an addition that adds a new decision rather than sharpening an existing one records its reason. Remove only redundant, stale, duplicated, narrative, unreachable, obvious-negation, friction or overfit text (§6), never a load-bearing rule to balance bytes. A body past `load_census`'s oversized threshold is a review trigger, never a cap: the close's hot-set review applies the split test above, a split keeps the complete rule with its evidence, and no edit trims text to meet a byte count. The CLAUDE.md byte band does not apply to skills; they are not always-loaded.

Keep verdicts in guidance; dates, measurements and incident narratives belong in cold evidence. A rule never carries its provenance: no "(owner decision)" or "by user decision" label, because the agent gains no action from it and the auto-mode classifier reads it as self-written consent. Who decided goes in the commit message or memory (`provenance_label_guard.py` blocks an added label). A scan-all catalog needs an index plus selected bodies; a reader who knows the domain needs domain-specific references. Generate selector indexes rather than maintain a second copy. Prefix-anchored path rules defer real cost; extension-wide globs merely postpone it. Memory promotion is a split: rule to its triggered owner; the note keeps only a clause the owner lacks, and a note the owner states in full is deleted. Check first whether an auto-loading skill already owns the domain; then it is an index cut, not a promotion. A relocated or cut rule has a home only when the destination states the same decision in its own sentence; shared vocabulary is not a home. `/codify` owns placement.

### 6. Conciseness — no editorial padding
Admission test per line: does it improve the decision a reader using the file makes, in the fewest non-redundant words? A true fact that informs no decision is cut; a rule only an editor needs moves to the editing owner, which must reach an edit made outside it (`/codify` Step 4), and a phrase check can keep it from drifting back. Delete whole sentences that add no needed behavior. Judge against the receiving model's rail tier (`railTier`, §3); a file several models load is judged against the tier that needs the most compensation among them (`detailed` is the bottom tier: a shorter rail is earned only by evidence), and a receiver with no measured tier counts as `detailed`. Test disputed reductions on the actual task. Cut lampshading, repeated summaries, historical narration and defensive scope prose. Cut an obvious negation that restates the step's own action ("reading the file is not following it") unless a recorded failure shows receivers skip that step. Cut friction prose: an added confirmation, "only if the user explicitly asks" or "when in doubt, ask" stays only where it guards an irreversible or out-of-scope action; elsewhere the step acts. Cut an overfit aside (§3b Overfit-to-Specific): generalize it into the owning rule, or delete it. Keep a negative-scope line (`Does NOT`, `SKIP when`, a named exclusion or permission) that pre-empts a real, tempting wrong action; state it once, where the temptation arises. Keep each row of a rationalizations-to-refuse table: a row pre-empts a tempting premise even when the body already states the rule. Keep precise lookup tables and necessary decision conditions; terseness must not erase them. Keep one compressed why-clause only where it combats a counterintuitive default. A verdict may carry a source tag or a pointer to its cold evidence (§5), never the run, date, price, vendor claim or incident itself; a number's basis (§1) may stay.

### 6b. Prose engineering — parseable clause structure
Put the action early. A sentence carries one idea; an em dash that opens a second idea becomes its own sentence. Flatten nested parentheses. Name the actor. State a point once rather than headline-and-restate it. A load-bearing condition gets its own clause. A classification note appended as a semicolon tail splits the action from its completion; give it its own clause. Bullet headlines obey `CLAUDE.core.md` §Preferences' headline rule. Telegraphic register and spec-list density are house style: flatten storms, do not prettify lists.

## Sections by file class

§7–§18 live in `reference/`, one file per file class. Read the file for the class you edit, in full; §1–§6b apply to every class.

### 7. Description-as-trigger discipline
A skill description states when to load it, then each real competing non-use. [skills_and_commands](reference/skills_and_commands.md)

### 8. Frontmatter convention
Skills use `description: >-`, commands one action-first `description:` line. [skills_and_commands](reference/skills_and_commands.md)

### 9. Idempotency / no-op gate
A command measures current state first and defines its no-change result. [skills_and_commands](reference/skills_and_commands.md)

### 10. Procedure verifiability
Each work-starting step names an operation and a checkable done-condition. [skills_and_commands](reference/skills_and_commands.md)

### 11. Argument handling
State argument format, no-argument and malformed-input behavior. [skills_and_commands](reference/skills_and_commands.md)

### 12. Orchestration-contract & known-failure-mode integrity
Match the engine's real inputs, schema and pins; doctrine names tiers, never models or efforts. [orchestration](reference/orchestration.md)

### 13. Channel-contract validity
An advisory uses `additionalContext` with a clean exit; a block uses the blocking exit code or a `permissionDecision`. [hooks](reference/hooks.md)

### 14. Registration & liveness
Registration proves wiring, not matching: prove clean, planted violation, clean. [hooks](reference/hooks.md)

### 15. Bounded state & cost
Measure a hook's runtime and emitted context; write shared state atomically. [hooks](reference/hooks.md)

### 16. Fail posture & docstring/behavior parity
A floor guard denies on its own failure; other failures report the lost check. [hooks](reference/hooks.md)

### 17. Claim freshness
Verify the claims a change rests on against current code and live schemas. [claims_and_wording](reference/claims_and_wording.md)

### 18. Leading words, and the negation trap
Prefer established terms to coinages; pair a prohibition with the correct action. [claims_and_wording](reference/claims_and_wording.md)

## Composition with other tools

Use `/instruction_audit` for structural quality and hook validity, `/rule_consistency` for contradictions, `/test_skill` for content resistance, `/claudemd_compact` for CLAUDE.md size, `/memory_audit` for retention. `harness_growth_guard.py` reports size/density; it does not prove preserved quality. A review of actively changing inputs records drift and rechecks affected claims, not a clean verdict or a blind rerun.
