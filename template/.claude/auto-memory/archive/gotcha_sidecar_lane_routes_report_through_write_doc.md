---
name: gotcha_sidecar_lane_routes_report_through_write_doc
description: "A delegated author lane obeys the Documentation Delegation Rule on its own report, routes it through mcp__ai-worker__write_doc with the key as reference, hits the local worker's context cap and ends on the error — five of five lanes, 2026-09-09."
metadata: 
  node_type: memory
  type: project
  originSessionId: 415750d4-b7da-4582-b7ce-c3d371cded22
  modified: 2026-09-09T14:34:19.285Z
---

**Five sidecar author lanes (luna·high, codex) each wrote their instrument files, then tried to write their report through `mcp__ai-worker__write_doc` with a 25–50 KB key as `reference_files`, got `[ai-worker error] input too large for … 65,536-token context window … NOTHING SENT`, retried 4–8 times, and ended their run on that error** (2026-09-09, `.claude/scratch/rewrite2/records/*.out`). Two of five wrote nothing else; none registered its instrument or wrote its report. The brief said "report ≤200 words + FULL: path" and never said which tool; the full-harness child read CLAUDE.md's HARD delegation rule and applied it to its own findings file.

**Why:** the Documentation Delegation Rule names its judgment-dense carve-out, but a delegate reads "NEVER write documentation prose directly" first and a report is prose. The local worker's context window (64k tokens) cannot take a key file plus a spec, so the call can only fail, and the lane's autonomy rail ("never end on a question") does not cover "never end on a tool error".

**How to apply:** every author-lane brief and the author guard rail (`guards/author.md` §strict) say the report is authored with `Write`, never `write_doc`/`read_files`. Resume a lane that died this way with `-r <session-id>` and a brief that names the tool; the work already on disk survives. When re-dispatching is gated (codex Hot), finish the mechanical remainder (registration, re-judge lists, manifest paragraph) from the orchestrator — the lane's key files are usable as written.

Related: [[feedback_documentation_delegation_rule]]
