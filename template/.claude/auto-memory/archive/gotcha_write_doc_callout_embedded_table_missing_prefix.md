---
name: gotcha_write_doc_callout_embedded_table_missing_prefix
description: "write_doc (obsidian modifier) emits markdown tables INSIDE callouts without the leading '> ' prefix, breaking them out of the collapsible in Obsidian's renderer."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 85a74a34-48af-461e-8ad0-89df2dec9535
---

When `mcp__ai-worker__write_doc` (with the `obsidian` modifier) generates a markdown table nested inside a callout block (`> [!info]-`, `> [!example]`, etc.), the table rows are frequently emitted at **column 0 with no leading `> ` prefix**. In Obsidian's renderer this terminates the callout early — the table renders OUTSIDE/below the collapsible instead of within it.

**Why it matters:** the `/doc_full` doc suite (doc_usage / doc_architecture) puts Configuration-Reference tables and component-deep-dive tables inside collapsible callouts. A silently de-nested table looks fine in raw markdown but renders wrong in the vault.

**How to apply:** after any `write_doc` call that nests tables in callouts, verify (or post-pass) that every table row carries the `> ` prefix. A deterministic fix is a scripted re-prefix of table lines that sit between callout markers (exclude tables under plain `##` headings like Changelog). Observed 2026-07: one Floors Designer Usage run needed 63 rows re-prefixed. Intermittent (model-run-dependent) — not every run trips it, so verify rather than assume. Related doc-tooling rule: [[feedback_delegate_output_trust]].
