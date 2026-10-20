---
disable-model-invocation: true
---

# `/worklog` — SHOW, USER-SHOW, HISTORY

Call shorthands: `worklog_formats.md`. Mirror rules: `worklog_mirror.md`.

## Operation: SHOW

**Cheap path (preferred):** `Read .claude/worklog-titles.md` (SessionStart does not inject it) and print Active grouped by domain — `## <category>` headings already group it. The mirror omits Future Scope by design.

When the ready list is hard to scan or a scheduled triage found stale entries, say which items need readiness review and link `/worklog triage`. Otherwise do not print a capacity alarm.

**Full read** (user asks for context, dates, or sub-bullets): `READ(Worklog.md)`. Group by domain and list the `[ ]` items; `[x]` items live in the archive (`/worklog history`). Default view is title + class + scope + date — don't dump Context/Where/Source unless asked.

**Mirror drift-resync.** After that full read, regenerate `.claude/worklog-titles.md` from all open `[ ]` blocks in `## Active` and `## Ledger` per `worklog_mirror.md` and `Write` it. This is the ordinary drift check.

**`show all` form** — also reads Obsidian (the mirror omits Future Scope), then prints after the Active listing:
```
## Future Scope (parked, excluded from agent-facing mirror)
### <Domain>
- `<class>` · scope `<n>` · <title> (added <date>; <one-line why-deferred>)
```

On bare `show`, if a Future Scope item looks relevant to current session work (recent commits touch its area), nudge in one line: "(N items in Future Scope; run `/worklog show all` if you want to review them.)" — no auto-load.

## Operation: USER-SHOW

Opt-in read of `User-Tasks.md`.

1. **`READ(User-Tasks.md)`.** File absent → print `User-Tasks.md does not exist yet — no items have been routed to it. Use /worklog user-add or accept an auto-detect proposal to start populating it.` and stop.
2. **Print grouped by domain.** Per `## <Domain>` section, print the heading and its entries verbatim, preserving date order (newest at top). Do not reformat, sort across domains, or strip dates.

## Operation: HISTORY

Opt-in read of `Worklog-Archive.md`. Never loaded passively (no mirror, no SessionStart, no sweep/triage/drive scan). `$ARGUMENTS` is an optional domain filter.

1. **`READ(Worklog-Archive.md)`.** File absent → print `Worklog-Archive.md does not exist yet — no items have been completed into it.` and stop.
2. **Print grouped by domain.** Per `## <Domain>` section, print the heading and its `[x]` lines verbatim (newest at the bottom — append order). A domain argument fuzzy-matches the canonical list; matching nothing → list the available headings and ask.
3. **Read-only.** COMPLETE is the only writer. The archive grows unbounded; pruning is the user's, directly in Obsidian.
