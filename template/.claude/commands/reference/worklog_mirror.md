---
disable-model-invocation: true
---

# `/worklog` — mirror maintenance

- **Mirror maintenance (incremental — do NOT re-read the source):** `.claude/worklog-titles.md` is not auto-injected — `Read` it first if you haven't this session. After an Active-section write, patch the affected line(s) and `Write` back:
  - **ADD** (Active path) → append the line you just composed.
  - **COMPLETE** / triage-`delete` / triage-`promote` (Active→Future Scope) → remove that item's line.
  - **UNBLOCK** → strip the ` [after: <condition>]` suffix from matched lines.
  - **PROMOTE** (Future Scope→Active) → add the reconstructed line.
  - **TRIAGE** → rebuild once at end-of-walk from the full-Active copy read in its Step 1.
  - Future Scope adds/removes do NOT touch the mirror.
  Then set `Last synced:` to today's **date only** — never a change narrative; the mirror is always-loaded context.
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
