---
disable-model-invocation: true
---

# `/worklog` — ADD and USER-ADD

Read at an `add` or `user-add` invocation, including one the CLAUDE.md auto-detect chain routes here. Entry formats and call shorthands: `worklog_formats.md`. Mirror-patch format: `worklog_mirror.md`.

## Cross-cutting rules for both adds

- **De-duplication:** before any add, scan the in-context mirror for a near-duplicate title in the same domain; surface the existing item unless the user confirms it's distinct. The mirror omits Future Scope — for Future-Scope-shaped candidates, read `## Future Scope` too.
- **Future Scope routing:** a Future-Scope-bound `add` proposal MUST say "Future Scope" in the confirm prompt, so the user knows the item is excluded from the mirror: `Add to Worklog Future Scope: <title> — <domain> · <class> · scope <n>?`. Override with `y, active not future`.
- **Auto-detect chain:** CLAUDE.md's detection rule fires propose-and-confirm; on `y` it invokes `/worklog add <inferred-text>` or `/worklog user-add <inferred-text>` to perform the write. The recipe lives here, not in CLAUDE.md or the skill. Strong-trigger phrases route to Future Scope; high-confidence art/feel/brainstorm phrases to User-Tasks; regular deferral phrases to Active (`worklog_reference` *Trigger Catalog*).

## Operation: ADD

1. **De-dup check.** Apply the cross-cutting rule above to `worklog-titles.md`. Proceed if the user confirms it's distinct.

2. **Required fields.**
   - Title: short, imperative, bold.
   - Class: `fix` / `debug` / `feature` / `refactor` / `test` / `docs` / `chore` / `design` (heuristics: `worklog_reference`).
   - Scope: 1–4 (scope table: `worklog_reference`).
   - Domain: from the canonical list; ask if ambiguous.
   - Context: one line on why this matters / what the deferred concern is.
   - Date: today.

3. **Optional fields.**
   - Where: file path or scene reference where the work lands.
   - Source: session name, PR ref, or commit hash that triggered the deferral.
   - **When:** phrasing implying a prerequisite or phase gate ("once X is done", "after Y ships", "not until Z") → propose `When: after <condition>` or `When: future` and confirm. No timing signal → omit (ready by default).
   - **Plan doc: REQUIRED if scope == 4.** Wikilink `[[Doc Title]]`; the doc must exist in `TODO/` and be listed in `## Linked Docs`. No doc yet → prompt the user to create one, or downgrade to scope 3 if it fits inline.

4. **Inferred-values confirmation** (auto-detect, or sparse `add` text): `Add to Worklog: <title> — <domain> · <class> · scope <n>?`. `y` accepts; overrides land inline (`y, scope 3` / `y, refactor not feature` / `y, domain spell`).

5. **Decide section.** Future Scope strong-trigger phrase (`worklog_reference` *Trigger Catalog*) OR explicit `When: future` → step **5b**. A `When: after <condition>` clause → `## Ledger > ### Domain`, step 6 (it is not ready, so it never occupies a ready-pool slot). Otherwise → `## Active > ### Domain`, step 6 — a fresh item is the newest by definition, so it belongs in the pool.

   Landing above 30 is fine: report `Active is now <N> (ceiling 30) — /worklog triage rebalances.` and write anyway. **Never demote to the Ledger to hold the ceiling** — an unseen demotion is a lost item.

5b. **(Future Scope path) Append an `FSLINE` under `## Future Scope > ### Domain`,** inside the `> [!example]- Future Scope (N items)` collapsed callout (every line within is `> `-prefixed).

   **Sub-heading exists** — `SR(Worklog.md, search: "> ### <Domain Long-Form>\n", replace: "> ### <Domain Long-Form>\n<FSLINE>\n")`.
   **Future Scope exists, sub-heading missing:** insert sub-heading + `FSLINE` just before the callout's close (the `> ` line immediately preceding `## Linked Docs`).
   **`## Future Scope` missing:** create it between `## Active` and `## Linked Docs` —
   ```
   search:  "## Linked Docs"
   replace: "## Future Scope\n\n> [!example]- Future Scope (1 item)\n> <!-- Distant-horizon items (When: future). One-liner format only — excluded from `.claude/worklog-titles.md` mirror. -->\n> ### <Domain Long-Form>\n<FSLINE>\n\n## Linked Docs"
   ```

   Then update the item-count: re-read the Future Scope block, count `> - ` lines, `SR` the `(N items)` substring on the callout-header line. Skip when the section was just created (already `(1 item)`).

   Skip steps 6–7; jump to step 8, then step 9 (which excludes this item by design).

6. **(Active path) Append a `BLOCK` under the right `### Domain` heading,** at the **top** of that section, immediately after the heading line.

   **Domain section exists** — `SR(Worklog.md, search: "### <Domain Long-Form>\n", replace: "### <Domain Long-Form>\n<BLOCK>\n")`.
   **Domain section missing:** insert it at the END of `## Active` — anchor on `## Future Scope` when that section exists, else on `## Linked Docs`. Anchoring on `## Linked Docs` while a `## Future Scope` sits between them lands the new domain OUTSIDE `## Active`, where the mirror filter and every Active-scanning op miss it.
   ```
   search:  "## Future Scope"   // or "## Linked Docs" when no Future Scope section exists
   replace: "### <Domain Long-Form>\n<BLOCK>\n\n## Future Scope"
   ```

7. **(Active path, scope-4 only)** If the named Plan doc isn't already in `## Linked Docs`, add it there in the same domain subsection. Skip if 5b was taken — Future Scope items carry no Plan docs.

8. **`FM(Worklog.md)`.**

9. **Patch the mirror (incremental).** Insert the composed line into `.claude/worklog-titles.md` under the `### <category>` heading within the tier's `## Active` or `## Ledger` supersection (per step 5's routing; append a new `### <category>` heading in that supersection at the end if absent), then `Write` back. Format per `worklog_mirror.md`. Do NOT re-read `Worklog.md`. Set `Last synced:` to today. Future Scope adds took 5b and carry no mirror line — skip.

10. **Confirm:** `Added.` — or `Added to Future Scope.` so the user knows the item is parked.

### Invocation contexts for ADD

| Caller | Confirm-first? | Notes |
|--------|---------------|-------|
| User types `/worklog add <text>` | No (already explicit) | Ask only for missing fields (class, scope, domain, context). |
| CLAUDE.md auto-detect + user says `y` | Already done by CLAUDE.md | Execute the recipe; honor inline overrides (`y, scope 3`). |
| `/worklog sweep` proposes an add + `y` | Already done by sweep | Same as above. |

## Operation: USER-ADD

Append a `UTLINE` for items needing user judgment Claude cannot supply. Append-only — never modifies or deletes existing entries.

1. **De-dup search.** Active-operation carve-out to the never-read-passively rule: the result lands in the tool response and informs the confirm prompt.
   ```
   Grep(pattern="<title keywords>", path="DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md")
   ```
   On near-match: `User-Tasks already has: <existing entry>. Add as distinct (y/n)?`. On `n`, abort.

2. **Compose the `UTLINE`.**
   - **Date:** today (when Claude flagged it, not the user's eventual action date).
   - **Title:** short, imperative, ≤60 chars, no bold.
   - **Context:** ≤80 chars on why this needs user attention.
   - **Domain:** from `worklog_reference` *Canonical domain list*.

3. **Inferred-values confirmation** (auto-detect): `Route to User-Tasks: <title> — <domain>?`. `y` accepts; `y, domain spell` overrides the domain; `y, active not user-tasks` cancels this add and reroutes to `/worklog add`.

4. **Append under the right `## <Domain>` heading,** at the **top** of the section, immediately after the heading line.

   **Domain section exists** — `SR(User-Tasks.md, search: "## <Domain Long-Form>\n", replace: "## <Domain Long-Form>\n<UTLINE>\n")`.
   **Domain section does NOT exist** — `APPEND(User-Tasks.md, "\n## <Domain Long-Form>\n<UTLINE>\n")`.

5. **`FM(User-Tasks.md)`** — NOT `Worklog.md`. **No mirror rewrite**: User-Tasks is excluded from the mirror by design.

6. **Confirm:** `Routed to User-Tasks.`

### Invocation contexts for USER-ADD

| Caller | Confirm-first? | Notes |
|--------|---------------|-------|
| User types `/worklog user-add <text>` | No (explicit) | Skip propose-and-confirm; ask only for missing domain + context. |
| CLAUDE.md auto-detect (User-Tasks route) + `y` | Already done by CLAUDE.md | Execute with inferred title + domain. Honor inline overrides (`y, domain spell` / `y, active not user-tasks`). |
| `/worklog triage` → `to-user-tasks` disposition | Already done by the triage walk | Execute from the Active item's title + Context + domain. Skip Step 1 — the walker already exposed existing entries. |
