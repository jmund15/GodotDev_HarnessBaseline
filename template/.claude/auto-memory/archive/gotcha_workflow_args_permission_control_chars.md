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

A `Workflow` invocation can fail with a permission-handler schema error: *"updatedInput ... script contains control characters that would be hidden in the approval dialog"*. The error text itself confirms **the model's tool input was valid** — the rejection happens in the harness's permission-rewrite layer, not the workflow engine.

**Root cause (settled 2026-08-30): the control characters are `\r` — CRLF line endings in whatever the approval dialog inlines.** The dialog resolves `scriptPath` to the script's file content for display and returns it as `updatedInput.script`; a CRLF script file then fails validation on EVERY invocation, however small the `args` (observed: two tiny-args `scriptPath` calls rejected identically). The 2026-07-25 large-multi-line-`args` case was the same `\r` class arriving via pasted prompt text. `\n` alone passes — it is displayable, `\r` is "hidden".

**Fix (applied 2026-08-30):** normalize `.claude/workflows/*.js` to LF (`sed -i 's/\r$//'`) and commit — the repo's `.gitattributes` already mandates `* text=auto eol=lf`, but blobs committed before normalization keep their CRLF on checkout until a renormalizing commit lands. Verify any NEW workflow script with `file <path>` (no "CRLF" in output) before first dispatch.

**How to apply on hitting the error:** check the script file's line endings FIRST (`file <path>`); convert and re-invoke. Don't retry with reformatted args — the newlines are not the problem, the carriage returns are. Manual parallel `Agent` dispatch remains the last-resort fallback (it loses per-agent effort pins).

**2026-09-01 — the scriptPath shape fails the same way when the harness's auto-mode approval dialog has to render the script.** Measured: `Workflow({scriptPath, args:{small scalars}})` against a script file with zero control characters (every code point scanned) was rejected twice with this exact error; none of the project's PreToolUse hooks emitted `updatedInput` (probed `model_pin_translate.py` directly — no output on an Anthropic session). The rewrite is the approval dialog expanding the script file into a `script` field and then failing its own validation on the multi-line text. The durable fix is a `"Workflow"` entry (and `"Agent"`) in `permissions.allow` of `.claude/settings.json`, which the user adds — an agent cannot grant itself that rule (the classifier denies the edit). Both entries are now in the allow block; where a session lacks them, fall back to parallel `Agent` dispatch and record that effort pins were inherited.

**Prevention — the shape that avoids BOTH spawn failure modes (verified 2026-07-27, 6/6 workflows spawned first try, 21/21 agents, 0 errors):** author the script to a file with `Write`, put **every agent prompt inside the script as a template literal**, and invoke `Workflow({scriptPath})` with `args` empty or tiny. Both documented failures — this permission rejection and the [[workflow-args-generation-fidelity]] 4ms/0-agent death — happen because prompt text transits a JSON tool-call payload. Script text loaded from disk never does. Corollaries:

- Prompt size stops mattering; a 12 KB multi-agent brief is as safe as a one-liner.
- Reserve `args` for short scalars (a token, a path, a count) — the values that genuinely vary per run.
- Keep the tolerant-parse guard anyway: a probe on 2026-07-27 confirmed `typeof args === 'string'` on this harness, so `const A = typeof args === 'string' ? JSON.parse(args) : (args ?? {})` is still mandatory.
- Pushing bulk CONTEXT to a scratch `.md` that agents `Read` by absolute path composes with this and additionally dodges [[gotcha_workflow_fanout_search_false_absence]].
