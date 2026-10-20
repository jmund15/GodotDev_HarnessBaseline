---
disable-model-invocation: true
---

# `/worklog` — mirror maintenance

- **Mirror maintenance:** `.claude/worklog-titles.md` is not auto-injected — `Read` it first if you haven't this session. Patch it from the current verified Active block after a local edit, then `Write` it back. If the copy is stale or cannot account for a concurrent change, reread Worklog and rebuild the mirror.
  - **ADD** (Active path) → append the line you just composed.
  - **COMPLETE** / triage-`delete` / triage-`promote` (Active→Future Scope) → remove that item's line.
  - **UNBLOCK** → move each matched line from Ledger to Active, remove its ` [after: <condition>]` suffix, and update both tier counts.
  - **PROMOTE** (Future Scope→Active) → add the reconstructed line.
  - **TRIAGE** → rebuild once at end-of-walk from the complete Active-and-Ledger copy read in its Step 1.
  - Future Scope adds/removes do NOT touch the mirror.
  Then set `Last synced:` to today's **date only** — never a change narrative; the mirror is always-loaded context.
  File shape: two `## <tier> (<count>)` supersections — `## Active` then `## Ledger` — each holding `### <category>` headings, then `- <class> · <scope> · <title>` lines beneath. The category lives in the heading, never per line. Add a heading when a category first appears; remove it when its last item leaves. Patch the count in the supersection heading on every add/remove.
  SHOW's full-read path is the ordinary drift check. TRIAGE can rebuild after its full Active read. If another local edit or failed patch makes the mirror known stale, reread Worklog and regenerate it.
  Line-format filter rules (composing or regenerating a line):
  - `[ ]` items in `## Active` and `## Ledger` → included in their respective tiers.
  - `[x]` items live in `Worklog-Archive.md`, never in the mirror.
  - Items in `## Future Scope` → **excluded**.
  - `When: after <condition>` → ` [after: <condition>]` suffix after the title.
  - `Quick-win:` sub-bullet → ` [quick-win]` suffix after the title.
  - Both → suffixes stack, stable order `... [after: <condition>] [quick-win]`.
  - Neither → plain `<category> · <class> · <scope> · <title>`.
