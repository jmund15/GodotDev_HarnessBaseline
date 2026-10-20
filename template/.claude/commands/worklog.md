---
description: Run a worklog operation: add, complete, promote, unblock, triage, sweep, show, or an installed form such as drive
---

The single executor for all worklog operations. Source of truth: `DevProjects/{{PROJECT_NAME}}/Claude/TODO/Worklog.md` (Obsidian); its title mirror, read only when an operation needs it, is `.claude/worklog-titles.md`. This file owns the tier model, the form dispatch and the rules every operation obeys; each operation's recipe lives in a reference file read at that step.

**Two open tiers, both mirrored, both reachable.** `## Active` is the ready pool; keep it scannable by readiness, not by a fixed item count. `## Ledger` holds unscheduled specified debt and every `When: after` item, in identical block shape. Drive surveys ready items in Active and Ledger and reports waiting Ledger items separately; named-item lookup can resolve a ready Ledger item without changing its tier. COMPLETE and TRIAGE scan Active and Ledger; PROMOTE searches Future Scope; UNBLOCK searches Ledger (`.claude/commands/reference/worklog_promote_unblock.md`). TRIAGE moves a Ledger item to Active only when its `When: after` condition has cleared; an explicit `promote` remains separate. An add never silently demotes a ready item. Classification + scope rules, domain list, trigger catalog, completion signals, which-operation litmus: `worklog_reference` skill.

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
| `triage` | Bulk cleanup walk: evaluate per-item readiness and dispositions, walk confirmation-driven, execute inline, rebalance Active and Ledger. Use when the ready list is no longer scannable, items look stale, or selection is blocked by backlog noise. |
| `unblock <condition>` | Strip `When: after <condition>` from all fuzzy-matching items; show matches before writing. |
| `promote <title>` | Move a `When: future` item from Future Scope back to Active, reconstructing a full `BLOCK`. |
| `user-add <free text>` | Append a `UTLINE` to `User-Tasks.md` (user-only addressable: art, feel, brainstorms, design audits). De-dup first. Append-only; never touches `Worklog.md` or the mirror. |
| `user-show` | Print `User-Tasks.md` grouped by domain. Opt-in only — never folded into `show` / `show all`. |
| `history [domain]` | Print `Worklog-Archive.md`'s `[x]` items grouped by domain (optional filter). Opt-in only; never loaded passively. |

## Recipes — read one at its step

- At the first vault call of any operation read `.claude/commands/reference/worklog_formats.md` — call shorthands (`SR` / `READ` / `FM` / `APPEND`) and the `BLOCK` / `FSLINE` / `XLINE` / `UTLINE` entry formats.
- When writing `.claude/worklog-titles.md`, read `.claude/commands/reference/worklog_mirror.md` — per-operation patch rules, file shape and line-format filters.
- At the `show`, `show ledger`, `show all`, `user-show` or `history` step read `.claude/commands/reference/worklog_show.md`.
- At the `add` or `user-add` step read `.claude/commands/reference/worklog_add.md`.
- At the `complete` step read `.claude/commands/reference/worklog_complete.md`.
- At the `sweep` step read `.claude/commands/reference/worklog_sweep.md`.
- At the `promote` or `unblock` step read `.claude/commands/reference/worklog_promote_unblock.md`.
- At the `triage` step read `.claude/commands/reference/worklog_triage.md` and execute from its steps; do not run triage from memory.
- A form the table does not list runs from `.claude/commands/reference/worklog_<form>.md` when that file exists: read it and execute from its steps, not from memory. A higher layer installs its forms this way, as the coding layer installs `drive`. No such file → report that the form is not installed here.
- If `CLAUDE_CODE_REMOTE=true`, or if `.claude/worklog-pending.md` holds un-struck `- ` lines, read `.claude/commands/reference/worklog_cloud.md` first.

## Rules every operation obeys

- **Mirror maintenance:** after a local Active edit, follow `.claude/commands/reference/worklog_mirror.md`.
- **Frontmatter bump:** every vault write also runs `FM(<the doc written>)`.
- **User-Tasks is append-only and never read passively** — no mirror, no SessionStart, no sweep/triage/drive scan. Its only reads are `user-show` and the de-dup search inside `user-add`. `Worklog.md` operations never touch it; triage's `to-user-tasks` disposition is the one crossover.
- **Cloud check first.** Detect cloud with the Bash form `[ "${CLAUDE_CODE_REMOTE:-}" = "true" ]`.
- **Native vault operations:** `Read`/`Edit`/`Write` implement the shorthands, including `FM()`, in `.claude/commands/reference/worklog_formats.md`.
