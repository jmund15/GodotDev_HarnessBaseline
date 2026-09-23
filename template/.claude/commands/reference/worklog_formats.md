---
disable-model-invocation: true
---

# `/worklog` — call shorthands and entry formats

`commands/worklog.md` owns the Forms table, the tier model and the cross-cutting rules; mirror-patch mechanics live in `worklog_mirror.md`.

**Call shorthands** used below — expand literally when invoking. `<Doc>` is `Worklog.md` / `Worklog-Archive.md` / `User-Tasks.md`; `P` = `DevProjects/{{PROJECT_NAME}}/Claude/TODO/<Doc>` (vault-relative, resolved under the vault root).
- `SR(<Doc>, search, replace)` = `Edit(P, old_string=search, new_string=replace)`
- `READ(<Doc>)` = `Read(P)`
- `FM(<Doc>)` = `Edit(P, old_string="last_updated: <current date>", new_string="last_updated: YYYY-MM-DD")` — bump the frontmatter `last_updated:` line
- `APPEND(<Doc>, content)` = `Edit(P, old_string=<last line before the insertion point>, new_string=<same line> + content)` — append at the stated anchor

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

**Class + scope are required** on every `BLOCK` and every `FSLINE`. If the `add` text doesn't make them obvious, infer them (`worklog_reference` classification) and write; the report names the inferred values.
