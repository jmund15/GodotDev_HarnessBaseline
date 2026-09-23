---
disable-model-invocation: true
---

# `/worklog` — SHOW, USER-SHOW, HISTORY

Call shorthands: `worklog_formats.md`. Mirror rules: `worklog_mirror.md`.

## Operation: SHOW

**Cheap path (preferred):** `Read .claude/worklog-titles.md` (SessionStart does not inject it) and print Active grouped by domain — `## <category>` headings already group it. The mirror omits Future Scope by design.

**Capacity check (preface every show output):** count `[ ]` lines in `## Active` only — the Ledger is uncapped by design. If > 30, prepend:
```
⚠ Active has <N> items (ceiling: 30). Run `/worklog triage` to rebalance against `## Ledger`.
```
At ≤ 30, omit it entirely. Fires on `show` / `show ledger` / `show all` only, never during `add` / `complete` / `drive` / `triage`. Soft alarm — never blocking.

**Full read** (user asks for context, dates, or sub-bullets): `READ(Worklog.md)`. Group by domain and list the `[ ]` items; `[x]` items live in the archive (`/worklog history`). Default view is title + class + scope + date — don't dump Context/Where/Source unless asked.

**Mirror drift-resync (the full-regen escape hatch).** After that full read, regenerate `.claude/worklog-titles.md` from the `## Active > [ ]` items per `worklog_mirror.md` and `Write` it. This is the only place the mirror is rebuilt from scratch.

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
3. **Read-only.** COMPLETE is the only writer. The archive grows unbounded; pruning is the user's, directly in Obsidian.
