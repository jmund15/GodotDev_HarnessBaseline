---
description: Run any worklog operation — drive logged items, add, complete, promote, unblock, triage, sweep, show.
---

The single executor for all worklog operations. Source of truth: `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` (Obsidian); its title mirror, read only when an operation needs it, is `.claude/worklog-titles.md`. This file owns the tier model, the form dispatch and the rules every operation obeys; each operation's recipe lives in a reference file read at that step.

**Two open tiers, both mirrored, both reachable.** `## Active` is the ready pool — hot-context plus newest, **30-item ceiling**. `## Ledger` holds unscheduled specified debt and every `When: after` item, in identical block shape. Three things read Active alone: the capacity check, DRIVE survey batching, DRIVE budget scoring. Everything else — `complete`, `unblock`, `promote`, named-item DRIVE, TRIAGE — scans both. TRIAGE Step 6 rebalances; an add never demotes. Classification + scope rules, domain list, trigger catalog, completion signals, which-operation litmus: `worklog_reference` skill.

## Forms

Argument: `$ARGUMENTS`

| Form | Operation |
|------|-----------|
| (no args) or `show` | Print Active (the ready pool) grouped by domain, from the mirror unless full content is needed. Footer-count `## Ledger`; **exclude `## Future Scope`**. |
| `show ledger` | Print `## Ledger` grouped by domain, same format. Opt-in — `show` prints its count, never its contents. |
| `show all` | `show` plus the full `## Ledger` listing plus the `## Future Scope` callout contents, each grouped by domain. |
| `add <free text>` | ADD, bypassing auto-detect's confirm. `When: future` or a strong-trigger phrase (`worklog_reference` *Trigger Catalog*) routes to Future Scope as an `FSLINE`. |
| `complete <id-or-search>` | COMPLETE the matched item — its `BLOCK` leaves `Worklog.md`, an `XLINE` lands in `Worklog-Archive.md`. Multiple matches → ask. |
| `sweep` | Three confirmation-driven passes: add-sweep, completion-sweep, promotion-sweep. |
| `triage` | Bulk cleanup walk: score per-item dispositions, walk confirmation-driven, execute inline, rewrite mirror at end. Use when Active > 30 or backlog pressure builds. |
| `drive [<N> \| scope:N \| items:N \| <names>] [--plan-only]` | Agentic prioritization-and-execution to commits. Three modes, first match wins after `--plan-only` is stripped: **no argument** → *survey*; **budget-shaped** (`<N>` ≡ `scope:N`, combinable with `items:N`, more-restrictive binds) → score and fill the target; **anything else** → *named-items*. Only ready items (no `When:` sub-bullet) are selectable. Scope-4 is never driven — route to `/design_drive` or `/part_drive`. `--plan-only` stops at the drafted plan body. |
| `unblock <condition>` | Strip `When: after <condition>` from all fuzzy-matching items; show matches before writing. |
| `promote <title>` | Move a `When: future` item from Future Scope back to Active, reconstructing a full `BLOCK`. |
| `user-add <free text>` | Append a `UTLINE` to `User-Tasks.md` (user-only addressable: art, feel, brainstorms, design audits). De-dup first. Append-only; never touches `Worklog.md` or the mirror. |
| `user-show` | Print `User-Tasks.md` grouped by domain. Opt-in only — never folded into `show` / `show all`. |
| `history [domain]` | Print `Worklog-Archive.md`'s `[x]` items grouped by domain (optional filter). Opt-in only; never loaded passively. |

## Recipes — read one at its step

- At the first vault call of any operation read `reference/worklog_formats.md` — call shorthands (`SR` / `READ` / `FM` / `APPEND`) and the `BLOCK` / `FSLINE` / `XLINE` / `UTLINE` entry formats.
- When writing `.claude/worklog-titles.md`, read `reference/worklog_mirror.md` — per-operation patch rules, file shape and line-format filters.
- At the `show`, `show ledger`, `show all`, `user-show` or `history` step read `reference/worklog_show.md`.
- At the `add` or `user-add` step read `reference/worklog_add.md`.
- At the `complete` step read `reference/worklog_complete.md`.
- At the `sweep` step read `reference/worklog_sweep.md`.
- At the `promote` or `unblock` step read `reference/worklog_promote_unblock.md`.
- At the `triage` step read `reference/worklog_triage.md` and execute from its steps; do not run triage from memory.
- At the `drive` step read `reference/worklog_drive.md` and execute from its steps; do not drive from memory. Its Step 5 continues in `reference/worklog_drive_execute.md`.
- If `CLAUDE_CODE_REMOTE=true`, or if `.claude/worklog-pending.md` holds un-struck `- ` lines, read `reference/worklog_cloud.md` first.

## Rules every operation obeys

- **Mirror maintenance is incremental.** When an operation writes Active, patch the affected lines of `.claude/worklog-titles.md` and `Write` back; never re-read `Worklog.md` to rebuild it. This command is the mirror's only writer, and SHOW's full-read path is its only full regeneration.
- **Frontmatter bump:** every vault write also runs `FM(<the doc written>)`.
- **User-Tasks is append-only and never read passively** — no mirror, no SessionStart, no sweep/triage/drive scan. Its only reads are `user-show`, the de-dup search inside `user-add`, and `show`'s count footer (count only — the content stays in the tool result). `Worklog.md` operations never touch it; triage's `to-user-tasks` disposition is the one crossover.
- **DRIVE executes by default.** The invocation is the execution directive; Mode D (choose-among) is the one selection gate; `--plan-only` stops at the drafted plan body.
- **Cloud check first.** Detect cloud with the Bash form `[ "${CLAUDE_CODE_REMOTE:-}" = "true" ]` — this is a `.md` command, so there is no `is_cloud()` import. Before doing its own work, every invocation checks whether `.claude/worklog-pending.md` holds un-struck `- ` lines.
- **MCP-offline:** `Worklog.md` is a vault file editable with native `Read`/`Edit`/`Write`. The one MCP touchpoint is `FM()` — do that bump with a native `Edit` when the MCP is down.
