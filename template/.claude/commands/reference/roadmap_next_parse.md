# Phase 2 — Parse Parts tables (bundled)

Per the user `CLAUDE.md` *Local worker* — 3+ files for synthesis → route through `mcp__ai-worker__read_files` (offline fallback: a Haiku subagent per `common.md §3`):

```
read_files(
  paths=[<each roadmap.md absolute path>],
  question="For each roadmap, extract every Parts table row as a structured record:
            {roadmap, tree (project | framework tree name — infer from the path), pos, part_name, state,
             deps (parsed to list), trigger (TRUNCATED to first 400 chars),
             source (TRUNCATED to first 200 chars)}.
            Also extract any 'Cross-roadmap references' section listing this roadmap's
            Parts named as Deps by other roadmaps.
            Also, if a '## MVP Checkpoints' section exists, extract per MVP:
            {mvp_id, status (the Status line: In progress / Ready for playtest / Verified),
             required_parts (the Required-Parts checkbox list — for each, the linked Part
             name from [[#Parts|<Part Name>]] and whether it is checked [x] or unchecked [ ];
             flag cross-roadmap clauses, which have no clean #Parts link)}.
            If no MVP Checkpoints section, return mvps: [] for that roadmap.
            You MUST return one entry per input path (N total). Do NOT silently
            omit any file. If a roadmap can't be parsed, include an entry with a
            'reason' field rather than omitting it.
            Return JSON-shaped output."
)
```

**Output-shape verification (MANDATORY post-call):** count the returned entries against the input `paths` list (the worker may return a bare array or `{roadmaps:[...]}` — count whichever). If `len(returned) < len(paths)` (empirically observed 2026-05-19 — worker returned 3 of 4 roadmaps on a long-context bundled call), enumerate which roadmap paths are missing and retry them via individual `Read` calls. Do NOT proceed to Phase 3 with partial data — an incomplete leverage graph silently misranks recommendations (a missing roadmap's cross-roadmap dep edges vanish, leaf-shaped Parts get over-promoted, downstream-blocking Parts get under-promoted). The check is mechanical: 1 if-statement; not a judgment call.

**Output-cap guard:** the `trigger`/`source` truncation above is load-bearing, not cosmetic. A full-verbatim extraction across the now-12-roadmap surface overflows the worker's output token cap (observed: 116 KB on 8 project roadmaps *alone*, before any framework tree). The 400/200-char caps preserve all readiness signal (concrete-type names that drive `arch-*` Readiness appear early in a trigger; `plan-pending` API/Test-Pin Readiness is confirmed by reading the design doc in Phase 5, never from the trigger field). If the call *still* overflows, it saves output to a file and returns a path — either re-issue split per-tree batches (project paths, then each framework tree's paths) and merge, or parse the saved JSON from disk (e.g. via PowerShell `ConvertFrom-Json`) rather than re-running.
