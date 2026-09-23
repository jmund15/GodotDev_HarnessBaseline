---
disable-model-invocation: true
---

# `/worklog` — DRIVE recipe: mode dispatch, scoring and selection

Read at a `/worklog drive [<N> | scope:N | items:N | <names>] [--plan-only]` invocation and execute from these steps; do not drive from memory. Execution continues in `worklog_drive_execute.md` at Step 5, unless `--plan-only` stopped the run at 4g. This file owns the `drive` form; `commands/worklog.md` owns the tier model and the cross-cutting rules; entry formats live in `worklog_formats.md`.

The agentic prioritization-and-execution op: read the worklog, select logged items, drive them to commits. **Execution is the default — the invocation is the execution directive** (`feedback_honor_execution_directive`, `feedback_single_gate_no_secondary_approval`). Mode D (choose-among) still asks when the fill-set is ambiguous; that is the one selection gate. Once selection settles, never ask "start now?".

**Mode dispatch, ordered — first match wins, after stripping `--plan-only`:**

1. **Argument empty → Survey.** Score ready items, propose 2–3 batches, the user picks. Never executes (Step 4-Survey).
2. **The WHOLE argument matches `^(\d+|scope:\d+|items:\d+)(\s+(scope:\d+|items:\d+))*$` → Budget.** Bare `<N>` ≡ `scope:N`. `scope:` and `items:` combine; the **more restrictive** limit binds — stop filling as soon as either is hit. On ambiguity `scope:` wins (better proxy for one-session capacity). Runs the scoring engine + Modes A–D, then continues into execution.
3. **Otherwise → Named-items.** Comma-split, fuzzy-match each name against ready Active + Ledger items (multi-match → ask, like COMPLETE). Selection is the user's, so scoring and Modes C/D don't apply. Then execute.

**`--plan-only`** (any mode with a selection): stop at the drafted plan body (4d) — non-executing. `drive 3 --plan-only` is the single-session plan-to-approval default.

**Depth is scope-proportional.** Each selected item is driven at its ladder tier per [`_brainstorm_shared/execution_depth.md`](../../skills/_brainstorm_shared/execution_depth.md); that file's rule 1 (independent litmuses stay the binding floor) governs every gate this op would otherwise reduce.

**Scope-4: never selected, never driven.** Budget mode keeps the −5 score, the Mode B refusal, and the 4c big-ticket flag; named-items mode refuses a named scope-4 item with the same flag. Route to `/design_drive` (no design doc yet) or `/part_drive` (roadmap Part exists), against the item's linked Plan doc.

### Step 1 — Read the open tiers

`READ(Worklog.md)` — full content (date, context, source, where, class, scope), not just titles. Score `[ ]` items only. **Partition into ready vs waiting:** no `When:` sub-bullet → **ready** (scored); `When: after ...` / `When: future` → **waiting** (skip scoring entirely, list in output only). Only ready items flow through Steps 2–4.

**Which tiers to read is mode-dependent.** Survey and Budget score `## Active` alone — the ready pool IS the candidate set, and scoring 200 Ledger items to surface 3 is what the tier split exists to stop. **Named-items mode reads `## Active` + `## Ledger`**: the user named the work, so tier is irrelevant and a name that resolves only in the Ledger must still match. A named Ledger item is driven in place and promoted to Active for the duration, so its `Where:` overlap makes it hot at the next rebalance.

### Step 2 — Gather context signals

In parallel:
- `git status --short` — items whose `Where:` paths overlap uncommitted changes are HOT.
- `git log --oneline -10` — items whose context references recent commit topics are warm.
- Active-item ages — added > 14 days ago = bit-rot candidates.
- Domain frequencies — domains with 2+ `[ ]` items are batch / cohesion candidates.
- **Class + scope distribution.** Many scope-1 items in one domain → quick-win sweep.

### Step 3 — Score every ready item

Per-item signal sum — the **shared scoring engine** Survey and Budget both consume. **Named-items mode skips Steps 2–3 entirely.**

- **+3** if `Where:` paths or `Source:` refs overlap `git status` (hot-context).
- **+3** if `Source:` carries a `Deferred: <blocker>` clause whose blocker has since cleared — the named
  drive shipped, the file it held is committed, the PR merged. A deferral records work that was ready and
  blocked, and nothing re-scores it when the block lifts, so the log's most-ready items sit at the bottom.
  Check the blocker's own words against `git log --oneline -20` before granting it.
- **+3** if the item has a `Quick-win:` sub-bullet (triage-flagged; explicit user intent). Stacks with hot-context.
- **+2** if `Source:` text matches recent commit messages (warm-context).
- **+2** if 2+ items share its `### Domain` section (cohesion candidate).
- **+1** per scope point above 1 (scope-3 = +2, scope-2 = +1, scope-1 = 0) — at equal heuristic weight, prefer higher-impact work. This works *against* quick-win-flagged scope-1 items; the +3 quick-win bonus restores parity.
- **−5** if scope == 4 — effective rule-out; the big-ticket flag handles scope-4 regardless of score.
- **−2** anti-thrash penalty if the item has an **unpaired** `tackle` event in `.claude/worklog-tackle-history.jsonl` within the last 14 days. Unpaired = a `tackle` for this title with no later `completion` for the same title. `Read` the JSONL, filter to the last 14 days by `date`, check for unpaired matches per the 4f schema. Absent or empty file = no penalties (cold start).

### Step 4 — Fork on mode

Empty argument → **Step 4-Survey**; budget target → **Step 4-Budget** (4a–4g); named items → 4a's named path, then 4c–4g.

---

### Step 4-Survey — Batch and propose (no target)

**Batch the scored items** in heuristic priority order, first match wins per item:

0. **Quick-win-flagged batch** (highest priority; fires only when ≥1 flagged item exists): items carrying `Quick-win:`, as a dedicated batch above all others named "Batch QW — flagged quick-wins".
1. **Hot-context batch**: `Where:` / `Source:` overlapping current `git status` or recent commits. Flagged items also matching hot-context stay in batch 0 — never double-list.
2. **Domain-cohesion batch**: 2+ items sharing a `### Domain`. Bonus weight when they share a class (3 `refactor` items in `vfx`).
3. **Quick-wins batch** (scope-1 sweep): scope-1 items across any domain *without* the flag; batch 0 takes precedence.
4. **Stale-item batch**: the 3–5 oldest `[ ]` items regardless of cohesion or scope.
5. **Big-ticket flag**: any single scope-4 item. Don't batch — route to `/design_drive` or `/part_drive`, citing the linked Plan doc.

Active has ≤ 2 `[ ]` items → skip batching; print them with "Want to drive one of these? (pass `<N>` / `items:N`, or name it, to go straight in)".

**Output proposal:**
```
Worklog drive — 3 candidate batches (<n> ready items; <m> waiting).

Batch A — <name> (<n> items, mix of scope <range>)
  Why: <one-line rationale citing the heuristic>
  Items:
    - <class>/<scope> · <title> (added <date>; <domain>)
    ...

Batch B — ...

Batch C — ...

Big-ticket (won't batch): <title> — scope 4, see [[Plan doc]]. Route to /design_drive or /part_drive?

Waiting (not scored):
  - <title> [after: <condition>] (<domain>)
  - <title> [future] (<domain>)
  (omit this section entirely if no waiting items)

Pick a batch (A/B/C) to drive, or re-run with `<N>` / `items:N` to go straight in.
```
Rationales cite *which heuristic* fired (hot-context, cohesion, quick-wins, age) so the user can sanity-check the priority logic.

**On batch pick:** re-enter at 4a with that batch's items as the fill-set, then 4c–4g and execute. Its context blocks are already loaded — don't re-read them.

Survey mode does **not** log to tackle-history — it commits to nothing until a batch is picked.

---

### Step 4-Budget — Fill the target (budget target given)

#### 4a — Select the fill-set

**Budget mode:** add ready items by descending Step-3 score until the binding target is reached (scope sum hits `scope:N`, or item count hits `items:N`, whichever comes first). Two items tied at the inclusion boundary → surface both as "contested alternates" in 4b.

**Named-items mode:** comma-split; fuzzy-match each name against ready `[ ]` items in Active or Ledger. A name matching 2+ items → list and ask, exactly as COMPLETE does. A name matching none → say so and stop rather than substituting a near-miss. The fill-set is what the user named — no scoring, no target. Skip 4b (Modes C/D are heuristics the user has overridden); go to 4c.

**Never include scope-4 items in either mode** — they are flagged in 4c and routed.

#### 4b — Classify pick mode (budget mode only)

Priority order, first match wins:

**Mode A — `empty-state`.** Active has 0 ready items:
```
Worklog clear: no ready items in Active.
- <m> waiting (after: ...): /worklog show all to review
- <k> in Future Scope: /worklog show all to review
```
Stop. If waiting items exist (`m > 0`), also suggest `/worklog unblock <condition>` for each condition recent commits might satisfy.

**Mode B — `scope-4-only`.** Top-scored item is scope-4 and no viable smaller fill-set exists. Refuse and route:
```
Top candidate is scope-4 (one-session viability cap is scope-3).

  <title> — <domain> · scope 4
  Plan doc: [[<doc>]]

Recommended: /design_drive (no design doc yet) or /part_drive (roadmap Part exists), against the linked plan doc. Or: re-run with a larger `items:N` target to surface smaller candidates.
```
Stop.

**Mode C — `auto-confirm`.** Fill-set unambiguous — a single dominant item (top score ≥ 4, second-place < 60% of top) OR a multi-item fill-set with no boundary tie. Print and proceed to 4d:
```
Driving: <fill-set summary> — total scope ~<sum>
  Why: <rationale citing the dominant heuristic(s)>
  Scores: <per-item score list>
```

**Mode D — `choose-among`.** Ambiguous — a boundary tie, or top score < 4 with no clear winner:
```
Proposed fill-set (target: <target>):
  - <class>/<scope> · <title>  [score <s>; <heuristic>]
  ...
Contested for the last slot (within 60% of each other):
  a. <title>  [score <s>]
  b. <title>  [score <s>]

Accept (y), swap (e.g. `use a`), or skip (n)?
```
If every item scored 0 (no hot-context, no cohesion, scope-1 odds-and-ends), default the fill-set to the oldest ready items up to the target, noting: "No strong signals — surfacing oldest ready items to clear backlog pressure."

#### 4c — Big-ticket flag

Any scope-4 ready item — scored in budget mode, or explicitly named — is surfaced below the fill-set proposal, never silently dropped:
```
Big-ticket (excluded): <title> — scope 4, see [[Plan doc]]. Route to /design_drive (no design doc yet) or /part_drive (roadmap Part exists)?
```

#### 4d — Draft the plan body

Read each fill-set item's full `Context` / `Where` / `Source` block from `Worklog.md` before scoping it — never the mirror line (`feedback_plan_worklog_items_from_source_not_mirror`).

The body's **artifact** follows the ladder tier: tier 1 needs none; tier 2 keeps it in-conversation (a plan *file* only when the executor is dispatched cold); tier 3 writes `.claude/plans/<slug>.md`. Under `--plan-only` the body is always written out — it is the deliverable. Shape either way:
```
## Plan: <title-or-fill-set-name>

**Source:** Worklog item(s) — `<class>` · scope `<n>` · added <date> · <domain>
**Context:** <verbatim Context line(s) from Worklog>
**Where:** <verbatim Where line(s), if present>

### Approach

<3-7 bullet outline of how to approach the work. Reference specific files/types/methods when known.>

### Steps

1. <concrete first step — usually a read or test-write per Logic-domain TDD>
2. <next step>
3. <...>

### Verification

- <how we'll know this works — test pass, manual repro, log check>
- <regression sentinel: which test suite must still pass after — usually the project's regression gate (`change_control` §Gate cadence names it) for code work>

### Worklog completion

After landing: `/worklog complete <title>` (commit hash <pending>).
```

**Test-first note:** a fill-set item in an area the project's test-first rule covers (CLAUDE.md names those domains) MUST open its **Steps** with a RED test pinning the bug or proving the new behavior missing. No production-code step before a verifying test.

**Multi-item drafting:** partition the fill-set by `PLAN_SHAPE` — never one plan spanning both shapes ([*Plan-file format*](../../skills/_brainstorm_shared/plan_file_format.md) → *One shape per plan file*). Within a shape, default to one plan and split further only on that rule's cohesion litmus (one `Constraints` block and one `Verification` section genuinely cover the set). Items sharing an invariant get Steps shaped "do X once, then apply across A, B, C"; items with no shared invariant but one plan's worth of spine get labelled sub-sequences under a single Verification section.

**Class-aware composition:**
- `class: design`: draft no implementation plan. Say `Picked a design item — recommend running /architecture_brainstorm first; it will route to /idea_brainstorm if the candidate pool is empty. Re-run drive once the design exists.` and stop on that item (drive the rest of the fill-set if any).
- **Audit-shape items** (title starts `Audit`/`Verify`/`Review`/`Inspect`/`Check`, or Context is read-and-decide): execute the reads while drafting, render the verdict in the plan body, plan only consequent code changes. Compliant verdict → the plan collapses to `/worklog complete` with the verdict as the `XLINE` ref. Same for read-only `debug` reproduction.
- `class: debug`: structure Steps per the `debugging` skill's 6 phases (feedback loop → reproduce → patterns → hypothesise → fix → cleanup). No fixes before reproduction.
- `class: test`: Steps describe what to assert and which test fixture, not implementation.

#### 4e — Conditional `/plan_check`

Evaluate **each** drafted plan against `/plan_check`'s litmus (3+ files, new type/folder, refactor of 2+ subclass family, file deletion/replacement) — a shape partition produced two plans, each judged on its own. Nothing auto-invokes it; this step does.

- **Litmus trips → run `/plan_check <plan-file>` now** on every plan that trips it, lens set by that plan's own shape, and converge before executing. The ladder never narrows the mandates or waives a pass the litmus mandates (`execution_depth.md` rule 1); seat width follows `orchestration` §2.
- **`--plan-only` → surface it instead of running it**, so the user approves plan and check together:
  ```
  ---
  **Pre-execution gate:** This plan touches <N files | introduces <type> | refactors <family>>. Recommend `/plan_check <this-plan>` before approval.
  ```
- **No triggers → omit the block and proceed.**

#### 4f — Log to tackle-history

Append one `tackle` line **per fill-set item** to `.claude/worklog-tackle-history.jsonl` (committed; the first append creates it):
```json
{"event": "tackle", "date": "YYYY-MM-DD", "title": "<title verbatim>", "domain": "<domain>", "class": "<class>", "scope": <n>, "mode": "auto-confirm|choose-among|named", "score": <s>}
```
Use `printf '...\n' >> .claude/worklog-tackle-history.jsonl`, NOT `echo` (OS-dependent line endings). `title` MUST be verbatim so COMPLETE's pair-matching works. One line per item, so pairing tracks per item. Named-items mode writes `"mode": "named", "score": null`.

##### JSONL schema reference

Two event shapes, both carrying `event` and `date`:

| Event | Required fields | Written by |
|-------|-----------------|------------|
| `tackle` | `event`, `date`, `title`, `domain`, `class`, `scope`, `mode`, `score` | DRIVE 4f |
| `completion` | `event`, `date`, `title` | COMPLETE Step 9 (`worklog_complete.md`) |

The event name `tackle` is historical, retained because live history lines pair on it; the emitter is DRIVE.

**Pairing semantics:** an item is "completed after a tackle" iff a `completion` event for the same `title` has a `date` ≥ the most recent `tackle` event's `date` for that title. Step 3's −2 penalty fires when a `tackle` exists in the last 14 days AND no matching `completion` follows.

**Append-only.** Never rewrite or compact this file inline. If it grows past ~1000 lines, introduce a separate `/worklog history-compact` operation that archives old entries.

#### 4g — Proceed, or stop under `--plan-only`

**Default: proceed straight into Step 5.** Announce in one line — `Selection settled. Driving <n> item(s) now.` — and do not ask for a second go-ahead; the invocation was it.

**Under `--plan-only`,** stop here with `Plan drafted. Say the word when ready to start, or refine first.` If 4e surfaced a pre-execution gate, use `Plan drafted with pre-execution gate flagged. Recommend running /plan_check before starting.` instead.

### Edge cases for DRIVE

- **Fill-set item already in current uncommitted work.** Hot-context fires on items the user is *already* doing: `git status` files overlap the item's `Where:` paths AND the item is still `[ ]`. Drive it as a continuation of that work and say so in the plan.
- **Survey Hot-context batch overlaps current work too narrowly.** Flag it rather than proposing it as fresh work.
- **User picks a fill-set then names a different one.** Re-run 4d with the new selection; don't re-score — the user overrode the heuristic.
- **Non-determinism.** The heuristic shifts with git context, so one Active list yields different batches across sessions. Intentional; say so when asked "why this batch?".
- **Scope is a coarse effort proxy.** A scope-2 item in an untouched system can be scope-3 in reality. The target is a sizing hint, not a contract; an item visibly exceeding its scope mid-drive is the Step-5 re-scope valve, not a judgment call.
- **A named item is waiting or absent.** Named-items matches ready `[ ]` items in Active or Ledger only. A `When: after` / `future` hit surfaces the gate and offers `/worklog unblock <condition>` or `/worklog promote <title>`; no hit stops rather than substituting a near-miss.
