---
description: Codify a known correction or workflow on an explicit request, a never-recur request or a named caller contract; use /autolearn to find signals
disable-model-invocation: false
---

Take ONE just-issued correction ("never let this happen again") or ONE procedure you keep running by hand, and land it where it will be in context when the decision recurs. Model invocation is allowed only after the current user explicitly asks for `/codify` or codification (a request that a failure never recur is one), or when a user-invoked workflow's loaded contract names `/codify`; a complaint that asks for no lasting fix is not an invocation. Placement rules are owned by `instruction_quality` §3 and §5; this command orders them into one procedure and adds the duplicate check (Step 0) and the firing proof (Step 7).

`/autolearn` is the sibling, not the overlap: it DETECTS signals across a whole session and owns the quality gates. This command routes one item you already have.

## Arguments

- `/codify <text>` — codify that item.
- **Bare `/codify`** — take the immediately-preceding correction and echo your reading back for confirmation before Step 0. If nothing is recoverable (invoked cold, or after a compaction), run `/autolearn`'s Step 0 compaction recovery; if that yields nothing, say so and stop. Never reconstruct a plausible correction — a fabricated one passes Step 0 triage cleanly, which is the silent failure.
- **Caller contract `/autolearn`** — it runs Steps 0–5 on each signal it found, puts the decided rows in its packet, and runs Steps 6–7 on each row the owner approves. No echo-back: the packet is the confirmation.
- **More than one item** — split, confirm the split, run sequentially. One item per pass keeps each independently revertible.

## Independent derivation (an owner-requested pass)

When the owner asks for the codification, two derivations of Steps 0–5 meet before Step 6, because the session that made the mistake diagnoses it under the same momentum that produced it. A `/autolearn` packet row skips this: its packet is the owner's review.
Read [`codify_derivation.md`](reference/codify_derivation.md) at this pass for its steps: brief, seal, dispatch, weigh.

## Step 0 — Does it already exist?

Follow the implementing branch for the failed action (`instruction_quality` §17). Name the choice that went wrong first: the agent decision that produced every complaint in the correction, not the symptom the correction names. A complaint that traces to a different choice is a separate item (Arguments). Then name what made that choice costly: the limit, default, guard or missing check in the harness that turned it into waste or harm, and fix that first: delete or narrow it where its removal removes the cause (`CLAUDE.core.md` §Core Principles). A rule teaching agents to work around a mechanism the harness can change is a symptom fix. Show from the evidence (error text, transcript, a rerun that varies the suspect) that the cause you name produced this failure; a cause read off an error message is a hypothesis. Diagnose execution before redesigning the rule: did the existing procedure's steps run, does its infrastructure work, and is the failure in the procedure itself? Only a fundamentally flawed procedure gets a rewrite; a skipped step gets emphasis or clarity on that step. Semantic-search `.claude/` for the rule at that choice before writing anything. Read each hit's fix history: `python3 .claude/tools/autolearn_outcomes.py history --target <hit path> [--target <hit path>]...`, and, for fixes older than the ledger, the hit's memory file and the home's `git log`. A fix of the same choice may have landed in another home or outside `/codify`, so also search by the choice: `git log --oneline -i --grep '<the choice's terms>' -- .claude/` and the ledger's signal and reason text (`rg -i '<terms>' .claude/autolearn_outcomes.jsonl`). An earlier fix for the same choice makes this a repeat: name what it changed and why it did not hold, and land a different kind of fix chosen by that cause. A second fix of the same kind for the same choice repeats the miss, and a repeat appended to a memory file is evidence, never the fix. Then read the target file itself for the same obligation in other words. A hit there is the one owner: strengthen or correct it, and delete any restatement you find (`instruction_quality` §3). When the hit already prescribes the fix, apply that prescription; using it only to pick a home repeats the miss. Owning the rule does not make its file the right home; the reachability row below decides where the owner lives. A rule CLAUDE.md or the system prompt already carries, or a hook already enforces, lands no new prose; a violation of it is a reachability or enforcement fix below.

**Done: the choice that went wrong, the mechanism that made it costly with the evidence tying it to this failure, the search query and its top hits (`path:section`) appear in your report. The diagnosis's premises, written as the landing commit's `premises` block per [`reference/evidence_contract.md`](../reference/evidence_contract.md), are each VERIFIED before landing by `python3 .claude/tools/verify_claims.py <file> --session <session id>`, the file holding the block's lines as one JSON array; a premise that is not VERIFIED is fixed or dropped.** The commit advisory's `restate_scan` can flag duplicates from a skipped Step 0; `/rule_consistency` adjudicates them.

| Finding | Fix | Wrong fix |
|---|---|---|
| Exists, wasn't loaded at decision time | reachability — run Step 4 on the rule itself. When its home's trigger does not fire at the decision, move the whole rule to the row Step 4 picks and cut the old home to a pointer. Keep the home only when it loads at the decision, and a pointer only when an earlier surface can hold the rule's full sentence | a new rule; a pointer that carries part of the rule |
| Exists, was loaded, was misread: the loaded text is ambiguous, a sibling line contradicts it, or its default points the wrong way. A misread that recurs across sessions, not a one-time slip, settles this row | fix that text in its owner, the contradicting sibling and the wrong default included; delete a line that steers the choice wrong in every case it covers | a guard; a reason field the agent fills in |
| Exists, reads clearly, was loaded, was ignored. First check that the live information at the decision (an injected advisory) pointed to the right action; when it did not, the row above owns it | a first slip with no earlier fix or recorded slip for the choice: record it with Step 6's `autolearn_outcomes.py record` call and land nothing; Steps 1–5 and 7 do not apply, and the report says so. A repeat: the mechanism `instruction_quality` §3's guard sort picks: the acting tool, the decision-point text (or a stronger word per §6's no-op test and §18), or a hook; after a text fix failed, the acting tool or a hook. The acting tool is the one that makes the choice Step 0 named, never a later tool that meets its result | restating it louder; a guard over a judgment; a guard whose pass condition the agent writes |
| Exists, a guard already enforces it, and its deny names a next step that serves its purpose (`instruction_quality` §3) | no change: the deny is the fix working, and that step is its whole cost | a deny that rewrites the command for the agent; more prose at the decision |
| Exists on contradicting surfaces | hand to `/rule_consistency` | a third copy |
| Genuinely absent | Step 1 | — |


**Division of labour with `/rule_consistency`:** Step 0 *detects* a cross-surface duplicate at save time; `/rule_consistency` *compares and reconciles* across all surfaces as a sweep. Never adjudicate the contradiction here.

## Step 1 — Classify

Judgment the agent must apply → **RULE**. A procedure you re-run by hand → **WORKFLOW** (routing prior: `commands/`, plus a `workflows/*.js` script when it fans out).

**WORKFLOW skips Steps 2–5.** It lands as a command per `instruction_quality` §9–§12 (no-op gate, done-condition per step, argument handling, contract integrity) and its proof is one invocation. A correction that yields both a rule and a workflow is two items — split per Arguments.

**Done: the classification is stated with the one property that decided it.**

## Step 2 — Generalize

Apply `instruction_quality` §3b's fit test to the rule and its mechanism. Then search the landing's home and every surface carrying the rule's class for text the landing makes redundant, stale or wrong, and remove it in the same landing (`CLAUDE.core.md` §Core Principles, smallest change).

**Done: the rule and its mechanism read in class-of-things terms, its model scope matches its evidence, any never/only/always in it is named and justified, and the text it makes redundant, stale or wrong is removed or named as kept with its reason.**

## Step 3 — Mechanizable?

Two mechanization surfaces, checked in order:

- **A tool boundary.** Can a `PreToolUse`/`PostToolUse` matcher *decide* it from a command shape, file path, tool name or live state, never from a statement the agent writes into the call? The matcher reads the call that makes Step 0's choice (the edit that writes the file, the plan or brief that orders the run), never a later call that only meets its result (the commit, the delegate's run); a later check is a backstop only when that call cannot be decided, and the report names why. Then it routes to a hook **plus one documented home** (`instruction_quality` §3) that the agent reads before the action the hook blocks. A rule that would reach the agent only through the hook's own deny makes the hook its teacher: land the home at the decision point first, or add no hook. A rule whose only home is a hook file is invisible to doctrine and exempt from `/rule_consistency`; a rule restated beyond that one home adds hesitation.
- **A gate over an artifact.** Can a script or a read-only lens *decide* it from a diff, a plan file, a PR body, or the repo state at gate time? Then it routes to the gate that already runs there — a lint in the project's regression gate (`change_control` §Gate cadence names it), a Claude-side grep or lens in the coding layer's pre-PR battery or plan-review panel, or a registry template in `agents/*_agents.md` (via `tools/lens.py`) — and the command that runs the gate is its documented home.

**Done: the matcher condition or the gate predicate is written out, or one line says why the rule needs judgment neither can supply.**

## Step 4 — Route by trigger shape

Each row's rule is owned elsewhere; this table composes them into an ordered procedure and adds the proof column. Work top-down — the order is reach-reliability ÷ passive cost. It routes a new rule and re-routes an existing one from Step 0's reachability row.

| Fires when… | Surface | Owning home | Passive cost | Firing proof |
|---|---|---|---|---|
| a file of a **prefix-anchored class** (below the repo root) is touched | `rules/<name>.md` + `paths:` | `instruction_quality` §5 | zero until matched — but a broad glob matches nearly every session | the glob string is prefix-anchored below the repo root — decidable by reading it, no tooling |
| an agent **edits** a specific artifact (an editing rule no user of the file needs) | the artifact's maintaining command (`/ladder_ingest`, `/worklog`, `/sync_baseline`, or the project's roadmap command); a judgment rule for the file also takes its `hooks/harness_edit_skill_reminder.py` `EDIT_NOTES` entry, which reaches an Edit/Write made outside the command: the rule's full sentence word for word as the owner states it, since the owner does not load on a direct edit, and a proof holding the two equal; with no command, a check that decides it (Step 3), else the `EDIT_NOTES` entry alone; `instruction_quality` only for a rule covering a class of files, never one file | `instruction_quality` §6 | zero until that edit | the rule appears in no file a reader loads to *use* the artifact, and an edit of the artifact receives it |
| an agent deliberately enters a domain | `auto-memory/archive/` (cold) | `instruction_quality` §5 | zero | the NL paraphrase an agent would search returns it top-ranked |
| the user types it | `commands/` | `instruction_quality` §5 | zero until invoked | a loaded surface names it at signal-time |
| a tool call matches a condition | `hooks/` **+ documented home** | `instruction_quality` §3 | one interpreter spawn per matched call | pass → deliberate violation → revert; the home loads before the blocked action |
| a gate runs over an artifact (diff, plan, PR body, repo state) | lens or lint inside that gate's command (Step 3) | `instruction_quality` §12 | zero until the gate runs | a planted violation yields the finding; a clean input yields none; a lens runs at the pin its gate runs at |
| a named task-shape begins | skill | `instruction_quality` §5, §7 | description bytes, every session | a fresh agent given only the description picks it |
| every session, and no trigger exists | CLAUDE.md **or** a MEMORY.md hot topic file | `instruction_quality` §5 | every byte, every session | Step 5 |

A `paths:` rule loads on every read of a matching file, and a harness `.md` is read to be executed, so an editing rule never lands there. `rules/` + `paths:` leads **on a prefix-anchored glob** — the only surface both guaranteed-to-load and genuinely free until matched. On a broad glob (`**/*.cs`, `**/*.tscn`, `**/*.md`) the cost is deferred, not avoided, and the row re-ranks below cold memory; width is about session shape, not file count (`Tests/**/*.cs` stays narrow because a gameplay session never opens `Tests/`). Hooks sit mid-table despite the strongest enforcement: they cost a spawn per matched call and can never stand alone.

**Done: the chosen row is named, and every row above it has a stated reason it was rejected.**

## Step 5 — Always-loaded admission and audit

Applies only when Step 4 lands on CLAUDE.md or a MEMORY.md hot topic file.

Write a MEMORY.md hook line with no path and no link. The path costs more than the hook it points at.

The admission standard, the byte measurement, the focused audit and the cap response are `instruction_quality` §5 A1–A5.

**Done: admission reason, byte delta, hard-cap status and audit result are recorded, or the item is re-routed.**

## Step 6 — Write, or queue

Direct `Edit` for skills, rules, commands, hooks, cold memory. A memory landing follows [`codify_memory.md`](reference/codify_memory.md), including its `How to apply:` rule; read it at this step.

**Write the verdict, not the incident.** The landed text is the rule, its trigger, and the one clause that makes it non-obvious. Its evidence goes to `auto-memory/archive/` or Obsidian, cited by name (`instruction_quality` §5).

State every landing's byte delta. Focused audit admission and growth handling follow [`instruction_quality`](../skills/instruction_quality/SKILL.md) §5. Cut only what the audit classes unnecessary.

**A landing that compensates a named model weakness records `model-rule: <JSON>` in the retirement container (`codify_landing.md` §Retirement trigger).** `tools/rule_retirement.py` owns its grammar. Resolve the driver alias through `model_registry.load()` and `resolve()`; freeze that row's `version` as `model`. Use the registry `driverNotes` fields `weakness` and `removeWhen` when they describe this compensation. Placement follows `instruction_quality` §3. A changed model flags the rule for the [rule-removal ablation](reference/codify_text_probe.md#rule-removal-ablation).

**Otherwise, record a retirement trigger only when the rule can become wrong for a named reason.** With no known invalidating condition, write no marker, omit the report row, and write no permanence label; never invent a generic "architecture/model changes" trigger.

Read [`codify_landing.md`](reference/codify_landing.md) at this step for the rest of the landing: where a trigger goes, its kinds in order and the reach of each location (§Retirement trigger); the queue for a non-load-bearing CLAUDE.md edit (§Queued edits); and the shared-doctrine flag for a touched path whose `baseline.lock.json` row is `status: tracked` (§Baseline status).

Record the pass, landed or not, unless `/autolearn` records it as a packet row: `python3 .claude/tools/autolearn_outcomes.py record --date <YYYY-MM-DD> --session <id> --target <home path> --signal <the choice that went wrong> --recommended apply --outcome applied|recorded --fix text|reach|tool|hook|remove|none --reason <why this fix; for a repeat, why the earlier one failed> [--metric <key>]`. An approved `/eval_dashboard` move lands through this pass, and its `metric` key goes in `--metric` so the dashboard measures the fix.

**Done: the diff is applied or the queue entry written, the pass is recorded, any justified retirement trigger is recorded (or the lifecycle row omitted when none is justified), and the baseline status of every touched path is stated.**

## Step 7 — Prove it fires

Run the Step-4 proof for the chosen surface and report the result. A fix for a failure also shows the failure gone: the test or run that failed before passes after. Until it does, the remedy is unproven: report it so and land no rule that asserts it. Every proof dispatch declares `args.probe: true` (`/orchestration_metrics` §Stop-gate). A proof spends only on what nothing already shows: evidence gathered earlier (the failing run, an earlier probe of the same text, A/B data on the same seat) counts and is not re-measured. Text whose miss a gate or hook catches on every artifact the text covers takes that mechanism's proof and no reader probe, since a miss costs one gate round. A rewording that keeps its decision and answers no recorded failure takes no probe, since its old-text arm cannot fail: its proof is the new sentence read against the old for the same decision. Other text that is the only mechanism takes the counterfactual probe: read [`codify_text_probe.md`](reference/codify_text_probe.md) at this step and run its text-fix mode, or its rule-removal ablation for a retirement candidate. A cold-memory or `rules/` proof uses the next search's incremental index update; use `/reindex_search` for scope changes or index drift. Registration proves wiring, not matching (`instruction_quality` §14). A reachability fix (the first table row) proves the existing mechanism handles the original failed action once the rule loads.

## Report

```
## codify — <one-line rule>

Step 0: <choice that went wrong>: <query> → <hits, or "absent">   [branch taken]
History: <earlier fixes for the choice and why each failed, or "none"> → fix <kind>
Class:  RULE | WORKFLOW  (<deciding property>)
Rule:   <generalized statement>
Surface: <row>  — rejected above it: <row: reason>, ...
Admission and audit: <pre-trigger decision and reason; byte delta; hard-cap status; audit result> | not applicable
Surface delta: added <rules, files, checks, steps> / removed <same>
Landed: <path> (<±bytes>) | queued: <entry>   [baseline: tracked | untracked]
Lifecycle: retire-when: <trigger> | basis: <marker> | review-by: <date>   (omit this row when no marker was recorded)
Proof:  <check run> → <result>
```
