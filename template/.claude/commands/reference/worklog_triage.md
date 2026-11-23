---
disable-model-invocation: true
---

# `/worklog triage`

Read at a `/worklog triage` and run from these steps. `commands/worklog.md` owns the files, tiers, write rules and the forms each disposition calls; formats are in `worklog_formats.md`. Triage proposes; the owner confirms every disposition.

## Step 1 — Read

Read `Worklog.md` whole: a disposition needs each item's Context, Where, Source and dates, which the mirror lacks. Review every `[ ]` item in Active and Ledger; Future Scope is out of scope.

## Step 2 — Gather evidence

For each item, read the commits back to its `Source:` or last recorded decision, far enough to tell whether it shipped or went stale, and record each range. Read `git status --short`. Age alone is never a disposition signal.

## Step 3 — Propose one disposition per item

First match wins:

| Disposition | When | Action |
|---|---|---|
| complete | First-party evidence shows the outcome shipped | `/worklog complete` with the matched ref |
| do-now | A small, mechanical action can be finished and verified this turn | Do it (Step 5), then complete |
| quick-win | The owner confirms a quick-win signal on a ready item | Add `Quick-win: flagged <today>` after its `Context:` line |
| delete | A duplicate or invalid record with no unique work or history | Remove the block; git history is the record |
| move | Its readiness changed: it now waits on a condition, or is explicitly `future` | A `When: after` item moves whole to Ledger; only an explicit `When: future` becomes a Future Scope line. Never move ready work for age |
| to-user-tasks | Only the owner can do it (`worklog_reference` §User-Tasks triggers), on a high-confidence signal | `/worklog user-add` from the block, then remove the block |
| skip | No strong signal | Nothing |

## Step 4 — Walk

Print `Triage: <total> items. Proposed: <n> complete, <m> do-now, <k> quick-win, <p> move, <u> to-user-tasks, <q> delete.` Then walk the proposals highest-signal first, in batches the owner can read:

```
[I/N] <Title> — <domain> · <class> · scope <s> · added <date>
  Context: <line>
  Recommended: <disposition> — <the evidence>
  [y]es  [c]omplete  [d]o-now  [f]lag  [m]ove  [u]ser-tasks  [x] delete  [s]kip  [q]uit
```

`q` stops the walk; the items left are unaddressed, counted apart from reviewed skips. A disposition never hides behind a proposal quota.

## Step 5 — Apply

- **do-now:** make the change, then run the gate its change class takes (`change_control` names it; a doc-only change takes none), then complete with today's date or the commit ref. Do it inline before the next item, so the owner sees the result. When it grows past the item's `Where:` list, dispatch it through `orchestration`; when it needs an owner decision, stop and offer complete-later, flag or skip. A failed gate stops the walk: the item stays open and the change stays uncommitted for the owner.
- **to-user-tasks:** confirm a `refactor`, `fix`, `test` or `chore` item a second time, since the move takes it out of every agent pass. Remove the block only after `user-add` succeeds; on a failed write keep the block and offer skip or move.
- **delete:** an item holding unlanded design or investigation content confirms `Delete (no archive): <title>?`; a mechanical item takes the keypress alone.

## Step 6 — Wrap up

1. Rebalance by readiness across every surviving item, even with no proposals: ready items in Active, waiting `When: after` items in Ledger, a Ledger item to Active only when its condition cleared, other unscheduled debt left in Ledger.
2. Bump `last_updated:` and rebuild the mirror once from the Step 1 read plus the walk's changes. Another write may have landed during the walk; re-read `Worklog.md` before the rebuild when it did.
3. Print:
   ```
   Triage complete. Applied: <n> complete, <m> do-now, <k> flagged, <p> moved, <u> to User-Tasks, <q> deleted.
   Reviewed skips: <s>. Unaddressed: <r>. Active <before> → <after> · Ledger <before> → <after>.
   ```
