---
name: gotcha-workflow-args-permission-control-chars
description: "Workflow tool calls carrying large multi-line agent prompts can be rejected by the permission handler ('script contains control characters') even when the tool input is valid — fall back to manual parallel Agent dispatch"
metadata: 
  node_type: memory
  type: project
  originSessionId: 0b5c3657-803b-4235-bb42-c157ccfcf355
  modified: 2026-07-27T22:36:30.590Z
retire_when:
  - review-by: 2026-11-23
---

A `Workflow` invocation whose `args` carry large multi-line agent prompts can fail with a permission-handler schema error: *"updatedInput ... script contains control characters that would be hidden in the approval dialog"*. The error text itself confirms **the model's tool input was valid** — the rejection happens in the harness's permission-rewrite layer, not the workflow engine.

**Why:** The failure looks like a malformed Workflow call and invites prompt-mangling retries that won't help (the newlines ARE the prompts). Observed 2026-07-25 dispatching `review_fanout.js` with four assembled audit-agent prompts.

**How to apply:** Don't retry with reformatted args. Prefer the **scriptPath dispatch** below; manual parallel `Agent` dispatch is the fallback when you also need session context (`fork`).

**Prevention — the shape that avoids BOTH spawn failure modes (verified 2026-07-27, 6/6 workflows spawned first try, 21/21 agents, 0 errors):** author the script to a file with `Write`, put **every agent prompt inside the script as a template literal**, and invoke `Workflow({scriptPath})` with `args` empty or tiny. Both documented failures — this permission rejection and the [[workflow-args-generation-fidelity]] 4ms/0-agent death — happen because prompt text transits a JSON tool-call payload. Script text loaded from disk never does. Corollaries:

- Prompt size stops mattering; a 12 KB multi-agent brief is as safe as a one-liner.
- Reserve `args` for short scalars (a token, a path, a count) — the values that genuinely vary per run.
- Keep the tolerant-parse guard anyway: a probe on 2026-07-27 confirmed `typeof args === 'string'` on this harness, so `const A = typeof args === 'string' ? JSON.parse(args) : (args ?? {})` is still mandatory.
- Pushing bulk CONTEXT to a scratch `.md` that agents `Read` by absolute path composes with this and additionally dodges [[gotcha_workflow_fanout_search_false_absence]].
