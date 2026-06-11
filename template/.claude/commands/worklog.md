The single executor for all worklog operations. Source of truth is `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` (Obsidian); local title mirror at `.claude/worklog-titles.md`. This command file is the **operations playbook** (the recipes). For the **decision-time reference** — classification + scope rules, the domain list, the full trigger catalog, completion signals, and the which-operation-to-pick litmus — refer to the `worklog_reference` skill. PLAN + TRIAGE recipes live in `agents/worklog_plan_triage.md` (loaded on demand).

## Forms

Argument: `$ARGUMENTS`

| Form | Operation |
|------|-----------|
| (no args) or `show` | Print the Active section grouped by domain. Cite from `worklog-titles.md` if no full content needed; otherwise read the Obsidian doc. **Excludes `## Future Scope` by default.** |
| `show all` | Same as `show`, but also prints the `## Future Scope` callout contents grouped by domain. Use when reviewing distant-horizon parking lot. |
| `add <free text>` | Bypass auto-detect confirmation. Compose entry from free text, infer class + scope, ask for any missing fields (domain, context), append to Active, rewrite mirror. If user explicitly tags `When: future` (or composes from a strong-trigger phrase — see `worklog_reference` skill), routes to `## Future Scope` as a one-liner instead. |
| `complete <id-or-search>` | Match an Active `[ ]` item by title or fuzzy search; delete its block from `Worklog.md` and append a `[x]` one-liner (completion date + ref) to `Worklog-Archive.md` under its domain; patch mirror. If multiple matches, ask the user to pick. |
| `sweep` | Run three passes: add-sweep (transcript scan for missed deferrals) + completion-sweep (git diff vs `[ ]` items) + **promotion-sweep** (Future Scope items whose conditions have plausibly ripened given recent commits / current state). Each candidate is confirmation-driven. |
| `triage` | Bulk cleanup walk. Reads Active, scores per-item dispositions (complete / do-now / quick-win flag / promote to Future Scope / delete / skip), walks them confirmation-driven, executes confirmed dispositions inline, rewrites mirror at end. Use when `/worklog show` flags overload (Active > 30) or backlog pressure builds. The "do-now" disposition executes scope-1 mechanical work this turn; "quick-win flag" defers small items to next session with a priority bump. |
| `plan [scope:N] [items:N]` | The agentic prioritization op. **No target** → *survey mode*: propose 2-3 prioritized batches with rationale, user picks one. **Target given** → *draft mode*: score per-item, fill the target, draft a plan-mode-ready body, log to tackle-history. Only scores **ready** items (no `When:` sub-bullet); `after`/`future` items are listed in "Waiting" or parked in `## Future Scope`. Quick-win-flagged items get a +3 score boost. |
| `unblock <condition>` | Strip `When: after <condition>` from all matching Active `[ ]` items, promoting them to ready. Fuzzy-matches condition text; shows matches before writing. |
| `promote <title>` | Move a `When: future` item from `## Future Scope` back to `## Active`. Reconstructs a full `[ ]` block (Context preserved from one-liner; Where/Source/When dropped). Mirror image of `unblock` — for the manual or sweep-suggested case where a Future Scope item ripens. |
| `tackle` | Alias for `plan scope:3` — the single-session draft-mode default. Drafts a plan-mode-ready body sized to ~one session; invoke while already in plan mode to ExitPlanMode straight into work. Scope-4 items are flagged, never drafted. |
| `user-add <free text>` | Append a one-line entry to the parallel `User-Tasks.md` doc (user-only addressable items: art, feel, brainstorms, design audits). De-dup search runs first, then prompt for missing fields (domain, context). Append-only from Claude's side. Never touches `Worklog.md` or the mirror. |
| `user-show` | Read `User-Tasks.md` and print grouped by domain. Opt-in only — never folded into `show` / `show all`. The "never read passively" rule applies to passive context loading, not explicit user invocation. |
| `history [domain]` | Read `Worklog-Archive.md` and print completed `[x]` items grouped by domain (optional single-domain filter). Opt-in only — the archive is never loaded passively (no mirror, no SessionStart, no sweep/triage/plan scan). |

## Cross-cutting rules

- **Mirror maintenance (incremental — do NOT re-read the source):** `.claude/worklog-titles.md` is already in your session context (SessionStart injects it). After an Active-section write, **patch the affected line(s) in place** and `Write` the file back — never re-read `Worklog.md` to regenerate the whole mirror. The op already knows what changed:
  - **ADD** (Active path) → append the one line you just composed.
  - **COMPLETE** / `delete` / triage-`promote` (Active→Future Scope) → remove that item's line.
  - **UNBLOCK** → strip the ` [after: <condition>]` suffix from the matched lines.
  - **PROMOTE** (Future Scope→Active) → add the reconstructed item's line.
  - **TRIAGE** → applies several of the above in one walk; rebuild the mirror once at end-of-walk from the full-Active copy it **already read in Step 1** (still no fresh re-read).
  - Future Scope adds/removes do NOT touch the mirror (excluded by design).
  After patching, set `Last synced:` to today. This command is the only writer of the mirror — keeps drift bounded.
  **Full regeneration** (re-read `Worklog.md`, rebuild from scratch) happens ONLY in `/worklog show`'s full-read path — the drift-correction escape hatch when the mirror and source diverge (e.g. a manual Obsidian edit). Routine add/complete must never trigger it.
  Line-format filter rules (apply when composing or regenerating a line):
  - `[ ]` items in `## Active` → included.
  - `[x]` items live in `Worklog-Archive.md`, never in the mirror.
  - Items in `## Future Scope` → **excluded** (deliberately hidden from always-loaded context).
  - Items with `When: after <condition>` → included with ` [after: <condition>]` suffix appended after the title.
  - Items with `Quick-win:` sub-bullet → included with ` [quick-win]` suffix appended after the title (set by `/worklog triage` flag disposition).
  - Items with both `When: after` AND `Quick-win:` → suffixes stack as `... [after: <condition>] [quick-win]` (stable order: after first, quick-win second).
  - Items with no `When:` and no `Quick-win:` line → included as plain `<category> · <class> · <scope> · <title>`.
- **Frontmatter bump:** every Obsidian write also updates `last_updated:` to today's date via `mcp__obsidian__obsidian_manage_frontmatter`.
- **De-duplication:** before any add, scan the in-context mirror for a near-duplicate title in the same domain. If one exists, surface the existing item — don't ask to add again unless user confirms it's distinct. **Caveat:** the mirror does NOT contain Future Scope items — for full de-dup including Future Scope, do an Obsidian read on `## Future Scope` when the candidate has Future-Scope-shaped phrasing.
- **Class + scope are required.** Every Active item needs both. Future Scope items also carry class + scope (one-liner format encodes them). If the user's `add` text doesn't make them obvious, propose inferred values and confirm before writing.
- **Future Scope routing:** when an `add` proposal would route to `## Future Scope`, the propose-and-confirm prompt MUST explicitly say "Future Scope" so the user knows the item will be excluded from the mirror. Format: `Add to Worklog Future Scope: <title> — <domain> · <class> · scope <n>?`. User can override to regular Active with `y, active not future`.
- **MCP-offline:** non-event for the Worklog itself — `Worklog.md` is a vault file edited with native `Read`/`Edit`/`Write`. The one MCP touchpoint is the `last_updated:` frontmatter bump (`obsidian_manage_frontmatter`); if the MCP is down, do that bump with a native `Edit` instead.
- **Auto-detect chain:** CLAUDE.md's detection rule fires *propose-and-confirm*; on `y`, it invokes `/worklog add <inferred-text>` (regular/Future Scope) or `/worklog user-add <inferred-text>` (User-Tasks route) to perform the actual write. The recipe lives here, not in CLAUDE.md or the skill. Strong-trigger phrases route to Future Scope; high-confidence art/feel/brainstorm phrases route to User-Tasks; regular deferral phrases route to Active. See `worklog_reference` skill *Trigger Catalog* for the phrase lists.
- **User-Tasks parallel doc:** `User-Tasks.md` lives at `DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md`. Append-only from Claude's side (writes via `/worklog user-add`); never read passively (no mirror, no SessionStart load, no scan by sweep/triage/plan). Reads only on (a) explicit `/worklog user-show`, (b) the de-dup search inside `/worklog user-add`, or (c) the count footer inside `/worklog show` (count only — content stays in tool result, never echoed into agent output). Worklog.md operations (ADD/COMPLETE/TRIAGE/etc.) never touch `User-Tasks.md`; the only crossover is the `to-user-tasks` disposition inside `/worklog triage`, which migrates an Active item to User-Tasks via the `user-add` recipe.

---

## Cloud fallback (CLAUDE_CODE_REMOTE=true)

On cloud, Obsidian MCP is unavailable, so `Worklog.md` cannot be written. Detect cloud with the Bash form `[ "${CLAUDE_CODE_REMOTE:-}" = "true" ]` — the convention `cloud-install.sh` uses. (This is a `.md` command, not Python; do not invent an `is_cloud()` import.)

- **Mutating ops** (`add`, `complete`, `promote`, `unblock`, triage dispositions): do NOT touch Obsidian or the mirror. **Append** the op to the tracked queue `.claude/worklog-pending.md` under a per-session header, then stop. Entry format:
  ```
  ## <ISO timestamp> cloud session <id>
  - ADD active: <title> (<domain> · <class> · scope <n>)
  - COMPLETE: <title>
  - PROMOTE: <title>
  ```
  Group a session's ops under one header (append lines; new header only when the session changes).
- **Read ops**: `show` operates on the local mirror `worklog-titles.md` (it ships with the checkout). Ops needing the full Obsidian doc (`show all` Future Scope, `sweep`, `plan`/`tackle`) print `Obsidian unavailable on cloud — defer to a local session.` and stop.
- **Commit** the pending file from cloud (it is tracked); the cloud→local handoff crosses machines via git.

### Replay (local session)

Any `/worklog` invocation first checks `.claude/worklog-pending.md`. If its body (below the DO-NOT-HAND-EDIT header) has un-struck `- ` lines:

1. Prompt: `Replay N pending cloud-session entries to Obsidian?` (N = un-struck `- ` lines).
2. On confirm, apply each entry as the matching native op against `Worklog.md` + mirror (ADD→add, COMPLETE→complete, PROMOTE→promote).
3. **Conflict policy = skip-and-audit-trail:** if an entry no longer matches Obsidian state (`COMPLETE: X` but X already archived; `ADD` of an existing title), rewrite that line struck-through (`- ~~<entry>~~ (skipped: <reason>)`) and skip — never hard-fail the replay.
4. After applying, **truncate the body, keep the header**.
5. **Idempotency:** a later run sees a header-only (empty) body → no-op, no re-prompt.

The SessionStart hook surfaces `Cloud worklog: N pending` on local sessions when un-struck entries exist (it excludes struck-through lines).

---

## Operation: SHOW

**Cheap path (preferred):** the mirror is already in your session context (injected by SessionStart hook). Cite from there — print the Active section grouped by domain, no tool call needed. Mirror lines already encode `category · class · scope · title` (with optional `[after: X]` suffix). The mirror does NOT contain Future Scope items by design.

**Capacity check (preface every show output):** count `[ ]` lines in the printed Active section. If count > 30, prepend a one-line alarm above the show output:

```
⚠ Active has <N> items (cap target: 30). Run `/worklog triage` for a guided cleanup pass.
```

If count ≤ 30, omit the alarm entirely. The alarm fires on `show` and `show all` only; it does NOT fire during `add` / `complete` / `plan` / `triage` itself (those have their own contexts). Soft alarm — informational, never blocking.

**Full read (when user asks for context, dates, or sub-bullets):**
```
mcp__obsidian__obsidian_read_note(filePath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md")
```

When showing full content, group by domain and list the `[ ]` items — `Worklog.md` holds only active work; completed `[x]` items live in `Worklog-Archive.md` and surface via `/worklog history`. Don't dump the full Context/Where/Source unless the user asks — title + class + scope + date is the default "show" view.

**Mirror drift-resync (the full-regen escape hatch).** This full read is the *one* place the mirror is rebuilt from scratch: after reading, regenerate `.claude/worklog-titles.md` from the `## Active > [ ]` items per the *Cross-cutting rules > Mirror maintenance* filter rules and `Write` it. Routine add/complete patch the mirror incrementally and never reach here — `show`'s full read is the correction path if the mirror and source ever diverge (e.g. a manual Obsidian edit).

**`show all` form** — also reads Obsidian (the mirror is insufficient since it omits Future Scope), then prints a Future Scope section after the Active listing:

```
## Future Scope (parked, excluded from agent-facing mirror)
### <Domain>
- `<class>` · scope `<n>` · <title> (added <date>; <one-line why-deferred>)
```

When the user runs bare `/worklog show` and the agent suspects a Future Scope item is relevant to the current session work (e.g., recent commits touch the area a Future Scope item is parked against), nudge: "(N items in Future Scope; run `/worklog show all` if you want to review them.)" — single line, no auto-load.

**User-Tasks count footer** — at the end of every `/worklog show` output (both bare and `show all`), do one read of `User-Tasks.md` to count its dated entries (lines matching `^- \d{4}-\d{2}-\d{2} — `). If count > 0, append a single line: `(N items in User-Tasks — /worklog user-show to review)`. If count == 0, omit the line entirely. This is the **only** passive read of `User-Tasks.md` outside of explicit `/worklog user-show` — it's a count for the awareness anchor, not content surfaced into context. If `User-Tasks.md` does not yet exist (first-ever session before any user-add), skip the read silently.

## Operation: USER-SHOW

Opt-in read of `User-Tasks.md`. Not folded into `/worklog show` or `show all` — those emit only the count footer; this prints content.

1. **Read User-Tasks.md.**
   ```
   mcp__obsidian__obsidian_read_note(filePath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md")
   ```
   If the file does not exist (no user-tasks added yet), print: `User-Tasks.md does not exist yet — no items have been routed to it. Use /worklog user-add or accept an auto-detect proposal to start populating it.` and stop.

2. **Print grouped by domain.** For each `## <Domain>` section in the doc, print the heading and its entries verbatim. Preserve date order (newest at top — that's how user-add inserts). Do NOT reformat, sort across domains, or strip dates. The doc IS the source of truth — read it, show it.

3. **Optional age callout.** If any entries are older than 90 days, append below the print:
   ```
   (N entries older than 90 days — consider whether they're still relevant, or edit/remove directly in Obsidian.)
   ```
   Soft nudge. User owns curation; no agent action follows.

## Operation: HISTORY

Opt-in read of `Worklog-Archive.md` — the completed-item store. Not folded into `show` / `show all`; the archive is never loaded passively (no mirror, no SessionStart, no sweep/triage/plan scan). Argument `$ARGUMENTS` is an optional domain filter.

1. **Read the archive.**
   ```
   mcp__obsidian__obsidian_read_note(filePath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog-Archive.md")
   ```
   If the file does not exist (nothing completed since the archive model landed), print: `Worklog-Archive.md does not exist yet — no items have been completed into it.` and stop.

2. **Print grouped by domain.** For each `## <Domain>` section, print the heading and its `[x]` lines verbatim (newest at the bottom — append order). If `$ARGUMENTS` names a domain (fuzzy-match the canonical list), print only that section; if it matches nothing, list the available domain headings and ask.

3. **Read-only.** Completions are written here by COMPLETE, never edited by this op. The archive can grow unbounded; if the user wants it pruned they edit Obsidian directly (git / Obsidian version history is the safety net). No agent-driven archive compaction exists.

## Operation: ADD

1. **De-dup check.** Scan `worklog-titles.md` (already in context) for a near-duplicate title in the same domain. If one exists, surface the existing item instead of asking to add. If user confirms it's distinct, proceed.

2. **Compose the entry block — required fields.**
   - Title: short and imperative (bold).
   - Class: pick from `fix` / `debug` / `feature` / `refactor` / `test` / `docs` / `chore` / `design`. See `worklog_reference` skill for the inference heuristics.
   - Scope: 1–4 (see scope table in `worklog_reference`).
   - Domain: pick from the canonical list. If ambiguous, ask.
   - Context: one line on *why this matters* / *what's the deferred concern*.
   - Date: today.

3. **Compose the entry block — optional fields.**
   - Where: file path or scene reference where the work would land.
   - Source: session name, PR ref, or commit hash that triggered the deferral.
   - **When:** if the user's phrasing implies a prerequisite or phase gate ("once X is done", "after Y ships", "not until Z", "not until we have enemies working"), propose `When: after <condition>` or `When: future` and confirm. If no timing signal is present → omit entirely (item is ready by default).
   - **Plan doc: REQUIRED if scope == 4.** Wikilink form: `[[Doc Title]]`. The doc itself must exist in `TODO/` and be entered in `## Linked Docs`. If the user wants to log a scope-4 item but no doc exists yet, prompt them to create one (or downgrade to scope 3 if it can fit inline).

4. **Inferred-values confirmation (if invoked from auto-detect or `add` with sparse text).**
   When CLAUDE.md auto-detect fires, the propose line should be:
   ```
   Add to Worklog: <title> — <domain> · <class> · scope <n>?
   ```
   User accepts with `y`, or overrides inline: `y, scope 3` / `y, refactor not feature` / `y, domain spell`.

5. **Decide section: Active vs. Future Scope.**

   - If the user's `add` text used a Future Scope strong-trigger phrase (`worklog_reference` skill *Trigger Catalog*) OR the user explicitly tagged `When: future` → route to `## Future Scope`. Skip step 6 (full-block append) and jump to step **5b** (one-liner append) below.
   - Otherwise → route to `## Active > ### Domain` (continue with step 6 below).

5b. **(Future Scope path) Append a one-liner under `## Future Scope > ### Domain`.**

   The Future Scope section is wrapped in a `> [!example]- Future Scope (N items)` collapsed callout — every line inside is prefixed with `> `. Use this format:
   ```
   > - `<class>` · scope `<n>` · <Title> (added YYYY-MM-DD; <one-line context>)
   ```
   No Context/Where/Source sub-bullets. The parenthetical context replaces them — keep it under 80 chars.

   **If the Future Scope section's `### <Domain>` sub-heading exists:**
   ```
   mcp__obsidian__obsidian_search_replace(
     targetType="filePath",
     targetIdentifier="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md",
     replacements=[{
       search: "> ### <Domain Long-Form>\n",
       replace: "> ### <Domain Long-Form>\n> - `<class>` · scope `<n>` · <Title> (added YYYY-MM-DD; <one-line context>)\n"
     }],
     replaceAll: false
   )
   ```

   **If the Future Scope section exists but lacks this `### <Domain>` sub-heading:** insert the sub-heading + new one-liner just before the closing of the callout (the line `> ` immediately preceding `## Linked Docs`).

   **If `## Future Scope` does not exist at all:** create it between `## Active` and `## Linked Docs`:
   ```
   replacements=[{
     search: "## Linked Docs",
     replace: "## Future Scope\n\n> [!example]- Future Scope (1 item)\n> <!-- Distant-horizon items (When: future). One-liner format only — excluded from `.claude/worklog-titles.md` mirror. -->\n> ### <Domain Long-Form>\n> - `<class>` · scope `<n>` · <Title> (added YYYY-MM-DD; <one-line context>)\n\n## Linked Docs"
   }]
   ```

   Then update the callout's item-count: re-read the Future Scope block, count the `> - ` lines, and `search_replace` the `(N items)` substring on the callout-header line. Skip count-update if the Future Scope section was just created (already says `(1 item)`).

   Skip steps 6–7 — jump to step 8 (frontmatter bump) and step 9 (mirror rewrite). The mirror rewrite will exclude this item by design.

6. **(Active path) Append under the right `### Domain` heading in Obsidian.**

   New `[ ]` items go at the **top** of the domain section, immediately after the heading line — above any existing `[ ]` items. Newest active item is always nearest to the heading. (No `[x]` history lives in Active anymore — completions move to `Worklog-Archive.md`.)

   **If the domain section already exists:**
   ```
   mcp__obsidian__obsidian_search_replace(
     targetType="filePath",
     targetIdentifier="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md",
     replacements=[{
       search: "### <Domain Long-Form>\n",
       replace: "### <Domain Long-Form>\n- [ ] **<Title>** — `<class>` · scope `<n>` · added YYYY-MM-DD\n  - Context: <one-line>\n  - Where: <path-if-any>\n  - Source: <ref-if-any>\n  - When: after <condition> | future  (omit line entirely if ready)\n  - Plan doc: [[<Doc Title>]]  (omit line entirely if not scope 4)\n"
     }],
     replaceAll: false
   )
   ```

   **If the domain section does NOT exist yet:** insert it with the new item immediately before `## Linked Docs`.
   ```
   replacements=[{
     search: "## Linked Docs",
     replace: "### <Domain Long-Form>\n- [ ] **<Title>** — `<class>` · scope `<n>` · added YYYY-MM-DD\n  - Context: <one-line>\n\n## Linked Docs"
   }]
   ```

7. **(Active path, scope-4 only) If the named Plan doc isn't already in `## Linked Docs`,** add it there as well, in the same domain subsection. Skip if Future Scope path was taken (Future Scope items don't carry Plan docs — they're scope-2/3 distant work, not scope-4 design tracks).

8. **Bump frontmatter.**
   ```
   mcp__obsidian__obsidian_manage_frontmatter(
     filePath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md",
     operation="set",
     key="last_updated",
     value="YYYY-MM-DD"
   )
   ```

9. **Patch the mirror (incremental).** Insert the single line you just composed into `.claude/worklog-titles.md` (already in your session context) next to the existing lines sharing its `<category>` prefix — or as a new cluster if that category isn't present yet — then `Write` it back. Format per *Cross-cutting rules > Mirror maintenance*. Do NOT re-read `Worklog.md`. Update `Last synced:` to today's date. (Future Scope adds took step 5b and carry no mirror line — skip this step.)

10. **Confirm to user:** `Added.` (terse — they don't need to re-read what they just confirmed). For Future Scope adds, `Added to Future Scope.` (so the user knows the item is parked, not active).

### Invocation contexts for ADD

| Caller | Confirm-first? | Notes |
|--------|---------------|-------|
| User explicitly types `/worklog add <text>` | No (already explicit) | Skip the propose-and-confirm; go straight to recipe. Ask only for missing fields (class, scope, domain, context). |
| CLAUDE.md auto-detect fires + user says `y` | Already done by CLAUDE.md | Just execute the recipe. CLAUDE.md handled the propose, including inferred class+scope. Honor any inline overrides (`y, scope 3`). |
| `/worklog sweep` proposes an add + user says `y` | Already done by sweep | Same as above. |

## Operation: USER-ADD

Append a one-line entry to `User-Tasks.md` for items requiring user judgment Claude cannot tackle. Append-only — never modifies or deletes existing entries.

1. **De-dup search.** Quick search against `User-Tasks.md` for near-duplicate titles in the same domain. Active-operation carve-out to the "never read passively" rule — result lands in the tool response and informs the propose-and-confirm; doesn't persist into always-loaded context.
   ```
   mcp__obsidian__obsidian_global_search(
     query="<title keywords>",
     searchInPath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md"
   )
   ```
   On near-match: `User-Tasks already has: <existing entry>. Add as distinct (y/n)?`. On `n`, abort.

2. **Compose the entry.** Single line — no class/scope, no Where/Source sub-bullets:
   ```
   - YYYY-MM-DD — <Title> — <one-line context>
   ```
   - **Date:** today (Claude flagged it now — not the user's eventual action date).
   - **Title:** short, imperative, ≤60 chars. No bold (flat doc, no checkbox semantics).
   - **Context:** ≤80 chars on *why* this needs user attention.
   - **Domain:** pick from the canonical list in `worklog_reference` *Canonical domain list* (single source — not restated here).

3. **Inferred-values confirmation** (when invoked from auto-detect). Propose line is:
   ```
   Route to User-Tasks: <title> — <domain>?
   ```
   User accepts with `y`, overrides domain inline (`y, domain spell`), or overrides the route entirely with `y, active not user-tasks` (reroutes to `/worklog add` — User-Tasks add is cancelled, Active add proceeds).

4. **Append under the right `## <Domain>` heading.**

   **If the domain section already exists:**
   ```
   mcp__obsidian__obsidian_search_replace(
     targetType="filePath",
     targetIdentifier="DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md",
     replacements=[{
       search: "## <Domain Long-Form>\n",
       replace: "## <Domain Long-Form>\n- YYYY-MM-DD — <Title> — <one-line context>\n"
     }],
     replaceAll: false
   )
   ```
   New entries go at the **top** of the domain section (immediately after the heading line) — newest nearest the heading.

   **If the domain section does NOT exist:** append heading + entry at the end of the file using `obsidian_update_note`:
   ```
   mcp__obsidian__obsidian_update_note(
     targetType="filePath",
     targetIdentifier="DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md",
     modificationType="wholeFile",
     wholeFileMode="append",
     content="\n## <Domain Long-Form>\n- YYYY-MM-DD — <Title> — <one-line context>\n"
   )
   ```

5. **Bump frontmatter** on `User-Tasks.md` (NOT Worklog.md):
   ```
   mcp__obsidian__obsidian_manage_frontmatter(
     filePath="DevProjects/{{PROJECT_NAME}}/Claude/TODO/User-Tasks.md",
     operation="set",
     key="last_updated",
     value="YYYY-MM-DD"
   )
   ```
   **No mirror rewrite** — User-Tasks is excluded from `.claude/worklog-titles.md` by design.

6. **Confirm:** `Routed to User-Tasks.`

### Invocation contexts for USER-ADD

| Caller | Confirm-first? | Notes |
|--------|---------------|-------|
| User types `/worklog user-add <text>` | No (explicit) | Skip propose-and-confirm. Ask only for missing domain + context. |
| CLAUDE.md auto-detect (User-Tasks route) + user says `y` | Already done by CLAUDE.md | Execute recipe with inferred title + domain. Honor inline overrides (`y, domain spell` / `y, active not user-tasks`). |
| `/worklog triage` → `to-user-tasks` disposition | Already done by triage walk | Execute recipe from the Active item's title + Context + domain. Skip Step 1 (de-dup) — the walker has already exposed existing entries. |

## Operation: COMPLETE

Cross-doc move: the `[ ]` block leaves `Worklog.md` entirely and a `[x]` one-liner lands in `Worklog-Archive.md` under its domain. The active doc holds only live work; completions are preserved (opaque) in the archive, readable via `/worklog history`.

1. **Identify the entry.** Match by title or fuzzy search against `[ ]` items in the `## Active` section of `Worklog.md`. If multiple matches, list them and ask the user to pick. (There are no `[x]` items in `Worklog.md` to collide with — they live in the archive.)

2. **Compose the one-liner:**
   ```
   - [x] `<class>` · scope `<n>` · <Title> (completed YYYY-MM-DD, <ref>)
   ```
   - `<class>` and `<n>` come from the original `[ ]` block (don't re-classify on completion unless the user explicitly asks).
   - `<ref>` is optional but recommended: commit hash (`abc1234`), PR (`#62`), or session name. Prefer commit hash for shipped code.
   - Title stays the same wording; just unbold it (the `[x]` line uses plain text, not `**bold**`).

3. **Append the `[x]` one-liner to `Worklog-Archive.md` FIRST (before deleting from Active — see atomicity note).**

   **If `Worklog-Archive.md` doesn't exist yet,** create it: frontmatter (`title: Worklog Archive`, `status: archive`, `last_updated: <today>`), an `# Worklog Archive` heading, the opacity note (`> Completed worklog items ... never mirrored, never loaded at SessionStart, never scanned by sweep/triage/plan. Read only via /worklog history.`), then a `## <Domain>` section holding the line.

   **If the archive has the item's `## <Domain>` section,** append the line at the bottom of that section (just before the next `## ` heading, or end of file):
   ```
   mcp__obsidian__obsidian_search_replace(
     targetType="filePath",
     targetIdentifier="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog-Archive.md",
     replacements=[{
       search: "<lastline-of-domain-section>\n",
       replace: "<lastline-of-domain-section>\n- [x] `<class>` · scope `<n>` · <Title> (completed YYYY-MM-DD, <ref>)\n"
     }]
   )
   ```

   **If the archive lacks the item's `## <Domain>` section,** append a new `## <Domain>` section with the line at end of file (`obsidian_update_note` wholeFile append).

   Newest completion goes at the **bottom** of its domain section (append order = completion order).

4. **Delete the `[ ]` block from `Worklog.md`** (checkbox + its indented sub-bullets) via `obsidian_search_replace`:
   ```
   replacements=[{
     search: "- [ ] **<Title>** — `<class>` · scope `<n>` · added YYYY-MM-DD\n  - Context: ...\n  - Where: ...\n  - Source: ...\n  - Plan doc: ...\n",
     replace: ""
   }]
   ```
   Match all sub-bullets that exist in this specific block — be precise about which lines are present.

   **Line-ending note:** both `Worklog.md` and `Worklog-Archive.md` are LF — use `\n` separators. `obsidian_search_replace` matches line endings literally, so a separator mismatch silently returns `totalReplacementsMade: 0` (no error). Capture the verbatim block from a targeted read of its domain section — don't reconstruct from memory or earlier-captured escaped text; if a match 0-hits, retry with `\r\n` (the file may have been re-saved as CRLF). See `obsidian_conventions`.

   **Atomicity:** if Step 3 (archive write) failed, do NOT execute this delete. Surface the error and stop — better a stuck Active item than a lost completion. (A duplicate archive line from a retried Step 3 is harmless; the archive is opaque.)

5. **Remove the now-empty domain heading** (if applicable). If the delete left the item's `### Domain` section with no remaining `- [ ]` items, remove the `### Domain` heading line too — empty headings are noise now that completed history lives in the archive, not Active. If other `[ ]` items remain under it, leave the heading.

6. **Bump frontmatter `last_updated`** on BOTH `Worklog.md` and `Worklog-Archive.md`.

7. **Patch the mirror (incremental).** Remove the just-completed item's line from `.claude/worklog-titles.md` (already in context) and `Write` it back. Do NOT re-read `Worklog.md`. Update `Last synced:` to today.

8. **Confirm to user:** `Marked complete.`

9. **Pair-emit to tackle history (if applicable).** Read `.claude/worklog-tackle-history.jsonl` (always exists, may be empty). If any `tackle` event line has `title` matching the just-completed item AND no later `completion` event for that same title exists, append a pair-completion event:
   ```json
   {"event": "completion", "date": "YYYY-MM-DD", "title": "<title verbatim>"}
   ```
   Use `printf '...\n' >> .claude/worklog-tackle-history.jsonl`. The `title` MUST match the tackle event's title verbatim — agents reading this file pair by exact-string match, not fuzzy match.

   If no matching `tackle` event exists, skip silently. The item was completed without ever being tackled — no anti-thrash signal to clear.

   This step is what makes the PLAN anti-thrash penalty self-clearing (PLAN recipe: `agents/worklog_plan_triage.md`). Without it, every tackled-then-completed item would forever carry the −2 penalty on re-occurrence.

### Edge cases for COMPLETE

- **User wants to un-complete (re-open) an item:** treat as a manual `add` for now — propose a fresh `[ ]` block with the original title and a `Source: re-opened from <YYYY-MM-DD> completion` note. We don't have an `uncomplete` form.
- **Item was scope 4 with a Plan doc:** the `[x]` line still references its plan doc implicitly via the title; the `## Linked Docs` entry can stay (the doc itself remains a useful artifact).
- **Item was a `debug` that resolved into a `fix`:** the original class is preserved on the `[x]` line. If a follow-up `fix` item is needed, that's a separate `add`.

## Operation: SWEEP

Three passes over the recent session, each confirmation-driven:

**Add-sweep:** scan the conversation transcript for trigger phrases I missed (see `worklog_reference` skill for the full catalog — both regular-deferral and Future-Scope triggers). For each candidate, propose `Add to Worklog: <title> — <domain> · <class> · scope <n>?` (or `Add to Worklog Future Scope: ...` if a strong-trigger phrase fired) with context citing the turn where it appeared. On `y` (or `y, <override>`), run the ADD recipe.

**Completion-sweep:** read the current Active section. Diff its `[ ]` items against `git status` / `git log --since="session start"` and the session's tool calls. For each `[ ]` item that the session plausibly resolved (file mentioned in commit, test added/passing, etc.), propose `Mark complete: <title> (<commit-ref>)?`. On `y`, run the COMPLETE recipe.

**Promotion-sweep (Future Scope ripening check):** read the `## Future Scope` section. For each one-liner, scan its title + parenthetical-context against:
- `git log --since="session start"` (and `--since="2 weeks ago"` if today is the first session of the week — wider window once per week)
- `git status --short`
- The session's tool-call topics (file paths, test names, system terms touched)

For each Future Scope item where a substring of its title or context overlaps a recent commit message, file path, or session topic, propose:
```
Promote from Future Scope: <title> — looks ripened (matched: "<short evidence>")?
```
On `y`, run the PROMOTE recipe (below). On `n` or `n, still parked`, skip silently.

Heuristics for the matcher (keep low false-positive rate; better to miss than to spam):
- Require ≥2 distinct token matches OR one specific identifier match (file path, function name, version number, PR number).
- Skip generic words ("test", "audit", "review") as match anchors — they're too noisy.
- Cap proposals at 5 per sweep to prevent overwhelming the user. If more than 5 candidates fire, propose the top 5 by match-strength and surface the rest with `(N more Future Scope candidates — run /worklog show all to review)`.

All three passes are confirmation-driven — never auto-apply. They are the deterministic backstop for the immediate-add, immediate-complete, and condition-ripening rules when signals were missed inline. Used by `/session_end` Phase 6.

## Operation: TRIAGE

Full recipe extracted to [`agents/worklog_plan_triage.md`](agents/worklog_plan_triage.md) — Read that file on any `triage` invocation and execute from its steps (disposition scoring table, confirmation walk, caps, edge cases). Do not run triage from memory of this stub.

## Operation: PROMOTE

Move a `When: future` item from `## Future Scope` back to `## Active`. Argument is `$ARGUMENTS` (title or fuzzy-search string).

### Step 1 — Identify the Future Scope item

Read `## Future Scope` from Obsidian. Match argument against the one-liner titles. If multiple matches, list them and ask user to pick. If no matches, suggest `/worklog show all` to review what's parked.

### Step 2 — Reconstruct an Active `[ ]` block

The one-liner format is:
```
> - `<class>` · scope `<n>` · <Title> (added YYYY-MM-DD; <one-line context>)
```

Reconstruct as a full `[ ]` block:
```
- [ ] **<Title>** — `<class>` · scope `<n>` · added YYYY-MM-DD (promoted from Future Scope YYYY-MM-DD-today)
  - Context: <one-line context from the parenthetical>
  - Source: promoted from Future Scope on YYYY-MM-DD
```

Preserve original `added` date — promotion is a state change, not a new add. Append the promotion date in the title parenthetical so the history is visible.

### Step 3 — Apply the move

Two-step `obsidian_search_replace`:

**Step A — delete the one-liner from `## Future Scope`:**
```
replacements=[{
  search: "> - `<class>` · scope `<n>` · <Title> (added YYYY-MM-DD; <context>)\n",
  replace: ""
}]
```

**Step B — insert the new `[ ]` block at the top of the matching `## Active > ### Domain` section.** Same anchor pattern as ADD step 6 (insert immediately after `### <Domain Long-Form>\n`).

### Step 4 — Cleanup

- If the Future Scope `### <Domain>` sub-section is now empty (no `> - ` lines), remove the sub-heading.
- If `## Future Scope` is now entirely empty (no `> - ` lines anywhere), remove the whole section including its `> [!example]-` callout wrapper.
- Update the callout's `(N items)` count in the header.

### Step 5 — Bump frontmatter + rewrite mirror

Same as ADD steps 8–9 — bump frontmatter, then **incrementally add** the promoted item's reconstructed line to the mirror (it's now ready, no longer `When: future`). Do NOT re-read the source.

### Step 6 — Confirm

```
Promoted: <Title> — now in Active under <Domain>.
```

### Edge cases for PROMOTE

- **Promotion immediately followed by completion** (the item ripened *and* shipped in the same session): run PROMOTE then COMPLETE in sequence. Don't try to skip the intermediate state — it makes the history readable.
- **User rejects a sweep-proposed promotion:** mark nothing. Do NOT add a `When: not yet` annotation — that's noise. The next sweep will re-evaluate.
- **Title collision with an existing Active item:** ask user. Possible the same work was logged twice (once as Active, once as Future Scope). Resolve manually.

## Operation: PLAN (+ `tackle` alias)

Full recipe extracted to [`agents/worklog_plan_triage.md`](agents/worklog_plan_triage.md) — Read that file on any `plan` / `tackle` invocation and execute from its steps (scoring engine, survey/draft fork, tackle-history JSONL schema, edge cases). Do not draft from memory of this stub.

---

## Operation: UNBLOCK

Promotes one or more `after <condition>` items to ready by stripping their `When:` sub-bullet. Argument is `$ARGUMENTS` (the condition text to match against).

### Step 1 — Find matching items

Read the Active section (mirror already in context for a quick scan; full Obsidian read if needed). Find all `[ ]` items where the `When: after <condition>` text fuzzy-matches the argument. Case-insensitive substring match is sufficient.

Show the matches before writing:
```
Unblocking items matching "<condition>":
  1. <title> (<domain>) — When: after <condition>
  2. <title> (<domain>) — When: after <condition>

Proceed? (y/n)
```

If no matches found: `No items found with When: after <condition>.` (check for typos, suggest alternatives if any `after` items exist).

### Step 2 — Strip the When: line for each matched item

For each confirmed item, remove the `When: ...` sub-bullet via `obsidian_search_replace`:
```
mcp__obsidian__obsidian_search_replace(
  targetType="filePath",
  targetIdentifier="DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md",
  replacements=[{
    search: "  - When: after <condition>\n",
    replace: ""
  }],
  replaceAll: false
)
```

Use the exact `When:` line text from the item, not the argument verbatim (they may differ slightly after fuzzy match).

### Step 3 — Bump frontmatter + patch mirror

After all strips are applied:
- Bump `last_updated` frontmatter.
- **Incrementally patch** `.claude/worklog-titles.md` (already in context) — strip the ` [after: <condition>]` suffix from the matched lines. Do NOT re-read the source.

### Step 4 — Confirm

```
Unblocked <n> item(s). They are now ready and will appear in /worklog plan batches.
```

### Edge cases for UNBLOCK

- **`future` items:** `unblock` does not match `When: future` — those have no condition to match. To unblock a `future` item, run `/worklog add` to re-open it without a `When:` line, or edit Obsidian directly and re-sync the mirror.
- **Multiple conditions partially matching:** show all matches and let user confirm each individually. Don't bulk-strip on a partial fuzzy match.
- **Item already has no `When:`:** skip silently (already ready).

---

## Examples

### Add cycle (explicit, scope-2)

User: `/worklog add audit _continuousTracking confidence-pin call sites`

1. De-dup: no match in mirror under AI/NPCs.
2. Compose: title `Audit _continuousTracking confidence-pin call sites`, class `refactor` (audit-and-cleanup language), scope `2` (multi-file but mechanical), domain `AI / NPCs` (inferred from `_continuousTracking` → perception). Ask user to confirm class/scope/domain.
3. User confirms (`y` or `y, scope 3` to override). Ask for one-line Context.
4. User provides: "follow-up from CorneredAction perception gotcha".
5. Run ADD recipe steps 5–8. (Scope 2, no Plan doc needed.)
6. Confirm: `Added.`

### Add cycle (auto-detect, scope-4)

I just said in conversation: "we should brainstorm a unified status-effect blackboard schema later — too big for this session."

1. Auto-detect fires: `Add to Worklog: Brainstorm unified status-effect blackboard schema — spell · design · scope 4?`
2. User: `y`.
3. Recipe: scope == 4, so prompt user for Plan doc title. User provides "Status BB Schema Design".
4. Verify the doc exists at `TODO/Status BB Schema Design.md` — if not, ask user to create it first (or downgrade to scope 3).
5. Append `[ ]` block to `### Spell Architecture` with `Plan doc: [[Status BB Schema Design]]`.
6. Append `## Linked Docs` entry under `### Spell Architecture`.
7. Bump frontmatter, rewrite mirror, confirm.

### Complete cycle (archive move)

Test passes for `Sweep ValidateRequiredExports() into the 4 non-runner CombatEffectFactory subclasses`. Commit `f3a91b2` lands.

1. Identify: matches one `[ ]` item under `### Spell Architecture`, class `refactor`, scope `2`.
2. Compose: `- [x] \`refactor\` · scope \`2\` · Sweep ValidateRequiredExports() into the 4 non-runner CombatEffectFactory subclasses (completed 2026-04-29, f3a91b2)`
3. Step 3 (archive FIRST): append the `[x]` line at the bottom of `## Spell Architecture` in `Worklog-Archive.md` (create the section if absent).
4. Step 4: archive write succeeded, so delete the old `[ ]` block from `Worklog.md` (4 lines: checkbox + Context + Where + Source).
5. Step 5: `### Spell Architecture` still has other `[ ]` items, so the heading stays. (Had it been the last item, the heading would be removed.)
6. Bump frontmatter on both docs, patch mirror (remove the now-completed line), confirm `Marked complete.`
