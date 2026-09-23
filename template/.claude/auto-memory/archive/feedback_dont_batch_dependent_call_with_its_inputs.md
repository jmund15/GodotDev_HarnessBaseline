---
name: dont-batch-dependent-call-with-its-inputs
description: Never put a tool call in the same batch as the calls that produce its arguments (e.g. a Workflow launch alongside the inventory whose results form its CONTEXT). Dependent ≠ independent — split into sequential turns.
metadata: 
  node_type: memory
  type: feedback
  originSessionId: dbb84ead-c527-47a7-97b3-4191277af230
---

The harness encourages batching **independent** tool calls in one turn. A call whose ARGUMENTS are derived from a sibling call's OUTPUT is **not** independent — batching them forces you to pass guessed/fabricated arguments before the producing call's results have returned.

**Why:** In a `/doc_architecture_audit` run I placed the `Workflow(...)` launch (Phase 2) in the same tool batch as the Phase-1 inventory `find`/`Glob`. The workflow's CONTEXT manifest is built FROM those inventory results, so I passed a fabricated 6-system manifest invented from memory — and the workflow audited systems that don't exist. Had to kill it and relaunch with the real structure. A second, smaller version of the same trap: do not pre-judge a file's contents (corruption, structure) from a guess before the read returns.

**Second instance (same session, different shape):** I batched a `Write` of the audit report with the `Read` of the existing report's Changelog whose rows the Write must re-emit verbatim. The Write executed against my *stale, memory-reconstructed* changelog (the Read returned in the same batch, too late), fabricating 3 of 16 prior rows. So the rule is NOT Workflow-specific — it covers any `Write`/`Edit` whose body copies content from a `Read`/`Grep` issued in the same batch. **Verbatim-preservation writes (changelog re-emit, append-only logs, "keep all prior rows") are the highest-risk shape** because the fabrication is silent and looks plausible.

**How to apply:** Before composing a multi-call batch, ask *"does any call here consume another call's result?"* If yes, split: gather inputs → SEE the results → then issue the dependent call. Two shapes to watch: (1) any agent/Workflow launch taking a Claude-assembled CONTEXT/manifest — the "Claude assembles → workflow consumes" command shape (`/doc_architecture_audit`, per-domain doc generators, review fan-outs); (2) any `Write`/`Edit` that must reproduce content from a same-batch `Read` (report re-generation preserving a changelog, doc edits quoting existing text). A runtime guard now lives in `doc_architecture_audit.md` Phase 2. Related: [[workflow-integration-mechanics]], [[onedrive-dehydration-breaks-vault-reads]], [[workflow-fanout-search-false-absence]].
