# Guard: author — delegates that write production code

Read ONLY the section your dispatch names; if none is named, read `## detailed`. `any.md`'s section of the same tier binds you too and arrives with this file. Every line cites the home that owns it; the home is authoritative if this summary and it ever disagree.

## detailed

- Code changes take the project's regression gate (`change_control` §Gate cadence names it) before commit. Under the concurrency guard you must NOT run it — finish the work and report that the gate is owed. [change_control §Gate cadence]
- Comments: default NONE. Add one only where deleting it would lead a maintainer to a wrong decision. [CLAUDE.core.md §Core Code Conventions]
- Do NOT reduce planned scope mid-execution. If the scope looks wrong, stop and report it — the cut is not yours to make. [feedback_dont_unilaterally_reduce_planned_scope.md]
- Your report, digest or findings file is a judgment-dense artifact YOU author: `Write` it directly. Never route it, a key, or any file over ~60 KB through `mcp__ai-worker__write_doc` / `read_files` — the local worker's context window rejects it and a lane that ends on that error has delivered nothing. [CLAUDE.md §Tool Routing, judgment-dense carve-out; gotcha_sidecar_lane_routes_report_through_write_doc]

## condensed

- The project's regression gate before commit (`change_control` §Gate cadence names it); do not run the gate under the concurrency guard — report it owed.
- Comments default to none (CLAUDE.core.md §Core Code Conventions). Planned scope is not yours to cut (`feedback_dont_unilaterally_reduce_planned_scope.md`).

## minimal

Read this file's `## condensed` section.
