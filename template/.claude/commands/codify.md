---
description: On an explicit `/codify` ask or a named caller contract, route one correction or workflow to its owning harness surface, then prove it fires.
disable-model-invocation: false
---

Take ONE just-issued correction ("never let this happen again") or ONE procedure you keep running by hand, and land it where it will be in context when the decision recurs. Model invocation is allowed only after the current user explicitly asks for `/codify`/codification, or when a user-invoked workflow's loaded contract names `/codify`; incidental correction language is not an invocation. Placement rules are owned by `instruction_quality` §3 and §5; this command orders them into one procedure and adds the duplicate check (Step 0) and the firing proof (Step 7).

`/autolearn` is the sibling, not the overlap: it DETECTS signals across a whole session and owns the quality gates. This command routes one item you already have.

## Arguments

- `/codify <text>` — codify that item.
- **Bare `/codify`** — take the immediately-preceding correction and echo your reading back for confirmation before Step 0. If nothing is recoverable (invoked cold, or after a compaction), run `/autolearn`'s Step 0 compaction recovery; if that yields nothing, say so and stop. Never reconstruct a plausible correction — a fabricated one passes Step 0 triage cleanly, which is the silent failure.
- **Two or more items** — split, confirm the split, run sequentially. One item per pass keeps each independently revertible.

## Step 0 — Does it already exist?

Semantic-search `.claude/` for the rule before writing anything. Then read the target file itself for the same obligation in other words. A hit there is the one place you edit: strengthen or correct it, and delete any restatement you find (`instruction_quality` §3). A rule CLAUDE.md or the system prompt already carries, or a hook already enforces, lands no new prose; a violation of it is a reachability or enforcement fix below.

**Done: the search query and its top hits (`path:section`) appear in your report.** A skipped Step 0 produces a clean-looking duplicate that nothing flags until `/rule_consistency` runs.

| Finding | Fix | Wrong fix |
|---|---|---|
| Exists, wasn't loaded at decision time | reachability — re-place it, or add a pointer from a surface that loads then | a new rule |
| Exists, was loaded, was ignored | enforcement — a hook, or a stronger word per §6's no-op test and §18 | restating it louder |
| Exists on 2+ surfaces, contradicting | hand to `/rule_consistency` | a third copy |
| Genuinely absent | Step 1 | — |

The first two rows are the common case. This is `/autolearn`'s *Redesign vs Execute* anti-pattern applied to placement — diagnose adherence before redesigning the rule.

**Division of labour with `/rule_consistency`:** Step 0 *detects* a cross-surface duplicate at save time; `/rule_consistency` *compares and reconciles* across all surfaces as a sweep. Never adjudicate the contradiction here.

## Step 1 — Classify

Judgment the agent must apply → **RULE**. A procedure you re-run by hand → **WORKFLOW** (routing prior: `commands/`, plus a `workflows/*.js` script when it fans out).

**WORKFLOW skips Steps 2–5.** It lands as a command per `instruction_quality` §9–§12 (no-op gate, done-condition per step, argument handling, contract integrity) and its proof is one invocation. A correction that yields both a rule and a workflow is two items — split per Arguments.

**Done: the classification is stated with the one property that decided it.**

## Step 2 — Generalize

Three gates, all required:

- **`/autolearn` *Anti-pattern: Overfit-to-Specific*** — strip the file / PR / spell / SHA out of the principle; it demotes to the `Signal:` evidence line.
- **Model scope** — a failure one model showed on clear text lands in that model's channel, not in universal doctrine (`rules/harness_authoring.md` *A correction takes the scope of its evidence*).
- **No permanent never from a single fix** — check whether the correction over-bans a *mechanism*. Litmus: *am I writing never/only/always about a mechanism when the incident was one instance of it?* Overfit-to-Specific does not catch this, and a false `never` blocks legitimate design space.

**Done: the rule reads in class-of-things terms, its model scope matches its evidence, and any never/only/always in it is named and justified.**

## Step 3 — Mechanizable?

Two mechanization surfaces, checked in order:

- **A tool boundary.** Can a `PreToolUse`/`PostToolUse` matcher *decide* it from a command shape, file path, or tool name? Then it routes to a hook **plus one documented home** (`instruction_quality` §3). A rule whose only home is a hook file is invisible to doctrine and exempt from `/rule_consistency`; a rule restated beyond that one home adds hesitation.
- **A gate over an artifact.** Can a script or a read-only lens *decide* it from a diff, a plan file, a PR body, or the repo state at gate time? Then it routes to the gate that already runs there — a lint in the project's regression gate (`change_control` §Gate cadence names it), a Claude-side grep or lens in the coding layer's pre-PR battery or plan-review panel, or a registry template in `agents/*_agents.md` (via `tools/lens.py`) — and the command that runs the gate is its documented home.

**Done: the matcher condition or the gate predicate is written out, or one line says why the rule needs judgment neither can supply.**

## Step 4 — Route by trigger shape

Each row's rule is owned elsewhere; this table composes them into one ordered procedure and adds the proof column. Work top-down — the order is reach-reliability ÷ passive cost.

| Fires when… | Surface | Owning home | Passive cost | Firing proof |
|---|---|---|---|---|
| a file of a **prefix-anchored class** (below the repo root) is touched | `rules/<name>.md` + `paths:` | `instruction_quality` §5 | zero until matched — but a broad glob matches nearly every session | the glob string is prefix-anchored below the repo root — decidable by reading it, no tooling |
| an agent deliberately enters a domain | `auto-memory/archive/` (cold) | `instruction_quality` §5 | zero | the NL paraphrase an agent would search returns it top-ranked |
| the user types it | `commands/` | `instruction_quality` §5 | zero until invoked | a loaded surface names it at signal-time |
| a tool call matches a condition | `hooks/` **+ documented home** | `instruction_quality` §3 | one interpreter spawn per matched call | pass → deliberate violation → revert, three runs |
| a gate runs over an artifact (diff, plan, PR body, repo state) | lens or lint inside that gate's command (Step 3) | `instruction_quality` §12 | zero until the gate runs | a planted violation yields the finding; a clean input yields none |
| a named task-shape begins | skill | `instruction_quality` §5, §7 | description bytes, every session | a fresh agent given only the description picks it |
| every session, and no trigger exists | CLAUDE.md **or** a MEMORY.md hot topic file | `instruction_quality` §5 | every byte, every session | Step 5 |

`rules/` + `paths:` leads **on a prefix-anchored glob** — the only surface both guaranteed-to-load and genuinely free until matched. On a broad glob (`**/*.cs`, `**/*.tscn`, `**/*.md`) the cost is deferred, not avoided, and the row re-ranks below cold memory; width is about session shape, not file count (`Tests/**/*.cs` stays narrow because a gameplay session never opens `Tests/`). Hooks sit mid-table despite the strongest enforcement: they cost a spawn per matched call and can never stand alone.

**Done: the chosen row is named, and every row above it has a stated reason it was rejected.**

## Step 5 — Always-loaded admission and audit

Applies only when Step 4 lands on CLAUDE.md or a MEMORY.md hot topic file.

Write a MEMORY.md hook line with no path and no link. The path costs more than the hook it points at.

The admission standard is `instruction_quality` §5 A1–A5. Run these checks in order:

1. **Admission.** State the pre-trigger decision the line changes and why an existing or narrower surface cannot carry it. A true, useful line still dilutes its neighbours; "the cluster already exists" is not a reason. For MEMORY.md, also state why the decision fires before a search would run. No admission reason → re-route behind a pointer or to a triggered surface (back to Step 4, land cold) and stop here.
2. **Measure** `wc -c` before and after. Growth needs no matching deletion.
3. **Audit** every changed section with the focused `instruction_quality` pass. It preserves every condition, exception, owner boundary and reachability requirement, and removes only redundant, stale, duplicated, narrative, unreachable, obvious-negation, friction or overfit text.
4. **Caps.** A §5 threshold crossing records its reason and hard-cap response. A hard-cap crossing (CLAUDE.md per `/claudemd_compact`, MEMORY.md per CLAUDE.md §2) routes to split, re-home or an evidence-backed rebase, never to deleting a load-bearing rule.

**Done: admission reason, byte delta, hard-cap status and audit result are recorded, or the item is re-routed.**

## Step 6 — Write, or queue

Direct `Edit` for skills, rules, commands, hooks, cold memory.

**Write the verdict, not the incident.** The landed text is the rule, its trigger, and the one clause that makes it non-obvious. A clause that only answers the triggering complaint, rather than the class it belongs to, is overfit: generalize it or leave it out. Its evidence goes to `auto-memory/archive/` or Obsidian, cited by name (`instruction_quality` §5).

Every landing is a byte delta: state it and run the focused audit. Write the ledger to `.claude/scratch/codify_audits/<sid8>-<target-slug>.json`, one row per changed rule unit: target/section, admission reason, bytes before/after, hard-cap status, before/after unit hashes, preserved obligations, and each removed span classed redundant | stale | duplicated | narrative | unreachable | obvious-negation | friction | overfit. Growth over 1.5KB or 10% reports its reason and audit findings; cut only what the audit classes unnecessary.

**Record a retirement trigger only when the rule can become wrong for a named reason.** With no known invalidating condition, write no marker, omit the report row, and write no permanence label; never invent a generic "architecture/model changes" trigger. A trigger goes in `retire_when:` in the memory file's frontmatter, or a `<!-- retire-when: ... -->` comment on the line below a rule landing in a command, skill or `rules/` file. Name the condition that would make the rule wrong, in this order:

1. **Mechanical**, when one exists — `claude >= X`, `tool:<name> absent`, `load_census budget <name> under X`. These are the only kinds that fire on a real state change.
2. **A prose usage condition in the file**, when the rule's real failure is its own use — "re-check this once three checklists have been authored under it". No marker: nothing in the tool set observes usage, so a marker here is decoration.
3. **`review-by: YYYY-MM-DD`**, only when nothing observable applies. A date says the rule may be stale, never why; it is the last resort, not the default.

**Reach differs by location.** `rule_retirement._review_scope` (CLAUDE.md, `rules/**`, top-level `auto-memory/*.md`) feeds the SessionStart due line, so only a marker there can *trigger* `/autolearn`. One in `commands/` or `skills/` is read by `/autolearn`'s full scan but can never be the reason it runs. Staleness that should prompt a review belongs on the always-loaded surface; a command rule whose condition is usage takes form 2.

`tools/rule_retirement.py` evaluates them, `/autolearn` proposes the retirement once one fires, and a trigger no kind matches is reported as malformed — it retires nothing.

CLAUDE.md and a `rules/` file may carry one file-level `review-by` comment covering every rule in the file; per-rule comments there would grow the always-loaded bundles.

**Queue non-load-bearing CLAUDE.md edits** under `/apply_harness_edits` and its load-mode contract. Append to `.claude/pending_harness_edits.md`, quoting each `old` string verbatim and anchoring by heading, never by line number.

Target listed in `baseline.lock.json` → it is shared doctrine (CLAUDE.md §10). Flag for `/sync_baseline` classification, and check whether a companion new file must upstream in the same operation — a cite pushed without its target dangles in every consuming project.

**Done: the diff is applied or the queue entry written, any justified retirement trigger is recorded (or the lifecycle row omitted when none is justified), and the baseline status of every touched path is stated.**

## Step 7 — Prove it fires

Run the Step-4 proof for the chosen surface and report the result. A cold-memory or `rules/` proof searches the index, so run `/reindex_search` first — a file written this session is not in it, and the proof fails for the wrong reason. Registration proves wiring, not matching (`instruction_quality` §14).

## Report

```
## codify — <one-line rule>

Step 0: <query> → <hits, or "absent">   [branch taken]
Class:  RULE | WORKFLOW  (<deciding property>)
Rule:   <generalized statement>
Surface: <row>  — rejected above it: <row: reason>, ...
Admission/audit: <pre-trigger decision and reason; byte delta; hard-cap status; audit result> | n/a
Landed: <path> (<±bytes>) | queued: <entry>   [baseline: tracked | untracked]
Audit artifact: <.claude/scratch/codify_audits/...json>  [changed units N/N; obligations preserved]
Lifecycle: retire-when: <trigger> | review-by: <date>   (omit this row when no marker was recorded)
Proof:  <check run> → <result>
```
