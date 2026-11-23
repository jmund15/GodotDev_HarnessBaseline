---
description: Run a worklog operation: show, add, complete, sweep, triage, drive, promote, unblock, user-add, history
---

# /worklog

The one executor of worklog writes. `worklog_reference` owns whether to log an item and its class, scope, domain and `When:`; this file owns the files, the tiers, the forms and their write steps.

**Files.** The vault TODO folder is `<vault_root>/DevProjects/<project_name>/Claude/TODO/`, each value printed by `python3 .claude/tools/adaptation.py get <key>`. It holds Worklog.md (the source of truth), Worklog-Archive.md (completed items, read only by `history`) and User-Tasks.md (owner-only items, read only by `user-show` and by `user-add`'s de-dup). `.claude/worklog-titles.md` mirrors the open titles and is read only when a form needs it. Entry and mirror formats: [`reference/worklog_formats.md`](reference/worklog_formats.md).

**Tiers.** `## Active` is the ready pool, kept scannable by readiness, never by an item count. `## Ledger` holds unscheduled debt and every `When: after` item, in the same block shape. `## Future Scope` holds `When: future` one-liners and stays out of the mirror. An add never demotes a ready item; only `triage` and `unblock` move items between tiers, and only `promote` takes one out of Future Scope.

**Cloud.** When `[ "${CLAUDE_CODE_REMOTE:-}" = "true" ]` holds, or .claude/worklog-pending.md holds un-struck `- ` lines, read [`reference/worklog_cloud.md`](reference/worklog_cloud.md) before any form.

## Forms

Argument: `$ARGUMENTS`. An unlisted form prints this table and stops.

| Form | Operation |
|---|---|
| (none) or `show` | Print Active by domain from the mirror; footer-count Ledger; leave Future Scope out |
| `show ledger` | Print Ledger by domain |
| `show all` | `show`, then Ledger, then Future Scope read from the vault |
| `add <text>` | §add |
| `user-add <text>` | §user-add |
| `complete <title or search>` | §complete |
| `sweep` | §sweep; `/close` runs it |
| `triage` | Walk Active and Ledger proposing one disposition each: [`reference/worklog_triage.md`](reference/worklog_triage.md) |
| `drive [<N> \| scope:N \| items:N \| <names>] [--plan-only]` | Select items and ship them through `/drive`: [`reference/worklog_drive.md`](reference/worklog_drive.md) |
| `promote <title>` | §promote |
| `unblock <condition>` | §unblock |
| `user-show` | Print User-Tasks.md by domain, verbatim |
| `history [domain]` | Print Worklog-Archive.md by domain, verbatim, optionally one domain |

## Write rules

- `add` and `complete` run through `python3 .claude/tools/worklog.py`, which de-dups, places the entry, bumps `last_updated:` and patches the mirror. Exit 2 means a duplicate, an ambiguous match or invalid input: show what it printed and ask. Never hand-edit the vault for these two.
- Every other write: edit the doc, bump its frontmatter `last_updated:` to today, and patch the mirror as `worklog_formats.md` §Mirror says. Future Scope and User-Tasks.md writes never touch the mirror.
- Take a block's exact text from a targeted read of its section, never from memory. The docs are LF; when an edit finds no match, retry with CRLF and confirm the edit landed.
- A full read of Worklog.md regenerates the mirror from it: that is the drift check.

## add

1. Take title, class, scope and domain from `worklog_reference` (§Classification, §Scope, §Canonical domain list), a one-line context, and the optional `Where:`, `Source:` and `When:` (`after <condition>` or `future`). Scope 4 needs a plan doc that exists in the TODO folder and is listed under `## Linked Docs`; with none, ask the owner to create it, or lower the scope when the work fits inline.
2. An auto-detect proposal, or `add` text that leaves a field to infer, confirms once: `Add to Worklog: <title> — <domain> · <class> · scope <n>?`. A Future Scope item says so: `Add to Worklog Future Scope: …`. The owner's inline overrides (`y, scope 3`, `y, active not future`) apply without a second ask.
3. Run `python3 .claude/tools/worklog.py add "<title>" --class <c> --scope <n> --domain "<Domain>" --context "<line>" [--where <path>] [--source <ref>] [--when "after <condition>"|future] [--plan-doc "<Doc Title>"]`.
4. Report `Added.`, or `Added to Future Scope.`

## user-add

For an item only the owner can do (`worklog_reference` §User-Tasks triggers).

1. Grep User-Tasks.md for the title's keywords. On a near match ask `User-Tasks already has: <entry>. Add as distinct?`; on no, stop. A `triage` caller skips this step.
2. Compose `- YYYY-MM-DD — <Title> — <one-line context>` (today; no bold). An auto-detect proposal confirms `Route to User-Tasks: <title> — <domain>?`; `y, active not user-tasks` turns it into §add.
3. Insert the line at the top of its `## <Domain>` section, appending the section when it is missing, and bump `last_updated:`.
4. Report `Routed to User-Tasks.`

## complete

Run `python3 .claude/tools/worklog.py complete "<title or search>" --ref <commit, PR or session>`: the block leaves Worklog.md, its `XLINE` joins Worklog-Archive.md under its domain, and the mirror line goes. Mark an item complete in the turn its resolution lands (`worklog_reference` §Completion signals). To re-open one, §add it again with `--source "re-opened from <date> completion"`.

## sweep

Three passes over the session, each a batch of proposals the owner confirms; nothing applies unconfirmed.

1. **Add:** trigger phrases the session passed over (`worklog_reference` §Full Trigger Catalog), each cited by its turn, proposed as §add.
2. **Complete:** Active and Ledger items whose outcome shipped, judged from the commits since each item's `Source:` and from `git status --short`, proposed as §complete with the ref.
3. **Promote:** Future Scope lines whose prerequisite cleared after their `added` date, proposed as §promote with the evidence.

A proposal needs an identifier or context that ties the item to the change; a shared generic word is not enough. Record each searched commit range. An item the owner did not reach is unassessed, never declined.

## promote

Match the argument against Future Scope's lines; on several matches ask, on none suggest `show all`. A title already open in Active or Ledger asks first: the work may be logged twice. Remove the line, and insert at the top of its `## Active > ### <Domain>` a block whose title gains ` (promoted from Future Scope <today>)`, whose `Context:` is the line's parenthetical, and whose `Source:` is `promoted from Future Scope on <today>`; the `added` date stays. Remove a Future Scope domain heading or the whole callout when it empties, else update its `(N items)` count. Add the mirror line. Report `Promoted: <title> — now in Active under <Domain>.`

## unblock

Find the Ledger items whose `When: after` line contains the argument, case-insensitively; `When: future` items never match. List them and confirm, each one separately when the match is partial. For each confirmed item, move its block to the top of its `## Active > ### <Domain>` without its `When:` line, then move its mirror line to Active without the `[after: …]` suffix and correct both tier counts. Report `Unblocked <n> item(s).`
