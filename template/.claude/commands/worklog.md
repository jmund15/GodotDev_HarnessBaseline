---
description: Run any worklog operation — drive logged items, add, complete, promote, unblock, triage, sweep, show.
---

The single executor for all worklog operations. Source of truth: `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` (Obsidian); title mirror `.claude/worklog-titles.md`. This file is the **operations playbook**.

**Two open tiers, both mirrored, both reachable.** `## Active` is the ready pool — hot-context plus newest, **30-item ceiling**. `## Ledger` holds unscheduled specified debt and every `When: after` item, in identical block shape. Three things read Active alone: the capacity check, DRIVE survey batching, DRIVE budget scoring. Everything else — `complete`, `unblock`, `promote`, named-item DRIVE, TRIAGE — scans both. TRIAGE Step 6 rebalances; an add never demotes. Classification + scope rules, domain list, trigger catalog, completion signals, which-operation litmus: `worklog_reference` skill. DRIVE + TRIAGE recipes: `agents/worklog_drive_triage.md` (Read on demand).

**Call shorthands** used below — expand literally when invoking. `<Doc>` is `Worklog.md` / `Worklog-Archive.md` / `User-Tasks.md`; `P` = `DevProjects/{{PROJECT_NAME}}/Claude/TODO/<Doc>`.
- `SR(<Doc>, search, replace)` = `mcp__obsidian__obsidian_search_replace(targetType="filePath", targetIdentifier=P, replacements=[{search: ..., replace: ...}], replaceAll: false)`
- `READ(<Doc>)` = `mcp__obsidian__obsidian_read_note(filePath=P)`
- `FM(<Doc>)` = `mcp__obsidian__obsidian_manage_frontmatter(filePath=P, operation="set", key="last_updated", value="YYYY-MM-DD")`
- `APPEND(<Doc>, content)` = `mcp__obsidian__obsidian_update_note(targetType="filePath", targetIdentifier=P, modificationType="wholeFile", wholeFileMode="append", content=...)`

**Entry formats** referenced by name below. Omit an inapplicable sub-bullet's line entirely — never leave a placeholder.
- `BLOCK` — an Active item:
  ```
  - [ ] **<Title>** — `<class>` · scope `<n>` · added YYYY-MM-DD
    - Context: <one-line>
    - Where: <path-if-any>
    - Source: <ref-if-any>
    - When: after <condition> | future        (omit if ready)
    - Plan doc: [[<Doc Title>]]               (scope 4 only)
    - Quick-win: flagged YYYY-MM-DD           (triage only)
  ```
- `FSLINE` — a Future Scope one-liner, `> `-prefixed, no sub-bullets; the parenthetical replaces them and stays under 80 chars: ``> - `<class>` · scope `<n>` · <Title> (added YYYY-MM-DD; <one-line context>)``
- `XLINE` — an archive completion line: ``- [x] `<class>` · scope `<n>` · <Title> (completed YYYY-MM-DD, <ref>)``
- `UTLINE` — a User-Tasks entry: `- YYYY-MM-DD — <Title> — <one-line context>`

## Forms

Argument: `$ARGUMENTS`

| Form | Operation (full recipe in the same-named section below) |
|------|-----------|
| (no args) or `show` | Print Active (the ready pool) grouped by domain, from the mirror unless full content is needed. Footer-count `## Ledger`; **exclude `## Future Scope`**. |
| `show ledger` | Print `## Ledger` grouped by domain, same format. Opt-in — `show` prints its count, never its contents. |
| `show all` | `show` plus the full `## Ledger` listing plus the `## Future Scope` callout contents, each grouped by domain. |
| `add <free text>` | ADD, bypassing auto-detect's confirm. `When: future` or a strong-trigger phrase (`worklog_reference` *Trigger Catalog*) routes to Future Scope as an `FSLINE`. |
| `complete <id-or-search>` | COMPLETE the matched Active item — its `BLOCK` leaves `Worklog.md`, an `XLINE` lands in `Worklog-Archive.md`. Multiple matches → ask. |
| `sweep` | Three confirmation-driven passes: add-sweep, completion-sweep, promotion-sweep. |
| `triage` | Bulk cleanup walk: score per-item dispositions, walk confirmation-driven, execute inline, rewrite mirror at end. Use when Active > 30 or backlog pressure builds. |
| `drive [<N> \| scope:N \| items:N \| <names>] [--plan-only]` | Agentic prioritization-and-execution to commits. Three modes, first match wins after `--plan-only` is stripped: **no argument** → *survey*; **budget-shaped** (`<N>` ≡ `scope:N`, combinable with `items:N`, more-restrictive binds) → score and fill the target; **anything else** → *named-items*. Only ready items (no `When:` sub-bullet) are selectable. Scope-4 is never driven — route to `/design_drive` or `/part_drive`. `--plan-only` stops at the drafted plan body. Mode mechanics + scoring: the DRIVE recipe file. |
| `unblock <condition>` | Strip `When: after <condition>` from all fuzzy-matching Active items; show matches before writing. |
| `promote <title>` | Move a `When: future` item from Future Scope back to Active, reconstructing a full `BLOCK`. |
| `user-add <free text>` | Append a `UTLINE` to `User-Tasks.md` (user-only addressable: art, feel, brainstorms, design audits). De-dup first. Append-only; never touches `Worklog.md` or the mirror. |
| `user-show` | Print `User-Tasks.md` grouped by domain. Opt-in only — never folded into `show` / `show all`. |
| `history [domain]` | Print `Worklog-Archive.md`'s `[x]` items grouped by domain (optional filter). Opt-in only; never loaded passively. |

## Cross-cutting rules

- **Mirror maintenance (incremental — do NOT re-read the source):** `.claude/worklog-titles.md` is not auto-injected — `Read` it first if you haven't this session. After an Active-section write, patch the affected line(s) and `Write` back:
  - **ADD** (Active path) → append the line you just composed.
  - **COMPLETE** / triage-`delete` / triage-`promote` (Active→Future Scope) → remove that item's line.
  - **UNBLOCK** → strip the ` [after: <condition>]` suffix from matched lines.
  - **PROMOTE** (Future Scope→Active) → add the reconstructed line.
  - **TRIAGE** → rebuild once at end-of-walk from the full-Active copy read in its Step 1 (still no fresh re-read).
  - Future Scope adds/removes do NOT touch the mirror.
  Then set `Last synced:` to today's **date only** — never a change narrative; the mirror is always-loaded context. This command is the mirror's only writer.
  File shape: two `## <tier> (<count>)` supersections — `## Active` then `## Ledger` — each holding `### <category>` headings, then `- <class> · <scope> · <title>` lines beneath. The category lives in the heading, never per line. Add a heading when a category first appears; remove it when its last item leaves. Patch the count in the supersection heading on every add/remove.
  **Full regeneration** happens ONLY in SHOW's full-read path — the drift-correction escape hatch when mirror and source diverge (e.g. a manual Obsidian edit). Routine add/complete must never trigger it.
  Line-format filter rules (composing or regenerating a line):
  - `[ ]` items in `## Active` → included.
  - `[x]` items live in `Worklog-Archive.md`, never in the mirror.
  - Items in `## Future Scope` → **excluded**.
  - `When: after <condition>` → ` [after: <condition>]` suffix after the title.
  - `Quick-win:` sub-bullet → ` [quick-win]` suffix after the title.
  - Both → suffixes stack, stable order `... [after: <condition>] [quick-win]`.
  - Neither → plain `<category> · <class> · <scope> · <title>`.
- **Frontmatter bump:** every Obsidian write also runs `FM(<the doc written>)`.
- **De-duplication:** before any add, scan the in-context mirror for a near-duplicate title in the same domain; surface the existing item unless the user confirms it's distinct. The mirror omits Future Scope — for Future-Scope-shaped candidates, read `## Future Scope` too.
- **Class + scope are required** on every `BLOCK` and every `FSLINE`. If the `add` text doesn't make them obvious, propose inferred values and confirm before writing.
- **Future Scope routing:** a Future-Scope-bound `add` proposal MUST say "Future Scope" in the confirm prompt, so the user knows the item is excluded from the mirror: `Add to Worklog Future Scope: <title> — <domain> · <class> · scope <n>?`. Override with `y, active not future`.
- **MCP-offline:** `Worklog.md` is a vault file editable with native `Read`/`Edit`/`Write`. The one MCP touchpoint is `FM()` — do that bump with a native `Edit` when the MCP is down.
- **Auto-detect chain:** CLAUDE.md's detection rule fires propose-and-confirm; on `y` it invokes `/worklog add <inferred-text>` or `/worklog user-add <inferred-text>` to perform the write. The recipe lives here, not in CLAUDE.md or the skill. Strong-trigger phrases route to Future Scope; high-confidence art/feel/brainstorm phrases to User-Tasks; regular deferral phrases to Active (`worklog_reference` *Trigger Catalog*).
- **User-Tasks parallel doc:** append-only from Claude's side; never read passively (no mirror, no SessionStart, no sweep/triage/drive scan). Reads only on (a) explicit `user-show`, (b) the de-dup search inside `user-add`, (c) the count footer inside `show` (count only — content stays in the tool result). Worklog.md ops never touch it; the only crossover is triage's `to-user-tasks` disposition.

---

## Cloud fallback (CLAUDE_CODE_REMOTE=true)

Obsidian MCP is unavailable on cloud, so `Worklog.md` cannot be written. Detect with the Bash form `[ "${CLAUDE_CODE_REMOTE:-}" = "true" ]` (this is a `.md` command — there is no `is_cloud()` import).

- **Mutating ops** (`add`, `complete`, `promote`, `unblock`, triage dispositions): do NOT touch Obsidian or the mirror. Append the op to the tracked queue `.claude/worklog-pending.md`, one header per session (new header only when the session changes), then stop:
  ```
  ## <ISO timestamp> cloud session <id>
  - ADD active: <title> (<domain> · <class> · scope <n>)
  - COMPLETE: <title>
  - PROMOTE: <title>
  ```
- **Read ops:** `show` uses the local mirror. Ops needing the full Obsidian doc (`show all` Future Scope, `sweep`, `drive`) print `Obsidian unavailable on cloud — defer to a local session.` and stop.
- **Commit** the pending file from cloud (it is tracked); the cloud→local handoff crosses machines via git.

### Replay (local session)

Any `/worklog` invocation first checks `.claude/worklog-pending.md`. If its body (below the DO-NOT-HAND-EDIT header) has un-struck `- ` lines:

1. Prompt: `Replay N pending cloud-session entries to Obsidian?` (N = un-struck `- ` lines).
2. On confirm, apply each entry as the matching native op (ADD→add, COMPLETE→complete, PROMOTE→promote).
3. **Conflict policy = skip-and-audit-trail:** an entry no longer matching Obsidian state (`COMPLETE: X` already archived; `ADD` of an existing title) is rewritten struck-through (`- ~~<entry>~~ (skipped: <reason>)`) and skipped — never hard-fail the replay.
4. After applying, truncate the body, keep the header.
5. **Idempotency:** a later run seeing a header-only body no-ops without re-prompting.

The SessionStart hook surfaces `Cloud worklog: N pending` on local sessions when un-struck entries exist.

---

## Operation: SHOW

**Cheap path (preferred):** `Read .claude/worklog-titles.md` (SessionStart does not inject it) and print Active grouped by domain — `## <category>` headings already group it. The mirror omits Future Scope by design.

**Capacity check (preface every show output):** count `[ ]` lines in `## Active` only — the Ledger is uncapped by design and counting it would restore the alarm's old meaninglessness. If > 30, prepend:
```
⚠ Active has <N> items (ceiling: 30). Run `/worklog triage` to rebalance against `## Ledger`.
```
At ≤ 30, omit it entirely. Fires on `show` / `show ledger` / `show all` only, never during `add` / `complete` / `drive` / `triage`. Soft alarm — never blocking.

**Full read** (user asks for context, dates, or sub-bullets): `READ(Worklog.md)`. Group by domain and list the `[ ]` items; `[x]` items live in the archive (`/worklog history`). Default view is title + class + scope + date — don't dump Context/Where/Source unless asked.

**Mirror drift-resync (the full-regen escape hatch).** After that full read, regenerate `.claude/worklog-titles.md` from the `## Active > [ ]` items per *Cross-cutting rules > Mirror maintenance* and `Write` it. This is the only place the mirror is rebuilt from scratch.

**`show all` form** — also reads Obsidian (the mirror omits Future Scope), then prints after the Active listing:
```
## Future Scope (parked, excluded from agent-facing mirror)
### <Domain>
- `<class>` · scope `<n>` · <title> (added <date>; <one-line why-deferred>)
```

On bare `show`, if a Future Scope item looks relevant to current session work (recent commits touch its area), nudge in one line: "(N items in Future Scope; run `/worklog show all` if you want to review them.)" — no auto-load.

**User-Tasks count footer** — ending every `show` output (bare and `show all`), read `User-Tasks.md` once and count dated entries (`^- \d{4}-\d{2}-\d{2} — `). If > 0, append `(N items in User-Tasks — /worklog user-show to review)`; if 0, omit. This is the only passive read of `User-Tasks.md` outside `user-show`. File absent → skip silently.

## Operation: USER-SHOW

Opt-in read of `User-Tasks.md`; prints content where `show` emits only the count footer.

1. **`READ(User-Tasks.md)`.** File absent → print `User-Tasks.md does not exist yet — no items have been routed to it. Use /worklog user-add or accept an auto-detect proposal to start populating it.` and stop.
2. **Print grouped by domain.** Per `## <Domain>` section, print the heading and its entries verbatim, preserving date order (newest at top). Do not reformat, sort across domains, or strip dates.
3. **Optional age callout.** Any entries older than 90 days → append `(N entries older than 90 days — consider whether they're still relevant, or edit/remove directly in Obsidian.)`. Soft nudge; no agent action follows.

## Operation: HISTORY

Opt-in read of `Worklog-Archive.md`. Never loaded passively (no mirror, no SessionStart, no sweep/triage/drive scan). `$ARGUMENTS` is an optional domain filter.

1. **`READ(Worklog-Archive.md)`.** File absent → print `Worklog-Archive.md does not exist yet — no items have been completed into it.` and stop.
2. **Print grouped by domain.** Per `## <Domain>` section, print the heading and its `[x]` lines verbatim (newest at the bottom — append order). A domain argument fuzzy-matches the canonical list; matching nothing → list the available headings and ask.
3. **Read-only.** COMPLETE is the only writer. The archive grows unbounded; pruning is the user's, directly in Obsidian (git / Obsidian version history is the safety net). No agent-driven compaction exists.

## Operation: ADD

1. **De-dup check.** Scan `worklog-titles.md` for a near-duplicate title in the same domain; surface the existing item rather than asking to add. Proceed if the user confirms it's distinct.

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

9. **Patch the mirror (incremental).** Insert the composed line into `.claude/worklog-titles.md` under its `## <category>` heading (append a new heading at the end if absent), then `Write` back. Format per *Cross-cutting rules > Mirror maintenance*. Do NOT re-read `Worklog.md`. Set `Last synced:` to today. Future Scope adds took 5b and carry no mirror line — skip.

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
   mcp__obsidian__obsidian_global_search(
     query="<title keywords>",
     searchInPath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md"
   )
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

## Operation: COMPLETE

Cross-doc move: the `BLOCK` leaves `Worklog.md` and an `XLINE` lands in `Worklog-Archive.md` under its domain.

1. **Identify the entry.** Match by title or fuzzy search against `[ ]` items in `## Active` **and** `## Ledger`. Multiple matches → list and ask.

2. **Compose an `XLINE`.**
   - `<class>` and `<n>` come from the original `BLOCK` — don't re-classify on completion unless asked.
   - `<ref>` is optional but recommended: commit hash (`abc1234`), PR (`#62`), or session name. Prefer the commit hash for shipped code.
   - Title keeps its wording, unbolded.

3. **Append the `XLINE` to `Worklog-Archive.md` FIRST** (before deleting from Active — atomicity note in step 4). Newest completion goes at the **bottom** of its domain section.

   **Archive doesn't exist:** create it — frontmatter (`title: Worklog Archive`, `status: archive`, `last_updated: <today>`), an `# Worklog Archive` heading, the opacity note (`> Completed worklog items ... never mirrored, never loaded at SessionStart, never scanned by sweep/triage/drive. Read only via /worklog history.`), then a `## <Domain>` section holding the line.
   **Archive has the item's `## <Domain>` section** — `SR(Worklog-Archive.md, search: "<lastline-of-domain-section>\n", replace: "<lastline-of-domain-section>\n<XLINE>\n")`, i.e. just before the next `## ` heading or at end of file.
   **Archive lacks that section** — `APPEND(Worklog-Archive.md, ...)` with a new `## <Domain>` section plus the line.

4. **Delete the `BLOCK` from `Worklog.md`** — `SR(Worklog.md, search: "<the verbatim BLOCK>", replace: "")`, matching exactly the sub-bullets present in this block.

   **Line-ending note:** both docs are LF — use `\n`. `obsidian_search_replace` matches literally, so a separator mismatch reports zero replacements without erroring: always verify the reported count. Capture the verbatim block from a targeted read of its domain section, never from memory; on a 0-hit retry with `\r\n` (re-saved CRLF) or set `flexibleWhitespace: true`. See `obsidian_conventions`.

   **Atomicity:** if step 3 failed, do NOT execute this delete — surface the error and stop. A duplicate archive line from a retried step 3 is harmless; the archive is opaque.

5. **Remove the now-empty domain heading.** If the delete left the `### Domain` section with no remaining `- [ ]` items, remove the heading line too. Other `[ ]` items remain → leave it.

6. **`FM()` on BOTH `Worklog.md` and `Worklog-Archive.md`.**

7. **Patch the mirror (incremental).** Remove the completed item's line from `.claude/worklog-titles.md` — and its `## <category>` heading if that was the category's last item — then `Write` back. Do NOT re-read `Worklog.md`. Set `Last synced:` to today.

8. **Confirm:** `Marked complete.`

9. **Pair-emit to tackle history (if applicable).** Read `.claude/worklog-tackle-history.jsonl` (always exists, may be empty). If a `tackle` event line has `title` matching the completed item AND no later `completion` event for that title exists, append via `printf '...\n' >> .claude/worklog-tackle-history.jsonl`:
   ```json
   {"event": "completion", "date": "YYYY-MM-DD", "title": "<title verbatim>"}
   ```
   `title` MUST match the tackle event verbatim — pairing is exact-string, not fuzzy. No matching `tackle` → skip silently. This step is what makes the DRIVE anti-thrash penalty self-clearing (`agents/worklog_drive_triage.md`).

### Edge cases for COMPLETE

- **Un-complete (re-open):** treat as a manual `add` — a fresh `BLOCK` with the original title and `Source: re-opened from <YYYY-MM-DD> completion`. There is no `uncomplete` form.
- **Item was scope 4 with a Plan doc:** the `## Linked Docs` entry stays; the doc remains a useful artifact.
- **A `debug` that resolved into a `fix`:** the original class is preserved on the `XLINE`. A follow-up `fix` item is a separate `add`.

## Operation: SWEEP

Three passes over the recent session, each confirmation-driven — never auto-apply. They are the deterministic backstop for the immediate-add, immediate-complete, and condition-ripening rules when inline signals were missed. Used by `/session_end` Phase 6.

**Add-sweep:** scan the transcript for missed trigger phrases (`worklog_reference` — both regular-deferral and Future-Scope catalogs). Propose `Add to Worklog: <title> — <domain> · <class> · scope <n>?` (or `Add to Worklog Future Scope: ...` on a strong-trigger phrase), citing the turn it appeared in. On `y` (or `y, <override>`), run ADD.

**Completion-sweep:** read Active and diff its `[ ]` items against `git status` / `git log --since="session start"` and the session's tool calls. For each plausibly resolved item propose `Mark complete: <title> (<commit-ref>)?`. On `y`, run COMPLETE.

**Promotion-sweep (Future Scope ripening):** read `## Future Scope`. Scan each `FSLINE`'s title + parenthetical against `git log --since="session start"` (widen to `--since="2 weeks ago"` on the first session of the week), `git status --short`, and the session's tool-call topics (file paths, test names, system terms). On overlap propose `Promote from Future Scope: <title> — looks ripened (matched: "<short evidence>")?`. On `y`, run PROMOTE. On `n` / `n, still parked`, skip silently.

Matcher heuristics (better to miss than to spam):
- Require ≥2 distinct token matches OR one specific identifier match (file path, function name, version number, PR number).
- Skip generic words ("test", "audit", "review") as match anchors.
- Cap at 5 proposals per sweep; beyond that, propose the top 5 by match-strength and surface `(N more Future Scope candidates — run /worklog show all to review)`.

## Operation: TRIAGE

Full recipe: [`agents/worklog_drive_triage.md`](agents/worklog_drive_triage.md) — Read that file on any `triage` invocation and execute from its steps (disposition scoring table, confirmation walk, caps, edge cases). Do not run triage from memory of this stub.

## Operation: PROMOTE

Move a `When: future` item from `## Future Scope` back to `## Active`. `$ARGUMENTS` is the title or fuzzy-search string.

### Step 1 — Identify the Future Scope item

Read `## Future Scope` from Obsidian and match the argument against the `FSLINE` titles. Multiple matches → list and ask. No matches → suggest `/worklog show all`.

### Step 2 — Reconstruct a `BLOCK` from the `FSLINE`

Title line gains ` (promoted from Future Scope YYYY-MM-DD-today)`; `Context:` takes the parenthetical's text; `Source: promoted from Future Scope on YYYY-MM-DD`; no other sub-bullets. Preserve the original `added` date — promotion is a state change, not a new add.

### Step 3 — Apply the move

**Step A** — `SR(Worklog.md, search: "<the verbatim FSLINE>\n", replace: "")`.
**Step B** — insert the reconstructed `BLOCK` at the top of the matching `## Active > ### Domain`, same anchor pattern as ADD step 6 (immediately after `### <Domain Long-Form>\n`).

### Step 4 — Cleanup

- Future Scope `### <Domain>` now empty (no `> - ` lines) → remove the sub-heading.
- `## Future Scope` entirely empty → remove the whole section including its `> [!example]-` callout wrapper.
- Update the callout's `(N items)` count.

### Step 5 — Bump frontmatter + patch mirror

Per ADD steps 8–9: `FM(Worklog.md)`, then **incrementally add** the promoted item's reconstructed line to the mirror (it is now ready). Do NOT re-read the source.

### Step 6 — Confirm

```
Promoted: <Title> — now in Active under <Domain>.
```

### Edge cases for PROMOTE

- **Promotion then completion in one session:** run PROMOTE then COMPLETE in sequence; the intermediate state keeps the history readable.
- **User rejects a sweep-proposed promotion:** mark nothing. Do NOT add a `When: not yet` annotation — the next sweep re-evaluates.
- **Title collision with an existing Active item:** ask the user; the work may have been logged twice.

## Operation: DRIVE

Full recipe: [`agents/worklog_drive_triage.md`](agents/worklog_drive_triage.md) — Read that file on any `drive` invocation and execute from its steps (mode dispatch, scoring engine, execute phase + re-scope valve, tackle-history JSONL schema, edge cases). Do not drive from memory of this stub.

Execution is the default: the invocation is the execution directive, and Mode D (choose-among) is the one selection gate. `--plan-only` stops at the drafted plan body.

---

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
