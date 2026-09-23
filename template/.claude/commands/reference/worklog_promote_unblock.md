---
disable-model-invocation: true
---

# `/worklog` — PROMOTE and UNBLOCK

Entry formats: `worklog_formats.md`. Mirror-patch format: `worklog_mirror.md`.

## Operation: PROMOTE

Move a `When: future` item from `## Future Scope` back to `## Active`. `$ARGUMENTS` is the title or fuzzy-search string.

### Step 1 — Identify the Future Scope item

Read `## Future Scope` from Obsidian and match the argument against the `FSLINE` titles. Multiple matches → list and ask. No matches → suggest `/worklog show all`.

### Step 2 — Reconstruct a `BLOCK` from the `FSLINE`

Title line gains ` (promoted from Future Scope YYYY-MM-DD-today)`; `Context:` takes the parenthetical's text; `Source: promoted from Future Scope on YYYY-MM-DD`; no other sub-bullets. Preserve the original `added` date — promotion is a state change, not a new add.

### Step 3 — Apply the move

**Step A** — `SR(Worklog.md, search: "<the verbatim FSLINE>\n", replace: "")`.
**Step B** — insert the reconstructed `BLOCK` at the top of the matching `## Active > ### Domain`, same anchor pattern as ADD step 6 (`worklog_add.md`) (immediately after `### <Domain Long-Form>\n`).

### Step 4 — Cleanup

- Future Scope `### <Domain>` now empty (no `> - ` lines) → remove the sub-heading.
- `## Future Scope` entirely empty → remove the whole section including its `> [!example]-` callout wrapper.
- Update the callout's `(N items)` count.

### Step 5 — Bump frontmatter + patch mirror

Per ADD steps 8–9 (`worklog_add.md`): `FM(Worklog.md)`, then **incrementally add** the promoted item's reconstructed line to the mirror (it is now ready). Do NOT re-read the source.

### Step 6 — Confirm

```
Promoted: <Title> — now in Active under <Domain>.
```

### Edge cases for PROMOTE

- **Promotion then completion in one session:** run PROMOTE then COMPLETE in sequence; the intermediate state keeps the history readable.
- **User rejects a sweep-proposed promotion:** leave the item unchanged; the next sweep re-evaluates it.
- **Title collision with an existing Active item:** ask the user; the work may have been logged twice.

## Operation: UNBLOCK

Promotes one or more `after <condition>` items to ready by stripping their `When:` sub-bullet. `$ARGUMENTS` is the condition text to match.

### Step 1 — Find matching items

Read `## Ledger` — every `When: after` item lives there, so an Active-only scan finds nothing. Find all `[ ]` items whose `When: after <condition>` fuzzy-matches the argument — case-insensitive substring is sufficient. Show matches before writing:
```
Unblocking items matching "<condition>":
  1. <title> (<domain>) — When: after <condition>
  2. <title> (<domain>) — When: after <condition>

Proceed? (y/n)
```
No matches: `No items found with When: after <condition>.` — check for typos, suggest alternatives if any `after` items exist.

### Step 2 — Strip the When: line for each matched item

`SR(Worklog.md, search: "  - When: after <condition>\n", replace: "")`. Use the exact `When:` line text from the item, not the argument verbatim — fuzzy match may have differed.

### Step 3 — Bump frontmatter + patch mirror

`FM(Worklog.md)`, then **incrementally** strip the ` [after: <condition>]` suffix from the matched lines in `.claude/worklog-titles.md`. Do NOT re-read the source.

### Step 4 — Confirm

```
Unblocked <n> item(s). They are now ready and will appear in /worklog drive batches.
```

### Edge cases for UNBLOCK

- **`future` items:** `unblock` does not match `When: future` — no condition to match. Re-open one via `/worklog add` without a `When:` line, or edit Obsidian directly and re-sync the mirror.
- **Multiple conditions partially matching:** show all matches and confirm each individually; never bulk-strip on a partial fuzzy match.
- **Item already has no `When:`:** skip silently.
