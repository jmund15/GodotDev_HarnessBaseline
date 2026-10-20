---
disable-model-invocation: true
---

# `/worklog` — TRIAGE recipe

`commands/worklog.md` owns the Forms table, the tier model, the cross-cutting rules and the cloud fallback; entry formats live in `worklog_formats.md` and mirror-patch format in `worklog_mirror.md`.

## Operation: TRIAGE

Bulk cleanup. Walks open Active and Ledger items proposing per-item dispositions; confirmation-driven, never auto-applies.

### Step 1 — Read `## Active` AND `## Ledger`

`READ(Worklog.md)`. Full content (Context, Where, Source, dates, class, scope) is needed to evaluate dispositions; the mirror is insufficient. Triage is the operation that rebalances the two open tiers, so inspect **both**. An item's tier informs readiness but is not a filter on review. `When: future` items live in Future Scope and are out of scope.

### Step 2 — Gather context signals (in parallel)

- For each item's named Source or last decision, inspect commits back far enough to determine whether it shipped or became stale; record the checked interval.
- `git status --short` — uncommitted changes.
- Today's date, for age math only when a disposition otherwise needs it; age alone is not a disposition signal.

### Step 3 — Score each item's recommended disposition

Derive ONE disposition per `[ ]` item — priority order, first match wins:

| Disposition | Trigger | Action |
|---|---|---|
| **complete** | First-party evidence confirms the item's requested outcome actually shipped | run COMPLETE with the matched commit hash or source ref |
| **do-now** | A small, mechanical next action can be completed and verified in this turn | execute the work this turn, then COMPLETE |
| **quick-win** | A ready item has a user-confirmed quick-win signal and remains actionable | add `Quick-win: flagged YYYY-MM-DD` sub-bullet; item stays in Active with the user's intent recorded |
| **delete** | The block is a duplicate or invalid record with no unique work or completion history to retain | remove the `BLOCK` entirely (no archive — git history is the record) |
| **promote** | The item's readiness or owner changed so it is no longer an Active next action | if `When: after <condition>` applies, move the full `BLOCK` to Ledger retaining that condition; only an explicit `When: future` becomes an `FSLINE`. Do not move dormant-but-ready work for age alone. |
| **to-user-tasks** | fundamentally user-only addressable: `feature` items whose output is production art (`worklog_reference`'s User-Tasks triggers name the art signals); `design`-class items whose output is user taste / vision, not technical alternatives; items whose `Context:` implies user judgment (playtest tuning, art preferences, lore decisions). High-confidence triggers only — when uncertain, prefer `skip`. | migrate to `User-Tasks.md` via USER-ADD (`worklog_add.md`), then delete the Active block |
| **skip** | default — no strong signal | no-op, item stays as-is |

Present every supported disposition in manageable batches. The user can stop the walk; count unaddressed items separately from reviewed `skip` items. Never hide high-confidence items behind a proposal quota.

### Step 4 — Walk items in disposition-priority order

Header summary first:
```
Triage: <total> items. Proposed: <n> complete, <m> do-now, <k> quick-win, <p> promote, <u> to-user-tasks, <q> delete. Walking now (highest-signal first).
```
Then per proposed item:
```
[I/N] <Title> — <domain> · <class> · scope <s> · added <date>
  Context: <one-line>
  Recommended: <disposition>
  Why: <one-line rationale citing the trigger>

  Apply? [y]es-recommended  [c]omplete  [d]o-now  [f]lag-quick-win  [p]romote  [u]ser-tasks  [x] delete  [s]kip  [q]uit-triage
```
Single-letter response. `y` applies the recommendation; `q` stops the walk (remaining items unaddressed).

### Step 5 — Execute the chosen disposition

#### `complete`
Run COMPLETE (`worklog_complete.md`). Pre-fill a Step-2 matched commit hash as `<ref>`; else prompt for a ref or accept "no ref".

#### `do-now`
1. Do-now is for mechanical-class work. If the change grows past the item's `Where:` list, dispatch it through `orchestration` per `worklog_reference` §Do-now gate. If it gains an owner decision, abort do-now and offer fallback dispositions inline (`[c]omplete-after-manual / [f]lag for next session / [s]kip`).
2. Make the change.
3. Verify by change class:
   - A change class the project gates → its gate, MANDATORY (`change_control` §Gate cadence names each class's gate).
   - Doc-only / `.md` → no verification needed.
4. Run COMPLETE with today's date as ref, or the commit hash if a commit lands this turn.
5. Resume the walk on the next item.

The walk pauses while you do the work — never batch do-now items to the end; inline execution lets the user see results before deciding the next item.

#### `flag` (quick-win)
`SR(Worklog.md, search: "  - Context: <verbatim context line>\n", replace: "  - Context: <verbatim context line>\n  - Quick-win: flagged YYYY-MM-DD\n")` — the sub-bullet lands immediately after `Context:`. Item stays in Active; Step 6's mirror rewrite appends the `[quick-win]` suffix to its line. DRIVE's ranking step owns how the flag counts.

#### `promote` (Active → Ledger or Future Scope)

Use the destination that matches the explicit readiness signal. For `When: after <condition>`, move the entire `BLOCK` from `## Active` to `## Ledger`, preserving its `Context`, `Where`, `Source`, and `When: after` line. Only an explicit `When: future` routes to `## Future Scope` as an `FSLINE`, using ADD step 5b's insertion and count-update logic (`worklog_add.md`). Do not convert a dormant-but-ready item to Future Scope based on age. Preserve confirmation before any deletion or opaque User-Tasks move.

#### `to-user-tasks` (Active → User-Tasks parallel doc)

For production art, feel-tuning, brainstorms whose output is user taste, cross-doc vision audits. **Monodirectional** unlike `promote`: `User-Tasks.md` is opaque to every future agent pass, so the item leaves Claude's awareness entirely. Confirm a `refactor`/`fix`/`test`/`chore` item a second time before migrating it — migration removes it from every agent pass, and those classes have technical outputs Claude can produce.

Derive the `UTLINE` from the `BLOCK` and run USER-ADD (`worklog_add.md`) with those fields first: title unchanged minus the bold, date = today (re-clocking marks the migration moment, not the original log date), Context condensed to one line that explains the user's action, same domain, skipping USER-ADD's Step 1 de-dup because the walker already showed existing entries. Delete the `BLOCK` from `## Active` (same precise multi-line `SR` as `promote`) only after that write succeeds. If the USER-ADD write failed, keep the Active block, surface the error and offer `[s]kip` or `[p]romote` — better a stuck Active item than a lost migration.

Frontmatter bump + mirror rewrite happen once in Step 6; the migrated item drops from the mirror.

#### `delete`
Remove the `BLOCK` entirely. No archive — git history is the record. An item holding unlanded design or investigation content gets `Confirm DELETE (no archive): <title>? [yes/no]`; a mechanical item takes the `x` keypress alone.

#### `skip`
No-op. Move to the next item.

#### `quit`
Stop the walk. Count remaining items as `unaddressed`, not `skip`. Proceed to Step 6.

### Step 6 — End-of-walk wrap-up

1. **Rebalance by readiness.** Inspect every surviving `[ ]` item in `## Active` and `## Ledger`, even when Active is empty or no item received a proposed disposition. Keep ready items in Active and waiting `When: after <condition>` items in Ledger. Ledger-to-Active promotion follows the TRIAGE rule in `worklog.md`. Leave other unscheduled runnable debt in Ledger. Do not move a dormant-but-ready item merely for age or to satisfy a display target. Report the net move; keep the ready set scannable without a cutoff or silent demotion.
2. `FM(Worklog.md)`.
3. Patch the mirror for the walk's net changes and readiness rebalance, including `[quick-win]` suffixes, removed items and both supersection counts. Follow `.claude/commands/reference/worklog_mirror.md` for a stale mirror.
4. Print:
```
Triage complete.
  Applied: <n> complete, <m> do-now (executed), <k> quick-win flagged, <p> moved to Ledger/Future Scope, <q> deleted.
  Reviewed skips: <s>. Unaddressed: <r>.
  Active <before> → <after> · Ledger <before> → <after>.
```
Count reviewed `skip` items separately from unaddressed items left when the user stops the walk.

### Edge cases for TRIAGE

- **Empty Active or no proposals:** the walk still reaches Step 6, whose readiness rebalance runs even when no item received a proposed disposition.
- **Mid-walk add request:** complete the add via ADD; warn that later proposals use pre-add state.
- **Race with parallel writes:** triage reads Active once at start, so another agent's mid-walk write can be double-written or dropped by the Step 6 rewrite. Solo-dev unlikely; if it surfaces, add a re-read before the mirror rewrite.
- **Quick-win flag already present:** don't propose `flag` again; fall through to the next-priority disposition.
- **Do-now misclassified (work blows up):** abort mid-execution, offer fallbacks inline, continue the walk. Never silently log a partially-done state.
- **Gate failure on a do-now change:** stop the walk — a regression is not a triage matter. The item stays `[ ]`; the change reverts or stays uncommitted at the user's choice.
