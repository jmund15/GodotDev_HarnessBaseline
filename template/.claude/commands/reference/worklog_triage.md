---
disable-model-invocation: true
---

# `/worklog` — TRIAGE recipe

`commands/worklog.md` owns the Forms table, the tier model, the cross-cutting rules and the cloud fallback; entry formats live in `worklog_formats.md` and mirror-patch format in `worklog_mirror.md`.

## Operation: TRIAGE

Bulk cleanup. Walks Active items proposing per-item dispositions; confirmation-driven, never auto-applies. Use when Active > 30 or backlog pressure builds.

### Step 1 — Read `## Active` AND `## Ledger`

`READ(Worklog.md)`. Full content (Context, Where, Source, dates, class, scope) is needed to score dispositions; the mirror is insufficient. Triage is the one operation that rebalances the two open tiers, so it scores **both** — an item's tier is an input to Step 3, never a filter on it. `When: future` items live in Future Scope and are out of scope.

### Step 2 — Gather context signals (in parallel)

- `git log --oneline --since="3 weeks ago"` — recent commit messages.
- `git status --short` — uncommitted changes.
- Today's date, for age math against each item's `added` field.

### Step 3 — Score each item's recommended disposition

Derive ONE disposition per `[ ]` item — priority order, first match wins:

| Disposition | Trigger | Action |
|---|---|---|
| **complete** | `Where:` paths or `Source:` text overlap recent commit messages or `git status` | run COMPLETE with the matched commit hash |
| **do-now** | scope-1 + class in `{fix, refactor, docs, chore}` + mechanical phrasing (rename / sweep / remove dead reference / single-line edit / one-paragraph add) | execute the work this turn, then COMPLETE |
| **quick-win** | scope-1 + class NOT in `{design, debug}` + has `Where:` OR concrete Context — minor judgment, not pure mechanical | add `Quick-win: flagged YYYY-MM-DD` sub-bullet; item stays in Active with a priority flag for next session |
| **delete** | age > 30 days + no `git log` activity touching `Where:` + Source references something already shipped/superseded | remove the `BLOCK` entirely (no archive — git history is the record) |
| **promote** | age > 14 days + no `git log` activity touching `Where:` + no `When: after` clause | move `## Active` → `## Future Scope` as an `FSLINE` (inverse of `/worklog promote`) |
| **to-user-tasks** | fundamentally user-only addressable: `feature` items whose output is production art (the `worklog_reference` User-Tasks triggers name the art signals); `design`-class items whose output is user taste / vision, not technical alternatives; items whose `Context:` implies user judgment (playtest tuning, art preferences, lore decisions). High-confidence triggers only — when uncertain, prefer `skip`. | migrate to `User-Tasks.md` via USER-ADD (`worklog_add.md`), then delete the Active block |
| **skip** | default — no strong signal | no-op, item stays as-is |

**Caps for proposal generation** (prevents triage-fatigue): `do-now` max 5 per session; `delete` / `promote` / `to-user-tasks` max 5 each; `complete` / `quick-win` / `skip` uncapped. Over a cap, take the highest-confidence per category by signal strength.

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
1. Read the item's `Where:` files (`read_files` if 3+; else `Read`).
2. Make the change. Mechanical class only — if the work expands (multi-decision, or multi-file beyond the `Where:` list), abort do-now and offer fallback dispositions inline (`[c]omplete-after-manual / [f]lag for next session / [s]kip`).
3. Verify by change class — the SSOT for tier-1 verification:
   - A change class the project gates → its gate, MANDATORY (`change_control` §Gate cadence names each class's gate).
   - Doc-only / `.md` → no verification needed.
4. Run COMPLETE with today's date as ref, or the commit hash if a commit lands this turn.
5. Resume the walk on the next item.

The walk pauses while you do the work — never batch do-now items to the end; inline execution lets the user see results before deciding the next item.

#### `flag` (quick-win)
`SR(Worklog.md, search: "  - Context: <verbatim context line>\n", replace: "  - Context: <verbatim context line>\n  - Quick-win: flagged YYYY-MM-DD\n")` — the sub-bullet lands immediately after `Context:`. Item stays in Active; Step 6's mirror rewrite appends the `[quick-win]` suffix to its line. DRIVE weights flagged items +3 in scoring — survey batching and budget fill alike.

#### `promote` (Active → Future Scope)
The only Active → Future Scope path; no top-level `/worklog defer` op exists. **Step A** — delete the `BLOCK` from `## Active`, same pattern as COMPLETE Step 4 (`worklog_complete.md`) (match only the sub-bullets present; mind the LF/CRLF trap). **Step B** — append to `## Future Scope > ### Domain` an `FSLINE` extended with `; promoted from Active YYYY-MM-DD-today`, reusing ADD step 5b's insertion logic (`worklog_add.md`) (create `### Domain`, or the whole `## Future Scope` section between `## Active` and `## Linked Docs`, if absent; update the callout's `(N items)` count).

#### `to-user-tasks` (Active → User-Tasks parallel doc)

For production art, feel-tuning, brainstorms whose output is user taste, cross-doc vision audits. **Monodirectional** unlike `promote`: `User-Tasks.md` is opaque to every future agent pass, so the item leaves Claude's awareness entirely. Confirm twice when the class is anything but `design` or `feature` — `refactor`/`fix`/`test`/`chore` items have technical outputs Claude can produce.

**Step A** — derive the `UTLINE` from the `BLOCK`: title unchanged minus the bold; date = today (re-clocking marks the migration moment, not the original log date); Context condensed to ≤80 chars; same domain.
**Step B** — run USER-ADD (`worklog_add.md`) with those fields, skipping its Step 1 de-dup — the walker already showed existing entries.
**Step C** — delete the `BLOCK` from `## Active`, same precise multi-line `SR` as `promote` Step A.
**Step D** — atomicity: if Step B failed (MCP error on the User-Tasks write), do NOT execute Step C. Leave the Active block, surface the error, offer fallbacks (`[s]kip` / `[p]romote`). Better a stuck Active item than a lost migration.

Frontmatter bump + mirror rewrite happen in Step 6; the migrated item drops from the mirror.

#### `delete`
Remove the `BLOCK` entirely. No archive — git history is the record. Scope > 1 confirms twice: `Confirm DELETE (no archive): <title>? [yes/no]`. Scope 1: the `x` keypress suffices.

#### `skip`
No-op. Move to the next item.

#### `quit`
Stop the walk. Remaining items count as `skip`. Proceed to Step 6.

### Step 6 — End-of-walk wrap-up

1. **Rebalance the two open tiers.** Recompute the ready pool over every surviving `[ ]` item in `## Active` + `## Ledger`: hot-context first (a `Where:` file is dirty in `git status`), then newest-added, stopping at **30**. Scope-4 and `When: after` items are never eligible. Move blocks whole in both directions — a Ledger item that went hot is promoted, a stale Active item demoted. Report the net move (`Rebalanced: +N to Active, −M to Ledger; pool cutoff age <D>d`); a zero-move rebalance prints nothing.
2. `FM(Worklog.md)`.
3. Rebuild the mirror from the copy read in **Step 1**, applying the walk's net changes AND step 1's rebalance (`[quick-win]` suffixes on flagged items; drop deleted / promoted / completed / migrated lines; patch both supersection counts). Do NOT re-read `Worklog.md`.
4. Print:
```
Triage complete.
  Applied: <n> complete, <m> do-now (executed), <k> quick-win flagged, <p> promoted to Future Scope, <q> deleted.
  Skipped: <s>. Unaddressed (quit early): <r>.
  Active <before> → <after> (ceiling 30) · Ledger <before> → <after>.
```
The rebalance holds Active at 30 by construction, so an over-ceiling `<after>` means fewer than 30 items are eligible — say so rather than recommending another pass.

### Edge cases for TRIAGE

- **Empty Active section:** print `Active is empty. Nothing to triage.` and stop before Step 3.
- **No items meet any trigger:** print `No actionable triage signals — items mostly need closer review than triage can offer. Try /worklog drive (or /worklog drive 3 to select and execute straight away) instead, where that form is installed.` and stop before Step 4.
- **Mid-walk add request:** complete the add via ADD; warn that subsequent proposals reference pre-add state.
- **Race with parallel writes:** triage reads Active once at start, so another agent's mid-walk write can be double-written or dropped by the Step 6 rewrite. Solo-dev unlikely; if it surfaces, add a re-read before the mirror rewrite.
- **Quick-win flag already present:** don't propose `flag` again; fall through to the next-priority disposition.
- **Do-now misclassified (work blows up):** abort mid-execution, offer fallbacks inline, continue the walk. Never silently log a partially-done state.
- **Gate failure on a do-now change:** stop the walk — a regression is not a triage matter. The item stays `[ ]`; the change reverts or stays uncommitted at the user's choice.
