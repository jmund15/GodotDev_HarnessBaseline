---
disable-model-invocation: true
---

# `/worklog` — DRIVE recipe: mode dispatch and selection

**Input:** the `/worklog drive [<N> | scope:N | items:N | <names>] [--plan-only]` argument forms.
**Enters at:** intake (selection), then each item runs the spine.
**Owner stop:** the 4b packet when the fill-set boundary is contested, and `--plan-only` after 4e.

Run the stages, halts, slices and close in [`drive_spine.md`](../../skills/_brainstorm_shared/drive_spine.md). This file holds only the selection and plan body a worklog item adds.

Read at a `/worklog drive` invocation and execute from these steps. Execution continues in `worklog_drive_execute.md` at Step 5; `--plan-only` stops after 4e, before tackle-history is written. This file owns the `drive` form; `commands/worklog.md` owns the tier model and the cross-cutting rules; entry formats live in `worklog_formats.md`.

The agentic prioritization-and-execution op: read the worklog, select logged items, drive them to commits. **Execution is the default**: the invocation is the execution directive (spine §Stages). The 4b packet still asks when the fill-set is contested; that is the one selection gate.

**Mode dispatch, ordered — first match wins, after stripping `--plan-only`:**

1. **Argument empty → Survey.** Surface the distinct actionable batches supported by ready-item evidence, explain why each is useful, and let the user choose. Never executes (Step 4-Survey).
2. **The WHOLE argument matches `^(\d+|scope:\d+|items:\d+)(\s+(scope:\d+|items:\d+))*$` → Budget.** Bare `<N>` ≡ `scope:N`. `scope:` and `items:` combine; the **more restrictive** user-supplied limit binds — stop filling as soon as either is hit. Use the user's limits as budgets, not ranking signals. Then continue into execution.
3. **Otherwise → Named-items.** Comma-split, fuzzy-match each name against ready Active + Ledger items (multi-match → ask, like COMPLETE). Selection is the user's; do not rank the named items. Then execute.

**`--plan-only`** (any mode with a selection): stop after 4e's drafted plan and any surfaced pre-execution gate, before 4f logs a `tackle` event. A plan that did not run has nothing to log. `drive 3 --plan-only` is the single-session plan-to-approval default.

Each selected item is driven at the depth [`_brainstorm_shared/execution_depth.md`](../../skills/_brainstorm_shared/execution_depth.md) §Admission table assigns.

**Scope-4: never selected, never driven.** Surface scope-4 work separately from the ready fill-set and route it to `/design_drive` (no design doc yet), `/part_drive` (roadmap Part exists) or `/feature_drive` (design settled, no Part admitted), against the item's linked Plan doc. Named-items mode applies the same redirect to an explicitly named scope-4 item.

### Step 1 — Build the shortlist from the title mirror

Read `.claude/worklog-titles.md` and parse both tier sections into a typed inventory of title, scope, class and `When`; reconcile ready/waiting totals and source revision against the live Worklog. Survey and Budget shortlist from that metadata, then read only the full `Worklog.md` blocks for shortlisted or contested items before selection. The mirror is a discovery index, not a spec; never select from title-only evidence. Recheck the Worklog revision before reporting and refresh the inventory if it changed.

### Step 2 — Gather context signals

Inspect evidence for each shortlisted candidate and contested item:
- Check current worktree changes against its `Where:` paths and cite any overlap.
- Inspect commits relevant to each item's `Where:` / `Source:` since its recorded state or last verified blocker; state the searched range.
- Verify a named `Deferred: <blocker>` against the owning change or commit, searching enough history to reach that event. A recent commit alone does not prove the blocker cleared.
- Flag an item as stale when its named source, owner, dependency or expected behavior changed; age alone does not establish rot.
- Treat items sharing an actual change seam or verification surface as cohesion candidates; domain labels alone do not make a batch.
- Record each signal with its evidence; Step 3 owns how they rank.
- Inspect tackle history by title and date to identify a prior incomplete attempt. A recently tackled item remains eligible when its blocker or diagnostic state changed; otherwise surface the prior attempt and avoid blind thrash.

### Step 3 — Prioritize every ready item

**Named-items mode skips Steps 2–3.** For Survey and Budget, compare shortlisted ready items by readiness, evidence, dependencies, write-set fit, user intent and effort fit. State what each choice displaces. Group items only where evidence supports a choice. A `Quick-win:` flag is user intent, not a bonus, and does not outrank a live blocker. Emit no scores. A user's `scope:N` and `items:N` values constrain the fill-set only; the more restrictive target binds.

### Step 4 — Fork on mode

Empty argument → **Step 4-Survey**; budget target → **Step 4-Budget** (4a–4g); named items → 4a's named path, then 4c–4g.

---

### Step 4-Survey — Batch and propose (no target)

Group ready items as Step 3 directs, keep each item in one batch and explain the evidence and trade-off. Surface scope-4 items separately and route them to `/design_drive` or `/part_drive`, citing the linked Plan doc.

When all ready items form one clear choice or have no useful alternative grouping, show them directly; otherwise offer coherent batches. If there is no useful batch, list the ready items and let the user choose.

**Output proposal:**
```
Worklog drive — <n> evidence-backed choices (<r> ready items; <w> waiting).

Batch A — <name> (<n> items)
  Why: <evidence and why this grouping is useful>
  Items:
    - <class>/<scope> · <title> (added <date>; <domain>)
    ...

<Additional supported batches, if useful alternatives exist>

Scope-4 (not driven): <title> — see [[Plan doc]]. Route to /design_drive or /part_drive.

Waiting (not ranked):
  - <title> [after: <condition>] (<domain>)
  (omit this section entirely if no waiting items)

Pick a batch to drive, or re-run with `<N>` / `items:N` to go straight in.
```
Rationales cite the matched paths, dependency, user flag or shared change/verification surface, and name the trade-off. Do not use an arbitrary score or batch count.

**On batch pick:** re-enter at 4a with that batch's items as the fill-set, then 4c–4g and execute. Its context blocks are already loaded — don't re-read them.

Survey mode does **not** log to tackle-history — it commits to nothing until a batch is picked.

---

### Step 4-Budget — Fill the target (budget target given)

#### 4a — Select the fill-set

**Budget mode:** select ready items by grounded priority and the user's budget, stopping when either user-supplied target binds (scope sum reaches `scope:N` or item count reaches `items:N`). If an equally viable alternative changes the fill-set at that boundary, surface the contested choice in 4b. Show the evidence and trade-off without a point sum.

**Named-items mode:** comma-split; fuzzy-match each name against ready `[ ]` items in Active or Ledger. A name matching 2+ items → list and ask, exactly as COMPLETE does. A name matching none → say so and stop rather than substituting a near-miss. The fill-set is what the user named — no scoring, no target. Skip 4b (its packet is a heuristic the user has overridden); go to 4c.

**Never include scope-4 items in either mode** — they are flagged in 4c and routed.

#### 4b — Confirm or ask (budget mode only)

With no ready item, print `Worklog clear: no ready items in Active or Ledger.` with the waiting count and stop. With only scope-4 candidates, route them per 4c and stop. When the evidence settles the fill-set's boundary and no equally viable alternative exists, print `Driving: <fill-set summary> — total scope ~<sum>` with its evidence and trade-off, and go to 4d. Otherwise ask one packet (`CLAUDE.core.md` §Preferences): the proposed fill-set and each contested alternative as options, the recommendation and the default of driving the recommended fill-set. With no material prioritization signal, the options are the oldest ready candidates within the budget, and the question says so.

#### 4c — Big-ticket flag

Any scope-4 ready item — found in the budget candidate set or explicitly named — is surfaced below the fill-set proposal, never silently dropped:
```
Big-ticket (excluded): <title> — scope 4, see [[Plan doc]]. Route to /design_drive (no design doc yet) or /part_drive (roadmap Part exists)?
```

#### 4d — Draft the plan body

Read each fill-set item's full `Context` / `Where` / `Source` block from `Worklog.md` before scoping it — never the mirror line (`feedback_plan_worklog_items_from_source_not_mirror`).

Apply `execution_depth.md` §Admission table's fast-path outline when no Plan stage is admitted. Use the body below only for an admitted Plan stage. Under `--plan-only` the requested plan body is always written out — it is the deliverable.
```
## Plan: <title-or-fill-set-name>

**Source:** Worklog item(s) — `<class>` · scope `<n>` · added <date> · <domain>
**Context:** <verbatim Context line(s) from Worklog>
**Where:** <verbatim Where line(s), if present>

### Approach

<a bounded outline of the evidence, implementation and verification steps this item needs. Reference specific files/types/methods when known.>

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

**Test-first note:** a fill-set item opens its **Steps** per CLAUDE.md *Hybrid TDD*.

**Multi-item drafting:** partition the fill-set by `PLAN_SHAPE` — never one plan spanning both shapes ([*Plan-file format*](../../skills/_brainstorm_shared/plan_file_format.md) → *One shape per plan file*). Within a shape, default to one plan and split further only on that rule's cohesion litmus (one `Constraints` block and one `Verification` section genuinely cover the set). Items sharing an invariant get Steps shaped "do X once, then apply across A, B, C"; items with no shared invariant but one plan's worth of spine get labelled sub-sequences under a single Verification section.

**Class-aware composition:**
- `class: design`: draft no implementation plan. Say `Picked a design item — recommend running /architecture_brainstorm first; it will route to /idea_brainstorm if the candidate pool is empty. Re-run drive once the design exists.` and stop on that item (drive the rest of the fill-set if any).
- **Audit-shape items** (title starts `Audit`/`Verify`/`Review`/`Inspect`/`Check`, or Context is read-and-decide): execute the reads while drafting, render the verdict in the plan body, plan only consequent code changes. Compliant verdict → the plan collapses to `/worklog complete` with the verdict as the `XLINE` ref. Same for read-only `debug` reproduction.
- `class: debug`: structure Steps per the `debugging` skill's phases (feedback loop → reproduce → patterns → hypothesise → fix → cleanup). No fixes before reproduction.
- `class: test`: Steps describe what to assert and which test fixture, not implementation.

#### 4e — Conditional `/plan_check`

Decide plan-check admission for **each** drafted plan per `execution_depth.md` §Admission table — a shape partition produced two plans, each judged on its own. Nothing auto-invokes it; this step does.

- **Admitted → run `/plan_check <plan-file>` now** on every admitted plan, lens set by that plan's own shape, and converge before executing; seat width follows `orchestration` §2.
- **`--plan-only` → surface it instead of running it**, so the user approves plan and check together:
  ```
  ---
  **Pre-execution gate:** Plan check is admitted for this plan (`execution_depth.md` §Admission table: <fact that holds>). Recommend `/plan_check <this-plan>` before approval.
  ```
- **Not admitted → omit the block and proceed.**

#### 4f — Log to tackle-history

Append one `tackle` line **per fill-set item** to `.claude/worklog-tackle-history.jsonl` (committed; the first append creates it):
```json
{"event": "tackle", "date": "YYYY-MM-DD", "title": "<title verbatim>", "domain": "<domain>", "class": "<class>", "scope": <n>, "mode": "auto-confirm|choose-among|named"}
```
Use `printf '...\n' >> .claude/worklog-tackle-history.jsonl`, NOT `echo` (OS-dependent line endings). `title` MUST be verbatim so COMPLETE's pair-matching works. One line per item, so pairing tracks per item.

##### JSONL schema reference

Two event shapes, both carrying `event` and `date`:

| Event | Required fields | Written by |
|-------|-----------------|------------|
| `tackle` | `event`, `date`, `title`, `domain`, `class`, `scope`, `mode` | DRIVE 4f |
| `completion` | `event`, `date`, `title` | COMPLETE Step 9 (`worklog_complete.md`) |

The event name `tackle` is historical, retained because live history lines pair on it; the emitter is DRIVE.

**Pairing semantics:** an item is "completed after a tackle" iff a `completion` event for the same `title` has a `date` ≥ the most recent `tackle` event's `date` for that title. Read historical tackle records by title and date; do not use historical scores.

**Append-only.** Never rewrite or compact this file inline. When append-only history slows its readers or state size becomes material, introduce an archival operation that preserves tackle/completion pairing and lets the current reader cover the required interval.

#### 4g — Proceed to execution

On the proceed path, announce in one line — `Selection settled. Driving <n> item(s) now.` — and continue into Step 5.

`--plan-only` has already stopped after 4e, before 4f writes tackle history. Report `Plan drafted. Say the word when ready to start, or refine first.` If 4e surfaced a pre-execution gate, use `Plan drafted with pre-execution gate flagged. Recommend running /plan_check before starting.` instead.

### Edge cases for DRIVE

- **Fill-set item already in current uncommitted work.** Hot-context fires on items the user is *already* doing: `git status` files overlap the item's `Where:` paths AND the item is still `[ ]`. Drive it as a continuation of that work and say so in the plan.
- **Survey Hot-context batch overlaps current work too narrowly.** Flag it rather than proposing it as fresh work.
- **User picks a fill-set then names a different one.** Re-run 4d with the new selection; honor the user's override without reranking it.
- **Non-determinism.** The heuristic shifts with git context, so one Active list yields different batches across sessions. Intentional; say so when asked "why this batch?".
- **Scope is a coarse effort proxy.** A scope-2 item in an untouched system can be scope-3 in reality. The target is a sizing hint, not a contract; an item visibly exceeding its scope mid-drive follows Step 5's re-scope valve and spine H2, not a judgment call.
- **A named item is waiting or absent.** Named-items matches ready `[ ]` items in Active or Ledger only. A `When: after` item surfaces its condition and offers `/worklog unblock <condition>`; Future Scope is not a drive candidate. No ready match stops rather than substituting a near-miss.
