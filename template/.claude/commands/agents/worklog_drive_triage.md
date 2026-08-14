---
description: Companion recipe file for /worklog DRIVE + TRIAGE — loaded by the worklog command on demand, never invoked directly.
disable-model-invocation: true
---

# /worklog — DRIVE + TRIAGE recipes

Extracted from `worklog.md` so the common `add`/`complete`/`show` path doesn't load these recipes. Loaded on demand: Read this file when `drive` or `triage` is invoked, then execute from its steps. The Forms table, cross-cutting rules (mirror maintenance, frontmatter bump, de-dup), cloud fallback, and all other operation recipes stay in `worklog.md`.

## Operation: TRIAGE

Bulk cleanup. Walks Active items proposing per-item dispositions; confirmation-driven, never auto-applies. Use when `/worklog show` flags overload (Active > 30) or any time backlog pressure builds.

### Step 1 — Read full Active section

Need full content (Context, Where, Source, dates, class, scope) to score dispositions. Mirror is insufficient.

```
mcp__obsidian__obsidian_read_note(filePath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md")
```

`Worklog.md` holds only `[ ]` items now (completions live in `Worklog-Archive.md`); `When: future` items live in Future Scope and aren't part of Active triage.

### Step 2 — Gather context signals (in parallel)

- `git log --oneline --since="3 weeks ago"` — recent commit messages.
- `git status --short` — uncommitted changes.
- Today's date for age math against each item's `added` field.

### Step 3 — Score each item's recommended disposition

For each `[ ]` item, derive ONE recommended disposition (priority order — first match wins):

| Disposition | Trigger | Action |
|---|---|---|
| **complete** | `Where:` paths or `Source:` text overlap recent commit messages or `git status` | run existing COMPLETE recipe with the matched commit hash |
| **do-now** | scope-1 + class in `{fix, refactor, docs, chore}` + mechanical phrasing (rename / sweep / remove dead reference / single-line edit / one-paragraph add) | execute work this turn, then COMPLETE |
| **quick-win** | scope-1 + class NOT in `{design, debug}` + has `Where:` OR concrete Context — needs minor judgment but not pure mechanical | add `Quick-win: flagged YYYY-MM-DD` sub-bullet; keeps item in Active with priority flag for next session |
| **delete** | age > 30 days + no `git log` activity touching `Where:` + Source references something already shipped/superseded | remove `[ ]` block entirely (no archive — git history is the record) |
| **promote** | age > 14 days + no `git log` activity touching `Where:` + no `When: after` clause | move from `## Active` to `## Future Scope` as a one-liner (inverse of `/worklog promote`) |
| **to-user-tasks** | item is fundamentally user-only addressable: `vfx`-domain `feature` items (production art); `design`-class items where output is user taste / vision (not technical alternatives); items whose `Context:` implies "needs user judgment" (playtest tuning, art preferences, lore decisions). High-confidence triggers only — when uncertain, prefer `skip`. | migrate to `User-Tasks.md` via the USER-ADD recipe, then delete the Active block |
| **skip** | default — no strong signal | no-op, item stays as-is |

**Caps for proposal generation** (prevents triage-fatigue):
- `do-now`: max 5 per session.
- `delete` / `promote` / `to-user-tasks`: max 5 each.
- `complete` / `quick-win` / `skip`: no cap.

If a cap would exceed, take the highest-confidence per category by signal strength.

### Step 4 — Walk items in disposition-priority order

Print the header summary first:

```
Triage: <total> items. Proposed: <n> complete, <m> do-now, <k> quick-win, <p> promote, <u> to-user-tasks, <q> delete. Walking now (highest-signal first).
```

Then for each proposed item:

```
[I/N] <Title> — <domain> · <class> · scope <s> · added <date>
  Context: <one-line>
  Recommended: <disposition>
  Why: <one-line rationale citing the trigger>

  Apply? [y]es-recommended  [c]omplete  [d]o-now  [f]lag-quick-win  [p]romote  [u]ser-tasks  [x] delete  [s]kip  [q]uit-triage
```

Single-letter response. `q` stops the walk (remaining items unaddressed). `y` applies the recommended disposition.

### Step 5 — Execute the chosen disposition

#### `complete`
Run the existing COMPLETE recipe (`## Operation: COMPLETE` in `worklog.md`). If a matching commit hash was identified in Step 2, pre-fill it as the `<ref>`; else prompt for ref or accept "no ref".

#### `do-now`
1. Read the item's `Where:` files (use `read_files` if 3+; else `Read`).
2. Make the change. Mechanical class only — if the work expands beyond mechanical (multi-decision, multi-file beyond `Where:` lists), abort do-now and offer fallback dispositions inline (`[c]omplete-after-manual / [f]lag for next session / [s]kip`).
3. Run verification:
   - `.cs` changes → `/regression_gate` MANDATORY (per CLAUDE.md Build & Test Commands).
   - `.tres` / `.tscn` Logic-affecting → relevant Logic test suite.
   - Doc-only / `.md` → no verification needed.
4. Run COMPLETE recipe with today's date as ref (or commit hash if a commit lands in this turn).
5. Resume walk on next item.

The walk pauses while you do the work — don't batch all do-now items at the end. Inline execution lets the user see results before deciding the next item.

#### `flag` (quick-win)
Add a sub-bullet to the item's `[ ]` block in Obsidian, immediately after the `Context:` line:

```
mcp__obsidian__obsidian_search_replace(
  targetType="filePath", targetIdentifier="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md",
  replacements=[{
    search: "  - Context: <verbatim context line>\n",
    replace: "  - Context: <verbatim context line>\n  - Quick-win: flagged YYYY-MM-DD\n"
  }]
)
```

Item stays in Active. Mirror rewrite (Step 6) appends `[quick-win]` suffix to its mirror line. `/worklog drive` weights flagged items +3 in scoring — survey-mode batching and budget-mode fill alike (treated as high hot-context).

#### `promote` (Active → Future Scope)
Inverse of `/worklog promote`. Two-step `obsidian_search_replace`:

**Step A** — delete the `[ ]` block from `## Active`. Same pattern as COMPLETE Step 4 (match all sub-bullets that exist in this specific block; be precise about which lines are present; mind the LF/CRLF line-ending trap).

**Step B** — append a one-liner to `## Future Scope > ### Domain`:
```
> - `<class>` · scope `<n>` · <Title> (added YYYY-MM-DD; <Context one-liner>; promoted from Active YYYY-MM-DD-today)
```

Reuse the existing Future Scope insertion logic from ADD step 5b (in `worklog.md`). If `### Domain` doesn't exist in Future Scope yet, create it. If `## Future Scope` doesn't exist at all, create it between `## Active` and `## Linked Docs`. Update Future Scope callout's `(N items)` count.

This is the only Active → Future Scope path; no top-level `/worklog defer` operation exists (kept off the CLI surface to bound complexity).

#### `to-user-tasks` (Active → User-Tasks parallel doc)

For items fundamentally user-only addressable — production art, feel-tuning, open-ended brainstorms whose output is user taste, cross-doc vision audits. Migrates the Active item to `User-Tasks.md` and deletes the Active block. Unlike `promote`, this is **monodirectional**: the User-Tasks doc is opaque to all future agent passes, so the item leaves Claude's awareness entirely. Confirm twice if the item's class is anything other than `design` or `feature` — `refactor`/`fix`/`test`/`chore` items rarely belong in User-Tasks (they have technical outputs Claude can produce).

**Step A** — derive a User-Tasks entry from the Active block:
- Title: stays the same wording (drop the `**bold**`).
- Date: today (User-Tasks date = "when Claude flagged it for the user"; re-clocking is intentional — signals the migration moment, not the original log date).
- Context: condense the Active block's `Context:` line to ≤80 chars if longer.
- Domain: same as the Active item.

**Step B** — invoke the USER-ADD recipe (in `worklog.md`) with the derived fields. Skip USER-ADD Step 1 (de-dup search) — the triage walker has already shown the user existing entries.

**Step C** — delete the `[ ]` block from `## Active` in Worklog.md. Same pattern as `promote` Step A: precise multi-line `obsidian_search_replace` matching all sub-bullets that exist in this specific block.

**Step D** — atomicity check: if Step B failed (MCP error on the User-Tasks write), do NOT execute Step C. The Active block stays as-is; surface the error and offer fallback dispositions (`[s]kip` / `[p]romote` to Future Scope). Better a stuck Active item than a lost migration.

Worklog frontmatter bump + mirror rewrite happen in Step 6 (end-of-walk) as normal — the migrated item is no longer in Active, so it drops from the mirror.

#### `delete`
Remove the `[ ]` block from Obsidian entirely. No archive — git history is the record.

For scope > 1, confirm twice:
```
Confirm DELETE (no archive): <title>? [yes/no]
```

For scope 1, single confirmation suffices (the user already chose `x`).

#### `skip`
No-op. Move to next item.

#### `quit`
Stop the walk. Remaining items are unaddressed (treated as `skip`). Proceed to Step 6.

### Step 6 — End-of-walk wrap-up

After the walk completes (or user quits):
1. Bump frontmatter `last_updated` to today.
2. Rebuild the mirror from the full-Active copy you read in **Step 1** — apply the walk's net changes (`[quick-win]` suffixes on flagged items; drop deleted / promoted / completed / migrated lines). Do NOT re-read `Worklog.md`; Step 1 already loaded it.
3. Print summary:

```
Triage complete.
  Applied: <n> complete, <m> do-now (executed), <k> quick-win flagged, <p> promoted to Future Scope, <q> deleted.
  Skipped: <s>. Unaddressed (quit early): <r>.
  Active count: <before> → <after> (cap target: 30).
```

If `<after>` still exceeds 30, suggest:
```
Still over cap. Consider another /worklog triage pass on the lower-signal items.
```

### Edge cases for TRIAGE

- **Empty Active section:** print `Active is empty. Nothing to triage.` and stop before Step 3.
- **No items meet any trigger:** print `No actionable triage signals — items mostly need closer review than triage can offer. Try /worklog drive (or /worklog drive 3 to select and execute straight away) instead.` and stop before Step 4.
- **Mid-walk add request:** if the user asks to add a new item during triage, complete the add via the ADD recipe; warn that subsequent triage proposals reference state from before the add.
- **Race with parallel writes:** triage reads Active once at start. If another agent writes to Active during the walk, mirror rewrite at Step 6 overwrites based on post-triage state — could double-write or drop interleaved items. Solo-dev unlikely; if it surfaces, add a re-read step before mirror rewrite.
- **Quick-win flag already present on an item:** don't propose `flag` again. Default recommendation falls through to next-priority disposition (likely `skip` or another).
- **Do-now misclassified (work blows up):** abort do-now mid-execution, offer fallback dispositions inline, continue walk. Do not silently log a partially-done state.
- **`/regression_gate` failure on a do-now `.cs` change:** stop the walk. The user has a regression to investigate; that's not a triage matter. Item stays `[ ]` (un-completed); the `.cs` change either reverts (user choice) or stays uncommitted for follow-up.

## Operation: DRIVE

The agentic prioritization-and-execution op. Reads the worklog, selects logged items, and drives them through to commits. **Execution is the default — the invocation is the execution directive** (`feedback_honor_execution_directive`, `feedback_single_gate_no_secondary_approval`). Mode D (choose-among) still asks when the fill-set is ambiguous; that is the one selection gate. Once selection settles, never ask "start now?".

**Mode dispatch, ordered — first match wins, after stripping `--plan-only` from the argument:**

1. **Argument empty → Survey.** Score ready items, propose 2–3 batches, the user picks. Never executes (Step 4-Survey).
2. **The WHOLE argument matches `^(\d+|scope:\d+|items:\d+)(\s+(scope:\d+|items:\d+))*$` → Budget.** Bare `<N>` ≡ `scope:N`. `scope:` and `items:` combine; the **more restrictive** limit binds — stop filling as soon as either is hit. On ambiguity, `scope:` wins (better proxy for one-session capacity). Selection runs the scoring engine + Modes A–D, then continues into execution.
3. **Otherwise → Named-items.** Comma-split the argument, fuzzy-match each name against ready Active items (multi-match → ask, like COMPLETE). Selection is the user's, so scoring and Modes C/D don't apply. Then execute.

**`--plan-only`** (any mode with a selection): stop at the drafted plan body (4d) — non-executing. `drive 3 --plan-only` is the single-session plan-to-approval default.

**Depth is scope-proportional.** Each selected item is driven at its ladder tier per [`_brainstorm_shared/execution_depth.md`](../../skills/_brainstorm_shared/execution_depth.md) — that file's rule 1 (independent litmuses stay the binding floor) governs every gate this op would otherwise reduce.

**Scope-4: never selected, never driven.** Budget mode keeps the −5 score, the Mode B refusal, and the 4c big-ticket flag; named-items mode refuses a named scope-4 item with the same flag. In every case route to `/design_drive` (no design doc yet) or `/part_drive` (roadmap Part exists), against the item's linked Plan doc.

### Step 1 — Read full Active section

Need full content (date, context, source, where, class, scope), not just titles. The mirror isn't enough.
```
mcp__obsidian__obsidian_read_note(filePath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md")
```
Score `[ ]` items only (`Worklog.md` holds only active work — completions are in the archive). Then **partition into ready vs. waiting**: items with no `When:` sub-bullet are **ready** (proceed to scoring); items with `When: after ...` or `When: future` are **waiting** (skip scoring entirely — list in output only). Only ready items flow through Steps 2–4.

### Step 2 — Gather context signals

In parallel:
- `git status --short` — what's currently uncommitted? Items whose `Where:` paths overlap with these are HOT.
- `git log --oneline -10` — what just happened? Items whose context references recent commit topics are warm.
- Active-item ages — items added > 14 days ago are bit-rot candidates.
- Domain frequencies — domains with 2+ `[ ]` items are batch / cohesion candidates.
- **Class + scope distribution.** Lots of scope-1 items in one domain → quick-win sweep. A scope-4 `design` item → flag and route to `/design_drive` (no design doc yet) or `/part_drive` (roadmap Part exists), never batch or drive.

### Step 3 — Score every ready item

Per-item signal sum — the **shared scoring engine** both modes consume. For each ready item:
- **+3** if `Where:` paths or `Source:` refs overlap `git status` (hot-context).
- **+3** if the item has a `Quick-win:` sub-bullet (flagged by `/worklog triage` for next-session priority — explicit user intent signal). Stacks with hot-context.
- **+2** if `Source:` text matches recent commit messages (warm-context).
- **+2** if 2+ items share its `### Domain` section (cohesion candidate).
- **+1** per scope point above 1 (so scope-3 = +2, scope-2 = +1, scope-1 = 0). At equal heuristic weight, prefer higher-impact work. (Works *against* quick-win-flagged scope-1 items — intentional; the +3 quick-win bonus restores parity with mid-scope items.)
- **−5** if scope == 4 (effective rule-out from selection; scope-4 is handled by the big-ticket flag regardless of score).
- **−2** anti-thrash penalty if the item has an **unpaired** `tackle` event in `.claude/worklog-tackle-history.jsonl` within the last 14 days. "Unpaired" = a `tackle` event for this title with no later `completion` event for the same title. Read the JSONL via `Read`, filter to the last 14 days by `date` field, and check for unpaired matches per the schema in 4f. Empty file = no penalties (cold start).

Survey and Budget modes both consume this engine. **Named-items mode skips Steps 2–3 entirely** — the user made the selection.

### Step 4 — Fork on mode

Per the mode dispatch above: empty argument → **Step 4-Survey**; budget target → **Step 4-Budget** (4a–4g); named items → 4a's named path, then 4c–4g.

---

### Step 4-Survey — Batch and propose (no target)

**Batch the scored items** in heuristic priority order (first match wins per item):

0. **Quick-win-flagged batch** (highest priority, only fires when ≥1 flagged item exists): items carrying a `Quick-win:` sub-bullet — explicit user/triage intent signal. Surface as a dedicated batch above all others, named "Batch QW — flagged quick-wins".
1. **Hot-context batch**: items whose `Where:` paths or `Source:` refs overlap current `git status` or recent commits. Strike while context is loaded. (Quick-win-flagged items that ALSO match hot-context stay in batch 0; don't double-list.)
2. **Domain-cohesion batch**: 2+ items sharing the same `### Domain` section. One focused session knocks them out together. Bonus weight if they share a class (e.g., 3 `refactor` items in `vfx`).
3. **Quick-wins batch** (scope-1 sweep): scope-1 items across any domain *without* the `Quick-win:` flag — clearable in a single short session, regardless of cohesion. The flag-driven batch (priority 0) takes precedence.
4. **Stale-item batch** (oldest items): top 3–5 oldest `[ ]` items regardless of cohesion or scope. Clear backlog pressure.
5. **Big-ticket flag**: any single scope-4 item. Don't batch — route to `/design_drive` (no design doc yet) or `/part_drive` (roadmap Part exists), citing the linked Plan doc.

If Active has ≤ 2 `[ ]` items, skip batching entirely — just print them with "Want to drive one of these? (pass `<N>` / `items:N`, or name it, to go straight in)".

**Output proposal:**
```
Worklog drive — 3 candidate batches (<n> ready items; <m> waiting).

Batch A — <name> (<n> items, mix of scope <range>)
  Why: <one-line rationale citing the heuristic>
  Items:
    - <class>/<scope> · <title> (added <date>; <domain>)
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

Rationales should cite *which heuristic* fired (hot-context, cohesion, quick-wins, age) so the user can sanity-check the priority logic.

**On batch pick:** re-enter at 4a with the batch's items as the fill-set, then continue 4c–4g and execute. The batch's context blocks are already loaded — don't re-read them.

Survey mode itself does **not** log to tackle-history — it commits to nothing until a batch is picked.

---

### Step 4-Budget — Fill the target (budget target given)

#### 4a — Select the fill-set

**Budget mode:** add ready items by descending Step-3 score until the binding target is reached (scope sum hits `scope:N`, or item count hits `items:N`, whichever comes first). If two items tie at the inclusion boundary, both are surfaced as "contested alternates" in 4b.

**Named-items mode:** comma-split the argument; fuzzy-match each name against ready Active `[ ]` items. A name matching 2+ items → list them and ask which, exactly as COMPLETE does. A name matching none → say so and stop rather than substituting a near-miss. The fill-set is what the user named — no scoring, no target. Skip 4b (Modes C/D are selection heuristics the user has overridden); go to 4c.

**Never include scope-4 items in either mode** — they're flagged in 4c and routed, never driven.

#### 4b — Classify pick mode (budget mode only)

In priority order — first match wins:

**Mode A — `empty-state`.** Active has 0 ready items. Print:
```
Worklog clear: no ready items in Active.
- <m> waiting (after: ...): /worklog show all to review
- <k> in Future Scope: /worklog show all to review
```
Stop. If only waiting items exist (`m > 0`), additionally suggest `/worklog unblock <condition>` for each condition recent commits might satisfy.

**Mode B — `scope-4-only`.** The top-scored item is scope-4 and no viable smaller fill-set exists. Don't drive — refuse and route:
```
Top candidate is scope-4 (one-session viability cap is scope-3).

  <title> — <domain> · scope 4
  Plan doc: [[<doc>]]

Recommended: /design_drive (no design doc yet) or /part_drive (roadmap Part exists), against the linked plan doc. Or: re-run with a larger `items:N` target to surface smaller candidates.
```
Stop.

**Mode C — `auto-confirm`.** The fill-set is unambiguous — a single dominant item (top score ≥ 4, second-place < 60% of top) OR a multi-item fill-set with no boundary tie. Print and proceed to 4d:
```
Driving: <fill-set summary> — total scope ~<sum>
  Why: <rationale citing the dominant heuristic(s)>
  Scores: <per-item score list>
```

**Mode D — `choose-among`.** Ambiguous — a boundary tie, or top score < 4 with no clear winner. Present the fill-set plus contested alternates, let the user adjust:
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

Any scope-4 ready item — scored in budget mode, or explicitly named in named-items mode — is surfaced below the fill-set proposal and never silently dropped:
```
Big-ticket (excluded): <title> — scope 4, see [[Plan doc]]. Route to /design_drive (no design doc yet) or /part_drive (roadmap Part exists)?
```

#### 4d — Draft the plan body

Read each fill-set item's full `Context` / `Where` / `Source` block from `Worklog.md` before scoping it — never the mirror line (`feedback_plan_worklog_items_from_source_not_mirror`).

The body's **artifact** follows the ladder tier: tier 1 needs none, tier 2 keeps it in-conversation (a plan *file* only when the executor is dispatched cold), tier 3 writes `.claude/plans/<slug>.md`. Under `--plan-only` the body is always written out — it is the deliverable. Shape either way:
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
- <regression sentinel: which test suite must still pass after — usually `/regression_gate` for .cs work>

### Worklog completion

After landing: `/worklog complete <title>` (commit hash <pending>).
```

**Logic-domain note:** if a fill-set item lives in a Logic-domain area (`SpellArchitecture`, `Synergies`, `Jmodot.Core`, `Inventory`, `Math/Parsing`, `Data Structures` per CLAUDE.md), its **Steps** MUST start with a RED test (failing test that pins the bug or proves the new behavior is missing). No production-code step before a verifying test.

**Multi-item drafting:** partition the fill-set by `PLAN_SHAPE` — never one plan spanning both shapes ([*Plan-file format*](../../skills/_brainstorm_shared/plan_file_format.md) → *One shape per plan file*). Within a shape, default to one plan and split further only on that rule's cohesion litmus (one `Constraints` block and one `Verification` section genuinely cover the set). Items sharing an invariant get Steps structured as "do X once, then apply across A, B, C"; items with no shared invariant but still one plan's worth of spine get labelled sub-sequences under a single Verification section.

**Class-aware composition:**
- `class: design` items: do NOT draft an implementation plan inline. Instead, suggest invoking `/architecture_brainstorm` (which will route to `/idea_brainstorm` first if the design space is greenfield): `Picked a design item — recommend running /architecture_brainstorm first; it will route to /idea_brainstorm if the candidate pool is empty. Re-run drive once the design exists.` Stop without drafting that item (drive the rest of the fill-set if any).
- **Audit-shape items** (title starts `Audit`/`Verify`/`Review`/`Inspect`/`Check`, or Context is read-and-decide): execute the reads while drafting and render the verdict in the plan body; plan only consequent code changes. Compliant verdict → plan collapses to `/worklog complete` with verdict as `[x]` ref. Same for read-only `debug` reproduction.
- `class: debug` items: structure Steps per the `debugging` skill's 6-phase discipline (feedback loop → reproduce → patterns → hypothesise → fix → cleanup). Don't propose fixes before reproduction.
- `class: test` items: Steps describe what to assert and which fixture (`SpellTestFixture` / `CastingTestFixture`), not implementation.

#### 4e — Conditional `/plan_check`

After drafting, evaluate **each** drafted plan against `/plan_check`'s litmus (3+ files, new type/folder, refactor of 2+ subclass family, file deletion/replacement) — a shape partition produced two plans, and each is judged on its own. Nothing auto-invokes it — this step does.

- **Litmus trips → run `/plan_check <plan-file>` now** on every plan that trips it, lens set by that plan's own shape, and converge before executing. The ladder can narrow the lens set; it never waives a pass the litmus mandates (`execution_depth.md` rule 1).
- **`--plan-only` → surface it instead of running it**, so the user approves the plan and the check together:
  ```
  ---
  **Pre-execution gate:** This plan touches <N files | introduces <type> | refactors <family>>. Recommend `/plan_check <this-plan>` before approval.
  ```
- **No triggers → omit the block and proceed.**

#### 4f — Log to tackle-history

Append one `tackle` event line **per fill-set item** to `.claude/worklog-tackle-history.jsonl` (file is committed to repo, always exists, may be empty):

```json
{"event": "tackle", "date": "YYYY-MM-DD", "title": "<title verbatim>", "domain": "<domain>", "class": "<class>", "scope": <n>, "mode": "auto-confirm|choose-among|named", "score": <s>}
```

Use the Bash `printf '...\n' >> .claude/worklog-tackle-history.jsonl` pattern (NOT `echo` — `echo` may add OS-dependent line endings). The `title` field MUST be the verbatim title from the worklog so the COMPLETE recipe's pair-matching works. Multi-item fill-set → one line per item, so completion-pairing tracks per-item. Named-items mode writes `"mode": "named", "score": null` — no scoring ran.

##### JSONL schema reference

The history file holds two event shapes, both with `event` and `date`:

| Event | Required fields | Written by |
|-------|-----------------|------------|
| `tackle` | `event`, `date`, `title`, `domain`, `class`, `scope`, `mode`, `score` | DRIVE 4f |
| `completion` | `event`, `date`, `title` | COMPLETE Step 9 (pair-emit) |

The event name `tackle` is a **historical name**, retained because live history lines pair on it; the emitter is DRIVE.

**Pairing semantics:** an item is "completed after a tackle" iff a `completion` event for the same `title` has a `date` ≥ the most recent `tackle` event's `date` for that title. Step 3's −2 penalty fires when a `tackle` exists in the last 14 days AND no matching `completion` follows.

**Append-only.** Never rewrite or compact this file inline — it's small (one line per tackle/completion) and append-only is the simplest correctness contract. If it grows unwieldy (>1000 lines), introduce a separate `/worklog history-compact` operation that archives old entries.

#### 4g — Proceed, or stop under `--plan-only`

**Default: proceed straight into Step 5.** Announce the transition in one line — `Selection settled. Driving <n> item(s) now.` — and do not ask for a second go-ahead; the invocation was it.

**Under `--plan-only`,** stop here instead:
```
Plan drafted. Say the word when ready to start, or refine first.
```
If 4e surfaced a pre-execution gate, alter to:
```
Plan drafted with pre-execution gate flagged. Recommend running /plan_check before starting.
```

### Step 5 — Execute the fill-set

Group the fill-set and drive each item at its ladder tier ([`execution_depth.md`](../../skills/_brainstorm_shared/execution_depth.md)).

- **Dispatch shape** per CLAUDE.md §Model Delegation + the `orchestration` skill — the ladder governs process artifacts, not who executes. Independent items may fan out via the generic engines (`dispatch.js`) with explicit model + effort pins; dependent or same-file items serialize. **Test and build runs are single-flight:** one gate, serially, orchestrator-side, never per-agent (`gotcha_workflow_single_flight_concurrency`).
- **Gates are provenance-blind.** Any `.cs` change anywhere in the batch → one full `/regression_gate` before commits, however small the items.
- **Per-item close-out:** run the COMPLETE recipe with the commit ref, in the same session the item lands.

**Re-scope valve.** Trigger: the item's real file or decision count exceeds its logged scope tier mid-drive. Action: update the item's `scope:` value in `Worklog.md` (ADD-style edit), leave it `[ ]`, and report the re-scope. Budget mode continues into the deeper tier only if the remaining scope-point budget covers the new value; otherwise the item stays re-scoped-but-undriven. Named-items mode continues at the deeper tier unless the new tier is 4 → flag and route per the scope-4 rule. Never silently dropped, never silently deepened past the budget.

**Done-condition.** Every fill-set item ends in exactly one terminal state: COMPLETEd with a ref, or left `[ ]` with a re-scope note. The closing report lists all items with their state — an unlisted item means the drive is not done.

### Edge cases for DRIVE
- **Fill-set item already in current uncommitted work.** Hot-context can fire on items the user is *already* doing. Detect by checking whether `git status` files overlap the item's `Where:` paths AND the item appears `[ ]` (not yet completed). If suspected, ask: `<title> looks like work-in-progress — drive as continuation, or pick the next candidate?`
- **Survey-mode Hot-context batch overlaps current work too narrowly.** Batching items that ARE the current uncommitted work is silly — flag it rather than proposing it as fresh work.
- **User picks a fill-set then says "actually use a different one".** Re-run 4d with the new selection. Don't re-score (the user has overridden the heuristic).
- **Non-determinism.** The heuristic shifts with git context — the same Active list can yield different batches/fill-sets across sessions. Intentional for an agentic op, but worth flagging if a user asks "why this batch?".
- **Scope is a coarse effort proxy.** A scope-2 item in a system you've never touched can blow up to scope-3 reality. Treat the target as a sorting/sizing hint, not a contract — a fill-set item that visibly exceeds its scope mid-drive is the re-scope valve (Step 5), not a judgment call.
- **A named item is waiting or absent.** Named-items mode matches ready Active `[ ]` items only. A name resolving to a `When: after` / `future` item surfaces the gate and offers `/worklog unblock <condition>` or `/worklog promote <title>`; a name resolving to nothing stops rather than substituting a near-miss.

---

## Examples (DRIVE)

### Survey mode (no argument)

User: `/worklog drive`

1. Read Worklog.md → 8 `[ ]` items across 4 domains.
2. `git status` shows changes in `Tests/Logic/AI/*`. `git log` shows recent BehaviorTree commits.
3. Score every ready item (Step 3 signal sums):
   - Hot-context: 2 items reference BehaviorTree (matches uncommitted Tests).
   - Cohesion: 2 items in `### AI / NPCs` (one overlaps Hot-context).
   - Quick-wins: 3 scope-1 items across `docs` + `chore` + `tooling`.
   - Stale: 1 item > 30 days old in `ui`.
   - Big-ticket: 1 scope-4 `design` item with linked doc — flag and route to `/design_drive` or `/part_drive`.
4. Step 4-Survey batches: Batch A (Hot + AI cohesion merged), Batch B (Quick-wins scope-1 sweep), Big-ticket flag.
5. User picks A → re-enter at 4a with A's items as the fill-set, then 4c–4g and Step 5.

### Budget mode (`/worklog drive 3` ≡ `drive scope:3`)

User types: `/worklog drive 3`

1. Read Worklog.md → 12 ready items across 5 domains. Read git context.
2. Score (Step 3):
   - `Migrate spell charge duration from charge-visual scene to statsheet` — `Where:` overlaps `git status` (uncommitted changes in `spell/charge_visual/*.tscn`). Hot-context +3, scope-2 +1 = **4**.
   - `Per-enemy status duration resistance` — scope-3 +2, no hot match = **2**.
   - `Convention for collision-chain ordering` — scope-1, no signals = **0**.
   - Other items < 3.
3. Step 4-Budget 4a: target `scope:3`. Fill-set = the score-4 scope-2 item (adding the next item would push the scope sum past 3). 4b → Mode C `auto-confirm` (top score 4, second-place 2 = 50% of top). Print:
   ```
   Driving: Migrate spell charge duration from charge-visual scene to statsheet — total scope ~2
     Why: Where path matches uncommitted spell/charge_visual/* changes (hot-context).
     Scores: 4
   ```
4. 4c: no scope-4 ready items → no big-ticket flag. 4d: scope-2 → ladder tier 2, so the body stays in-conversation. Logic-domain item (SpellArchitecture) → Steps start with a RED test against the new statsheet field's expected behavior.
5. 4e `/plan_check` evaluation: 2 files touched, no new types, no subclass refactor → no litmus trip. Skip the block.
6. 4f: append one `tackle` event to `.claude/worklog-tackle-history.jsonl`.
7. 4g: `Selection settled. Driving 1 item now.` → Step 5: tier-2 depth (floor lenses skipped — `Where:` paths already read first-party), RED→GREEN, one `/regression_gate` for the `.cs` change, commit, COMPLETE with the ref.

### Named-items mode

User: `/worklog drive hit flash, NPC tests`

1. Argument is not budget-shaped → Named-items. Skip Steps 2–3 (no scoring).
2. 4a: comma-split → `hit flash` matches one ready item; `NPC tests` matches two — list both, user picks. Fill-set is the two chosen items.
3. 4b skipped (user selection). 4c: neither is scope-4 → no flag.
4. 4d–4f: read both source blocks, draft per tier, log two `tackle` events with `"mode": "named", "score": null`.
5. Step 5: same-file items serialize; one `/regression_gate` covers the batch; each item COMPLETEs with its commit ref. Closing report lists both items with their terminal state.

### Plan-only

User: `/worklog drive 3 --plan-only`

Strip `--plan-only` → argument `3` → Budget mode. Selection and 4a–4f run identically; 4g stops at the drafted plan body instead of entering Step 5. A tripped `/plan_check` litmus is surfaced in 4e, not run.

### Scope-4 refusal

User: `/worklog drive scope:3`

1. Score → the only meaningfully-scored ready item is `Core Elemental Spells tier-1 implementation` (scope 4, −5 penalty). No viable smaller fill-set exists.
2. 4b Mode B fires. Print:
   ```
   Top candidate is scope-4 (one-session viability cap is scope-3).

     Core Elemental Spells tier-1 implementation — spell · scope 4
     Plan doc: [[Core Elemental Spells Brainstorm v1.1]]

   Recommended: /design_drive (no design doc yet) or /part_drive (roadmap Part exists), against the linked plan doc. Or: re-run with a larger items:N target to surface smaller candidates.
   ```
3. Stop. Nothing drafted, nothing driven. Return.
