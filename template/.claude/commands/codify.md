---
description: Fix a failure in how agents decide at its root, or land a procedure you keep running by hand; runs unasked when a correction or a found failure traces to how agents decide. Use /autolearn to find signals
disable-model-invocation: false
---

Take ONE correction or ONE procedure you keep running by hand, and land it where it will be in context when the decision recurs. Every fix to a failure in how agents decide or behave (a rule ignored, missing or misleading) runs `/codify`, asked or not (`instruction_quality` H3), because a fix written outside it layers a patch where the root belongs. Harness code that broke its own design is fixed like any code (`rules/harness_tooling.md`); a design that failed (such as what it makes an agent remember, match or choose) runs `/codify`. A user-invoked workflow whose loaded contract names `/codify` runs `/codify`. Placement rules are owned by `instruction_quality` §3 and §5; this command orders them into one procedure and adds the duplicate check (Step 0) and the firing proof (Step 7).

`/autolearn` detects signals across a whole session and owns the quality gates; this command routes one item you already have.

## Arguments

- `/codify <text>` — codify that item.
- **Bare `/codify`** — take the immediately-preceding correction and echo your reading back for confirmation before Step 0. If nothing is recoverable (invoked cold, or after a compaction), run `/autolearn`'s Step 0 compaction recovery; if that yields nothing, say so and stop. Never reconstruct a plausible correction — a fabricated one passes Step 0 triage cleanly, which is the silent failure.
- **Caller contract `/autolearn`** — it runs Steps 0–5 on each signal it found, puts the decided rows in its packet, and runs Steps 6–7 on each row the owner approves. No echo-back: the packet is the confirmation.
- **More than one item** — split, confirm the split, run sequentially.

## Independent derivation (an owner's correction)

For an owner's correction, two derivations of Steps 0–5 meet before Step 6. A `/autolearn` packet row skips this: its packet is the owner's review. The skip covers the derivation only; Step 6's review still runs on that row.
Read [`codify_derivation.md`](reference/codify_derivation.md) at this pass for its steps: the evidence packet, dispatch, seal, weigh.

## Step 0 — Does it already exist?

Follow the implementing branch for the failed action (`instruction_quality` §17).

1. **Name the choice that went wrong** from the owner's own words, every message they sent on the subject read verbatim (`session_digest.py` owner prompts), never the session's paraphrase: the agent decision that produced every complaint in the correction, not the symptom the correction names. A complaint that traces to a different choice is a separate item (Arguments).
2. **Name the earliest stage that made it** (a design, a board, a plan, a brief, or the edit itself) from the artifact that first records it: a choice a plan or brief fixed is that stage's choice, and the later stages only carried it out.
3. **Name what made that choice costly:** the limit, default, guard or missing check in the harness that turned it into waste or harm. Fix that first: delete or narrow it where its removal removes the cause (`CLAUDE.core.md` §Core Principles). A rule teaching agents to work around a mechanism the harness can change is a symptom fix. Show from the evidence (error text, transcript, a rerun that varies the suspect) that the cause you name produced this failure; a cause read off an error message is a hypothesis.
4. **Diagnose execution before redesigning the rule:** did the existing procedure's steps run, does its infrastructure work, and is the failure in the procedure itself? Only a fundamentally flawed procedure gets a rewrite; a skipped step gets emphasis or clarity on that step.
5. **Search for the rule and its fix history.** Semantic-search `.claude/` for the rule at that choice before writing anything. Read each hit's fix history: `python3 .claude/tools/autolearn_outcomes.py history --target <hit path> [--target <hit path>]...`, and, for fixes older than the ledger, the hit's memory file and the home's `git log`. A fix of the same choice may have landed in another home or outside `/codify`, so also search by the choice: `git log --oneline -i --grep '<the choice's terms>' -- .claude/` and the ledger's signal and reason text (`rg -i '<terms>' .claude/autolearn_outcomes.jsonl`). For a shared-doctrine choice, search the baseline clone too (`baseline_sync.py` `CACHE_CLONE`): `git -C <that clone> log --all -i --grep '<the choice's terms>'`; the publish commit's body carries each row's reason.
6. **Call a repeat a repeat.** An earlier fix for the same choice makes this a repeat: name what it changed, whether the observation it named ever ran, and why it did not hold, and fix that cause (`instruction_quality` §3b *A repeat fixes*). A repeat appended to a memory file is evidence, never the fix. For a run of recent fixes to the same home, follow the redesign and regression-read principle in `instruction_quality` §3b *A repeat fixes*.
7. **Read the target file itself** for the same obligation in other words. A hit there is the one owner: strengthen or correct it, and delete any restatement you find (`instruction_quality` §3). When the hit already prescribes the fix, apply that prescription; using it only to pick a home repeats the miss. Owning the rule does not make its file the right home; the reachability row below decides where the owner lives. A rule CLAUDE.md or the system prompt already carries, or a hook already enforces, lands no new prose; a violation of it is a reachability or enforcement fix below.

**Done: the choice that went wrong, the earliest stage that made it with the artifact line recording it, the mechanism that made it costly with the evidence tying it to this failure, the search query and its top hits (`path:section`) appear in your report. The diagnosis's premises, written as the landing commit's `premises` block per [`reference/evidence_contract.md`](../reference/evidence_contract.md), are each VERIFIED before landing by `python3 .claude/tools/verify_claims.py <file> --session <session id>`, the file holding the block's lines as one JSON array; a premise that is not VERIFIED is fixed or dropped.**

| Finding | Fix | Wrong fix |
|---|---|---|
| Exists, wasn't loaded at decision time | reachability — run Step 4 on the rule itself. When its home's trigger does not fire at the decision, move the whole rule to the row Step 4 picks and cut the old home to a pointer. Keep the home only when it loads at the decision, and a pointer only when an earlier surface can hold the rule's full sentence | a new rule; a pointer that carries part of the rule |
| Exists, was loaded, was misread: the loaded text is ambiguous, a sibling line contradicts it, or its default points the wrong way. A misread that recurs across sessions, not a one-time slip, settles this row | fix that text in its owner, the contradicting sibling and the wrong default included; delete a line that steers the choice wrong in every case it covers; cut a rule that decided a judgment in advance down to the reason it weighs | a guard; a reason field the agent fills in |
| Exists, reads clearly, was loaded, was ignored. First check that the live information at the decision (an injected advisory) pointed to the right action; when it did not, the row above owns it | a first slip with no earlier fix or recorded slip for the choice: record it with Step 6's `autolearn_outcomes.py record` call and land nothing; Steps 1–5 and 7 do not apply, and the report says so. A repeat: the mechanism `instruction_quality` §3's guard sort picks: the acting tool, the decision-point text (or a stronger word per §6's no-op test and §18), or a hook. The acting tool is the one that makes the choice Step 0 named, never a later tool that meets its result. A judgment that sort gives to no tool or hook takes no mechanism: record the repeat with the measure that would show the next one (Step 6's `--metric`) | restating it louder; a guard over a judgment; a tool that makes the judgment for the agent; a guard whose pass condition the agent writes |
| Exists, a guard already enforces it, and its deny names a next step that serves its purpose (`instruction_quality` §3) | no change: the deny is the fix working, and that step is its whole cost | a deny that rewrites the command for the agent; more prose at the decision |
| Exists on contradicting surfaces | hand to `/rule_consistency`, which reconciles across all surfaces; never adjudicate it here | a third copy |
| Genuinely absent | a first slip of a judgment a capable model makes unprompted (arithmetic, plain logic, a frame the session itself used elsewhere): record it as the loaded-and-ignored row records a first slip, and land nothing. Otherwise Step 1 | a rule because the owner asked for a codify pass |


## Step 1 — Classify

Judgment the agent must apply → **RULE**. A procedure you re-run by hand → **WORKFLOW** (routing prior: `commands/`, plus a `workflows/*.js` script when it fans out).

**WORKFLOW skips Steps 2–5.** It lands as a command per `instruction_quality` §9–§12 (no-op gate, done-condition per step, argument handling, contract integrity) and its proof is one invocation. A correction that yields both a rule and a workflow is two items — split per Arguments.

Before the file, name the way of thinking that failed. When it is a frame the agent read the work in (an example taken as the whole set, a proxy standing in for the goal, a value held fixed that varies in use) or a judgment a rule settled in advance, the fix is the principle the owner's words state with the reason it weighs, never a rule keyed to a surface of the case that failed (a word, a count, a table row). The agent is trusted to judge whether a failure is a frame failure or a judgment a rule settled in advance; the text names no list that decides it for them.

**Done: the classification is stated with the one property that decided it.**

## Step 2 — Generalize

Apply `instruction_quality` §3b's fit test to the rule and its mechanism. Then search the landing's home and every surface carrying the rule's class for text the landing makes redundant, stale or wrong, and remove it in the same landing (`CLAUDE.core.md` §Core Principles, smallest change).

**Done: the rule and its mechanism read in class-of-things terms, its model scope matches its evidence, any never/only/always in it is named and justified, and the text it makes redundant, stale or wrong is removed or named as kept with its reason.**

## Step 3 — Mechanizable?

Apply `reference/mechanism_litmus.md` first: a hook, gate lint or tool behavior lands only when L1-L4 hold, and its proof follows `rules/harness_tooling.md` §Every guard has a re-runnable proof. Then check the two surfaces in order, a tool boundary and then a gate over an artifact. Read [`codify_mechanize.md`](reference/codify_mechanize.md) at this step for each surface's test and where it routes.

**Done: a mechanism lands only where the litmus holds and its predicate is written out here; otherwise one line names the text home that owns the fix. A choice made from a job's shape, size, depth or the owner's intent is judgment: a tool may print the facts it weighs, never make the pick or refuse one the agent made. A tool may refuse on an observed fact (a tier claim, a path, an absent grant), because that is a fact, not a judgment; the grant is the dispatcher's judgment (`instruction_quality` H5).**

## Step 4 — Route by trigger shape

Place the rule first by the layer model (`instruction_quality` §5): the narrowest layer whose readers all face its decision before a narrower trigger could deliver it. The rows below place it among that layer's triggers.

Work top-down — the order is reach-reliability ÷ passive cost — over the rows that fire at or before the stage Step 0 named. A row that fires at a later stage (a file-path rule for a choice a plan made) is a backstop: it lands only beside a fix at that stage, never as the fix. It routes a new rule and re-routes an existing one from Step 0's reachability row.

Read [`codify_route.md`](reference/codify_route.md) for the rows: when each fires, its surface, owning home, passive cost and firing proof, and where a `paths:` rule leads.

**Done: the chosen row is named with the stage it fires at, and every row above it has a stated reason it was rejected. A rule no row fits lands nothing: record the pass, and a repeat looks for the trigger it missed.**

## Step 5 — Always-loaded admission and audit

Applies only when Step 4 lands on an always-loaded home for its readers: the CLAUDE chain, `CLAUDE.interactive.md`, or `guards/common.md` and its kind files.

A rule lands in its home, and the topic it concerns gets a `MEMORY.md` hook line with no path and no link; the line points at the topic's file and states no rule of its own, because an active rule living only in a recall index reaches no agent before its decision.

The admission standard, the byte measurement, the focused audit and the cap response are `instruction_quality` §5 A1–A5.

**Done: admission reason, byte delta, hard-cap status and audit result are recorded, or the item is re-routed.**

## Step 6 — Write, or queue

Direct `Edit` for skills, rules, commands, hooks, cold memory. A memory landing follows [`codify_memory.md`](reference/codify_memory.md), including its `How to apply:` rule; read it at this step.

**Write the verdict, not the incident.** The landed text is the rule, its trigger, and the one clause that makes it non-obvious. Its evidence goes to `auto-memory/archive/` or Obsidian, cited by name (`instruction_quality` §5).

State every landing's byte delta. Focused audit admission and growth handling follow [`instruction_quality`](../skills/instruction_quality/SKILL.md) §5. Cut only what the audit classes unnecessary.

**A landing that compensates a named model weakness records `model-rule: <JSON>` in the retirement container (`codify_landing.md` §Retirement trigger).** `tools/rule_retirement.py` owns its grammar. Resolve the driver alias through `model_registry.load()` and `resolve()`; freeze that row's `version` as `model`. Use the registry `driverNotes` fields `weakness` and `removeWhen` when they describe this compensation. Placement follows `instruction_quality` §3. A changed model flags the rule for the [rule-removal ablation](reference/codify_text_probe.md#rule-removal-ablation).

**Otherwise, record a retirement trigger only when the rule can become wrong for a named reason.** With no known invalidating condition, write no marker, omit the report row, and write no permanence label; never invent a generic "architecture/model changes" trigger.

Read [`codify_landing.md`](reference/codify_landing.md) at this step for the rest of the landing: where a trigger goes, its kinds in order and the reach of each location (§Retirement trigger); the queue for a non-load-bearing CLAUDE.md edit (§Queued edits); and the shared-doctrine flag for a touched path whose `baseline.lock.json` row is `status: tracked` (§Baseline status).

Record the pass, landed or not, unless `/autolearn` records it as a packet row: `python3 .claude/tools/autolearn_outcomes.py record --date <YYYY-MM-DD> --session <id> --target <home path> --signal <the choice that went wrong> --recommended apply --outcome applied|recorded --fix text|reach|tool|hook|remove|none --reason <why this fix; for a repeat, why the earlier one failed> [--metric <key>]`. A `recorded` row prints the earlier rows nearest its signal, by shared words and paths, of any date, target or outcome. A listed `declined` row is the owner's ruling against a fix for that choice, and it holds while its reason and signal do ([`autolearn`](autolearn.md) *Packet outcomes*). Judge same-choice from them and from Step 0 item 5's search by the choice, since a same choice in other words is not listed. When one is the same choice, the row just written wrongly recorded a first slip: run Step 6 on the repeat and record its landing, naming that row in `--reason`.

**Review the diff before the commit,** against the owner's words on the correction and against what the landing removes, sized to the change. Run the registered harness lens's audit (`orchestration` §4) over the uncommitted landing, scoped with `--files <its changed paths>`, and fold or rule each finding before committing. The review compensates the session judging its own landing under the momentum that produced the mistake, and retires when a run of landings reports no finding.

**Done: the diff is applied or the queue entry written, the pass is recorded, any justified retirement trigger is recorded (or the lifecycle row omitted when none is justified), the baseline status of every touched path is stated, and the review's findings are folded or ruled.**

## Step 7 — Prove it fires

Run the Step-4 proof for the chosen surface and report the result. A fix for a failure also shows the failure gone: the test or run that failed before passes after. Until it does, the remedy is unproven: report it so and land no rule that asserts it. Every proof dispatch declares `args.probe: true` (`/orchestration_metrics` §Stop-gate). A proof spends only on what nothing already shows: evidence gathered earlier (the failing run, an earlier probe of the same text, A/B data on the same seat) counts and is not re-measured. Text whose miss a gate or hook catches on every artifact the text covers takes that mechanism's proof and no reader probe, since a miss costs one gate round. A rewording that keeps its decision and answers no recorded failure takes no probe, since its old-text arm cannot fail: its proof is the new sentence read against the old for the same decision. Other text that is the only mechanism takes the counterfactual probe: read [`codify_text_probe.md`](reference/codify_text_probe.md) at this step and run its text-fix mode, or its rule-removal ablation for a retirement candidate. A cold-memory or `rules/` proof uses the next search's incremental index update; use `/reindex_search` for scope changes or index drift. Registration proves wiring, not matching (`instruction_quality` §14). A reachability fix (the first table row) proves the existing mechanism handles the original failed action once the rule loads.

## Report

```
## codify — <one-line rule>

Step 0: <choice that went wrong> @ <earliest stage>: <query> → <hits, or "absent">   [branch taken]
History: <earlier fixes for the choice and why each failed, or "none"> → fix <kind>
Class:  RULE | WORKFLOW  (<deciding property>)
Rule:   <generalized statement>
Surface: <row>  — rejected above it: <row: reason>, ...
Admission and audit: <pre-trigger decision and reason; byte delta; hard-cap status; audit result> | not applicable
Surface delta: added <rules, files, checks, steps> / removed <same>
Landed: <path> (<±bytes>) | queued: <entry>   [baseline: tracked | untracked]
Lifecycle: retire-when: <trigger> | basis: <marker> | review-by: <date>   (omit this row when no marker was recorded)
Proof:  <check run> → <result>
Held-if: <the observation in later real sessions that shows the fix held, and the step or tool that reads it>
Review: <N> findings, each folded or ruled: <one clause each>  (<audit output path>)
```
