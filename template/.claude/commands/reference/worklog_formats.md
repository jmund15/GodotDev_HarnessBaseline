---
disable-model-invocation: true
---

# `/worklog` — entry and mirror formats

Read at a `/worklog` write that `tools/worklog.py` does not make. `commands/worklog.md` owns the files, tiers and forms; `tools/worklog.py` parses these same formats.

## Entries

Leave out a sub-bullet that does not apply; never write a placeholder. Class and scope are required on every `BLOCK` and `FSLINE`; infer them from `worklog_reference` when the text does not give them, and report the inferred values.

- `BLOCK`, an open item in Active or Ledger:
  ```
  - [ ] **<Title>** — `<class>` · scope `<n>` · added YYYY-MM-DD
    - Context: <one line>
    - Where: <path>
    - Source: <session, PR or commit>
    - Session: <runtime session id>          (omit when Source is that session)
    - When: after <condition> | future        (omit when ready)
    - Plan doc: [[<Doc Title>]]               (scope 4 only)
    - Quick-win: flagged YYYY-MM-DD           (triage only)
  ```
- `FSLINE`, a Future Scope line inside the `> [!example]- Future Scope (N items)` callout, every line `> `-prefixed, no sub-bullets: ``> - `<class>` · scope `<n>` · <Title> (added YYYY-MM-DD; <one-line context>) · session <runtime session id>``
- `XLINE`, an archive line: ``- [x] `<class>` · scope `<n>` · <Title> (completed YYYY-MM-DD, <ref>)``
- `UTLINE`, a User-Tasks.md line: `- YYYY-MM-DD — <Title> — <one-line context>`

## Mirror

`.claude/worklog-titles.md` holds two sections, `## Active (<count>)` then `## Ledger (<count>)`, each with `### <domain>` headings and one line per open item beneath: `- <class> · <scope> · <title>`. The domain lives in the heading, never on the line. Add a heading when a domain's first item arrives and remove it when its last leaves; correct the section count on every add and removal. Then set `Last synced:` to today's date, with no narrative.

- A `When: after <condition>` item takes the suffix ` [after: <condition>]`; a `Quick-win:` item takes ` [quick-win]`; both stack in that order.
- `[x]` items and Future Scope items never appear.
- A copy you cannot patch with certainty (another edit landed, a patch failed) is rebuilt from a full read of Worklog.md.
