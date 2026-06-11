---
description: Companion recipe file for /worklog PLAN + TRIAGE — loaded by the worklog command on demand, never invoked directly.
disable-model-invocation: true
---

# /worklog — PLAN + TRIAGE recipes

Extracted from `worklog.md` so the common `add`/`complete`/`show` path doesn't load these recipes. Loaded on demand: Read this file when `plan`, `tackle`, or `triage` is invoked, then execute from its steps. The Forms table, cross-cutting rules (mirror maintenance, frontmatter bump, de-dup), cloud fallback, and all other operation recipes stay in `worklog.md`.

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
  targetType="filePath",
  targetIdentifier="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md",
  replacements=[{
    search: "  - Context: <verbatim context line>\n",
    replace: "  - Context: <verbatim context line>\n  - Quick-win: flagged YYYY-MM-DD\n"
  }]
)
```

Item stays in Active. Mirror rewrite (Step 6) appends `[quick-win]` suffix to its mirror line. `/worklog plan` weights flagged items +3 in scoring — survey-mode batching and draft-mode fill alike (treated as high hot-context).

#### `promote` (Active → Future Scope)
Inverse of `/worklog promote`. Two-step search_replace:

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
- **No items meet any trigger:** print `No actionable triage signals — items mostly need closer review than triage can offer. Try /worklog plan (or /worklog plan scope:3 to draft straight into one) instead.` and stop before Step 4.
- **Mid-walk add request:** if the user asks to add a new item during triage, complete the add via the ADD recipe; warn that subsequent triage proposals reference state from before the add.
- **Race with parallel writes:** triage reads Active once at start. If another agent writes to Active during the walk, mirror rewrite at Step 6 overwrites based on post-triage state — could double-write or drop interleaved items. Solo-dev unlikely; if it surfaces, add a re-read step before mirror rewrite.
- **Quick-win flag already present on an item:** don't propose `flag` again. Default recommendation falls through to next-priority disposition (likely `skip` or another).
- **Do-now misclassified (work blows up):** abort do-now mid-execution, offer fallback dispositions inline, continue walk. Do not silently log a partially-done state.
- **`/regression_gate` failure on a do-now `.cs` change:** stop the walk. The user has a regression to investigate; that's not a triage matter. Item stays `[ ]` (un-completed); the `.cs` change either reverts (user choice) or stays uncommitted for follow-up.

## Operation: PLAN

The agentic prioritization op. Reads the worklog, scores ready items once, then **forks on whether a capacity target was given**: no target → propose exploratory batches (*survey mode*); target given → draft a plan-mode-ready body sized to the target (*draft mode*).

**Argument:** optional capacity target — `scope:<N>` and/or `items:<N>`.
- **No target** → *survey mode*: propose 2–3 prioritized batches; the user picks one to plan.
- **Target given** → *draft mode*: fill the target with the highest-scoring ready items, draft a ready-to-execute plan body, log to tackle-history.
- If both `scope:` and `items:` are given, the **more restrictive** limit binds — stop filling as soon as either is hit. On ambiguity, `scope:` wins (better proxy for one-session capacity).

**`tackle` alias:** `/worklog tackle` ≡ `/worklog plan scope:3` — the single-session draft-mode default. Everything in the *draft mode* path (Step 4-Draft) applies.

**Draft-mode rules:**
- **Scope-4 never enters a drafted body** — it needs its own design doc + multi-session arc. Draft mode surfaces it as a big-ticket flag and routes to `mcp__ccd_session__spawn_task` (Step 4-Draft, Mode B / 4c). Survey mode also flags scope-4, never batches it.
- **Plan-mode assumption.** Draft mode assumes the harness is already in plan mode. If it isn't, the output is still a usable draft body — warn at the top, don't refuse. Never try to enter plan mode yourself — you can't.

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
- **Class + scope distribution.** Lots of scope-1 items in one domain → quick-win sweep. A scope-4 `design` item → flag for `spawn_task`, never batch or draft.

### Step 3 — Score every ready item

Per-item signal sum — the **shared scoring engine** both modes consume. For each ready item:
- **+3** if `Where:` paths or `Source:` refs overlap `git status` (hot-context).
- **+3** if the item has a `Quick-win:` sub-bullet (flagged by `/worklog triage` for next-session priority — explicit user intent signal). Stacks with hot-context.
- **+2** if `Source:` text matches recent commit messages (warm-context).
- **+2** if 2+ items share its `### Domain` section (cohesion candidate).
- **+1** per scope point above 1 (so scope-3 = +2, scope-2 = +1, scope-1 = 0). At equal heuristic weight, prefer higher-impact work. (Works *against* quick-win-flagged scope-1 items — intentional; the +3 quick-win bonus restores parity with mid-scope items.)
- **−5** if scope == 4 (effective rule-out from drafting; scope-4 is handled by the big-ticket flag regardless of score).
- **−2** anti-thrash penalty if the item has an **unpaired** `tackle` event in `.claude/worklog-tackle-history.jsonl` within the last 14 days. "Unpaired" = a `tackle` event for this title with no later `completion` event for the same title. Read the JSONL via `Read`, filter to the last 14 days by `date` field, and check for unpaired matches per the schema in Step 4-Draft 4f. Empty file = no penalties (cold start).

### Step 4 — Fork on mode

No target → **Step 4-Survey**. Target given (`scope:` and/or `items:`, or the `tackle` alias) → **Step 4-Draft**.

---

### Step 4-Survey — Batch and propose (no target)

**Batch the scored items** in heuristic priority order (first match wins per item):

0. **Quick-win-flagged batch** (highest priority, only fires when ≥1 flagged item exists): items carrying a `Quick-win:` sub-bullet — explicit user/triage intent signal. Surface as a dedicated batch above all others, named "Batch QW — flagged quick-wins".
1. **Hot-context batch**: items whose `Where:` paths or `Source:` refs overlap current `git status` or recent commits. Strike while context is loaded. (Quick-win-flagged items that ALSO match hot-context stay in batch 0; don't double-list.)
2. **Domain-cohesion batch**: 2+ items sharing the same `### Domain` section. One focused session knocks them out together. Bonus weight if they share a class (e.g., 3 `refactor` items in `vfx`).
3. **Quick-wins batch** (scope-1 sweep): scope-1 items across any domain *without* the `Quick-win:` flag — clearable in a single short session, regardless of cohesion. The flag-driven batch (priority 0) takes precedence.
4. **Stale-item batch** (oldest items): top 3–5 oldest `[ ]` items regardless of cohesion or scope. Clear backlog pressure.
5. **Big-ticket flag**: any single scope-4 item. Don't batch — propose `mcp__ccd_session__spawn_task` instead, citing the linked Plan doc.

If Active has ≤ 2 `[ ]` items, skip batching entirely — just print them with "Want to plan one of these? (pass `scope:N` / `items:N` to draft straight into one)".

**Output proposal:**
```
Worklog plan — 3 candidate batches (<n> ready items; <m> waiting).

Batch A — <name> (<n> items, mix of scope <range>)
  Why: <one-line rationale citing the heuristic>
  Items:
    - <class>/<scope> · <title> (added <date>; <domain>)
    - <class>/<scope> · <title> (added <date>; <domain>)
    ...

Batch B — ...

Batch C — ...

Big-ticket (won't batch): <title> — scope 4, see [[Plan doc]]. Spawn task?

Waiting (not scored):
  - <title> [after: <condition>] (<domain>)
  - <title> [future] (<domain>)
  (omit this section entirely if no waiting items)

Pick a batch (A/B/C) to plan, or re-run with `scope:N` / `items:N` to draft one directly.
```

Rationales should cite *which heuristic* fired (hot-context, cohesion, quick-wins, age) so the user can sanity-check the priority logic.

**On batch pick:** two options —
- **Inline plan:** outline an approach for the batch's items in the current turn. Lighter-weight, doesn't enter Plan mode.
- **Plan mode hand-off:** if the batch is non-trivial, suggest entering Plan mode (the user has to invoke; you can't). Pre-populate a draft plan with the batch's items and their context blocks already filled in so they have a head-start.

Survey mode does **not** log to tackle-history — it doesn't commit to a drafted body.

---

### Step 4-Draft — Fill the target and draft a body (target given)

#### 4a — Select the fill-set

Add ready items by descending Step-3 score until the binding target is reached (scope sum hits `scope:N`, or item count hits `items:N`, whichever comes first). **Never include scope-4 items** — they're flagged in 4c, never drafted. If two items tie at the inclusion boundary, both are surfaced as "contested alternates" in 4b.

#### 4b — Classify pick mode

In priority order — first match wins:

**Mode A — `empty-state`.** Active has 0 ready items. Print:
```
Worklog clear: no ready items in Active.
- <m> waiting (after: ...): /worklog show all to review
- <k> in Future Scope: /worklog show all to review
```
Stop. If only waiting items exist (`m > 0`), additionally suggest `/worklog unblock <condition>` for each condition recent commits might satisfy.

**Mode B — `scope-4-only`.** The top-scored item is scope-4 and no viable smaller fill-set exists. Don't draft — refuse and route:
```
Top candidate is scope-4 (one-session viability cap is scope-3).

  <title> — <domain> · scope 4
  Plan doc: [[<doc>]]

Recommended: spawn a parallel session via `mcp__ccd_session__spawn_task` with the linked plan doc as context. Or: re-run with a larger `items:N` target to surface smaller candidates.
```
Stop.

**Mode C — `auto-confirm`.** The fill-set is unambiguous — a single dominant item (top score ≥ 4, second-place < 60% of top) OR a multi-item fill-set with no boundary tie. Print and proceed to 4d:
```
Tackling: <fill-set summary> — total scope ~<sum>
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

Any scope-4 ready item is surfaced below the fill-set proposal — never silently dropped:
```
Big-ticket (excluded from draft): <title> — scope 4, see [[Plan doc]]. Spawn task?
```

#### 4d — Draft the plan body

For the fill-set, produce a plan-mode-ready body:
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

**Multi-item drafting:** if the fill-set has 2+ items, draft ONE plan covering them all — not separate plans. Find the shared invariant and structure Steps as "do X once, then apply across A, B, C". If the items have no shared invariant, structure Steps as labelled sub-sequences but keep a single Verification section.

**Class-aware composition:**
- `class: design` items: do NOT draft an implementation plan inline. Instead, suggest invoking `/architecture_brainstorm` (which will route to `/idea_brainstorm` first if the design space is greenfield): `Picked a design item — recommend running /architecture_brainstorm first; it will route to /idea_brainstorm if the candidate pool is empty. Re-run plan once the design exists.` Stop without drafting that item (draft the rest of the fill-set if any).
- **Audit-shape items** (title starts `Audit`/`Verify`/`Review`/`Inspect`/`Check`, or Context is read-and-decide): execute the reads in plan mode and render the verdict in the plan body; plan only consequent code changes. Compliant verdict → plan collapses to `/worklog complete` with verdict as `[x]` ref. Same for read-only `debug` reproduction.
- `class: debug` items: structure Steps per the `debugging` skill's 6-phase discipline (feedback loop → reproduce → patterns → hypothesise → fix → cleanup). Don't propose fixes before reproduction.
- `class: test` items: Steps describe what to assert and which fixture (`SpellTestFixture` / `CastingTestFixture`), not implementation.

#### 4e — Conditional `/plan_check` recommendation

After drafting, evaluate whether the draft would trigger `/plan_check` per CLAUDE.md's litmus (3+ files, new type/folder, refactor of 2+ subclass family, file deletion/replacement). If yes, append to the output:

```
---
**Pre-execution gate:** This plan touches <N files | introduces <type> | refactors <family>>. Recommend `/plan_check <this-plan>` before approval. Run it now? (y/n)
```

If no triggers fire, omit this block.

#### 4f — Log to tackle-history

Append one `tackle` event line **per fill-set item** to `.claude/worklog-tackle-history.jsonl` (file is committed to repo, always exists, may be empty):

```json
{"event": "tackle", "date": "YYYY-MM-DD", "title": "<title verbatim>", "domain": "<domain>", "class": "<class>", "scope": <n>, "mode": "auto-confirm|choose-among", "score": <s>}
```

Use the Bash `printf '...\n' >> .claude/worklog-tackle-history.jsonl` pattern (NOT `echo` — `echo` may add OS-dependent line endings). The `title` field MUST be the verbatim title from the worklog so the COMPLETE recipe's pair-matching works. Multi-item fill-set → one line per item, so completion-pairing tracks per-item.

##### JSONL schema reference

The history file holds two event shapes, both with `event` and `date`:

| Event | Required fields | Written by |
|-------|-----------------|------------|
| `tackle` | `event`, `date`, `title`, `domain`, `class`, `scope`, `mode`, `score` | PLAN draft mode Step 4f |
| `completion` | `event`, `date`, `title` | COMPLETE Step 8 (pair-emit) |

**Pairing semantics:** an item is "completed after a tackle" iff a `completion` event for the same `title` has a `date` ≥ the most recent `tackle` event's `date` for that title. Step 3's −2 penalty fires when a `tackle` exists in the last 14 days AND no matching `completion` follows.

**Append-only.** Never rewrite or compact this file inline — it's small (one line per tackle/completion) and append-only is the simplest correctness contract. If it grows unwieldy (>1000 lines), introduce a separate `/worklog history-compact` operation that archives old entries.

#### 4g — Confirm to user

End with a one-liner:
```
Plan drafted. ExitPlanMode when ready to start, or refine first.
```

If a `/plan_check` recommendation was surfaced in 4e, alter to:
```
Plan drafted with pre-execution gate flagged. Recommend running /plan_check before ExitPlanMode.
```

### Edge cases for PLAN

- **Draft mode invoked outside plan mode.** The output is still useful (a draft plan body) but warn at the top: `(Note: not in plan mode — output is a draft you can paste into plan mode, or read for guidance.)`. Don't refuse to run.
- **Fill-set item already in current uncommitted work.** Hot-context can fire on items the user is *already* doing. Detect by checking whether `git status` files overlap the item's `Where:` paths AND the item appears `[ ]` (not yet completed). If suspected, ask: `<title> looks like work-in-progress — draft as continuation, or pick the next candidate?`
- **Survey-mode Hot-context batch overlaps current work too narrowly.** Batching items that ARE the current uncommitted work is silly — flag it rather than proposing it as fresh work.
- **User picks a fill-set then says "actually use a different one".** Re-run 4d with the new selection. Don't re-score (the user has overridden the heuristic).
- **Non-determinism.** The heuristic shifts with git context — the same Active list can yield different batches/fill-sets across sessions. Intentional for an agentic op, but worth flagging if a user asks "why this batch?".
- **Scope is a coarse effort proxy.** A scope-2 item in a system you've never touched can blow up to scope-3 reality. Treat the target as a sorting/sizing hint, not a contract — if a fill-set item visibly exceeds its scope mid-draft, say so.

---

## Examples (PLAN)

### Plan cycle (survey mode — no target)

User: `/worklog plan`

1. Read Worklog.md → 8 `[ ]` items across 4 domains.
2. `git status` shows changes in `Tests/Logic/AI/*`. `git log` shows recent BehaviorTree commits.
3. Score every ready item (Step 3 signal sums):
   - Hot-context: 2 items reference BehaviorTree (matches uncommitted Tests).
   - Cohesion: 2 items in `### AI / NPCs` (one overlaps Hot-context).
   - Quick-wins: 3 scope-1 items across `docs` + `chore` + `tooling`.
   - Stale: 1 item > 30 days old in `ui`.
   - Big-ticket: 1 scope-4 `design` item with linked doc — flag for spawn_task.
4. Step 4-Survey batches: Batch A (Hot + AI cohesion merged), Batch B (Quick-wins scope-1 sweep), Big-ticket flag.
5. User picks A. Suggest entering Plan mode with pre-drafted notes. No tackle-history log (survey mode).

### Plan cycle (draft mode — `/worklog tackle` ≡ `plan scope:3`)

User enters plan mode, then types: `/worklog tackle`

1. Read Worklog.md → 12 ready items across 5 domains. Read git context.
2. Score (Step 3):
   - `Migrate spell charge duration from charge-visual scene to statsheet` — `Where:` overlaps `git status` (uncommitted changes in `spell/charge_visual/*.tscn`). Hot-context +3, scope-2 +1 = **4**.
   - `Per-enemy status duration resistance` — scope-3 +2, no hot match = **2**.
   - `Convention for collision-chain ordering` — scope-1, no signals = **0**.
   - Other items < 3.
3. Step 4-Draft 4a: target `scope:3`. Fill-set = the score-4 scope-2 item (adding the next item would push the scope sum past 3). 4b → Mode C `auto-confirm` (top score 4, second-place 2 = 50% of top). Print:
   ```
   Tackling: Migrate spell charge duration from charge-visual scene to statsheet — total scope ~2
     Why: Where path matches uncommitted spell/charge_visual/* changes (hot-context).
     Scores: 4
   ```
4. 4c: no scope-4 ready items → no big-ticket flag. 4d: draft the plan body. Logic-domain item (SpellArchitecture) → Steps start with a RED test against the new statsheet field's expected behavior.
5. 4e `/plan_check` evaluation: 2 files touched, no new types, no subclass refactor → no gate trigger. Skip the block.
6. 4f: append one `tackle` event to `.claude/worklog-tackle-history.jsonl`.
7. 4g: print `Plan drafted. ExitPlanMode when ready to start, or refine first.`

### Plan cycle (draft mode — scope-4 refusal)

User: `/worklog plan scope:3`

1. Score → the only meaningfully-scored ready item is `Core Elemental Spells tier-1 implementation` (scope 4, −5 penalty). No viable smaller fill-set exists.
2. 4b Mode B fires. Print:
   ```
   Top candidate is scope-4 (one-session viability cap is scope-3).

     Core Elemental Spells tier-1 implementation — spell · scope 4
     Plan doc: [[Core Elemental Spells Brainstorm v1.1]]

   Recommended: spawn a parallel session via mcp__ccd_session__spawn_task with the linked plan doc as context. Or: re-run with a larger items:N target to surface smaller candidates.
   ```
3. Stop. No plan body drafted. Return.
