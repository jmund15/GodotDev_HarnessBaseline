---
name: workflow-single-flight-concurrency
description: Workflow parallel()/pipeline() agents must not each run GdUnit4 tests or fan out csharp-ls LSP calls — both are single-flight and wedge under concurrency.
metadata: 
  node_type: memory
  type: reference
  originSessionId: 83488c5b-b5a5-4640-ba5f-8485f22ab71b
  modified: 2026-07-25T00:31:19.031Z
---

Dynamic Workflow `parallel()`/`pipeline()` fan-out runs N subagents concurrently (cap `min(16, cores-2)`, overflow queued). Two {{PROJECT_NAME}} resources are SINGLE-FLIGHT and will wedge if multiple concurrent agents touch them:

- **GdUnit4 Godot named-pipe + Godot process concurrency** — ONE test host at a time, machine-wide. N agents each shelling `dotnet test` / invoking the regression gate = N test hosts → pipe wedge + orphan accumulation (the ~2h-loss crash class in `GdUnit4_Process_Management`). Test EXECUTION must serialize: run the gate ONCE, serially, in the orchestrator (Claude) OUTSIDE any `parallel()` barrier — never inside a fanned agent. (2026-07-24: the pipe is now salted per worktree — but concurrency is STILL machine-single-flight one layer down: concurrent Godot test instances crash CLR `0xc000001d`, verified 4/4, so `isolation: "worktree"` does NOT license parallel suite runs either.) Investigation and fix-authorship may fan out; running tests may not.
- **csharp-ls LSP wrapper** — single-flight (`CSharp_LSP_Runtime_Gotchas`); concurrent `findReferences`/`documentSymbol` wedge it until a full Claude Code restart. Pre-compute the symbol map ONCE orchestrator-side BEFORE the fan-out and pass results into agents as CONTEXT/args. Forbid LSP in fanned agent prompts; instruct Grep/Read only.

DISTINCT from the existing "LSP unavailable on cloud" carve-out (that is availability; this is single-flight-under-concurrency). The latent trap surfaced in the harness-workflow audit: a per-PR review fan-out where each agent runs the project's PR-review command, whose test phase invokes the regression gate → N concurrent gates. Bake "read-only, do NOT run the regression gate" into the fanned agent prompt; the gate runs once above the barrier.

**Guard reaches only the top-level fanned agent — nested fan-out escapes it.** The `review_fanout.js` GUARD is a string *appended to the dispatched agent's prompt*. If that agent itself spawns sub-agents (e.g. each per-PR agent running a PR-review command that spawns 4–7 sub-agents), those sub-sub-agents receive their OWN prompts without the guard — so "no regression gate, no language server" silently lapses one level down. Rule: do NOT route a *nested* fan-out command through the generic engine; keep it Claude-orchestrated. Also: the engine's global `file:line` dedup is wrong wherever same-location hits are signal (cross-PR edits = conflicts, not duplicates) — a second reason a bespoke or keep-as-is shape beats the engine there.

Workflow scripts also have NO filesystem access — file reads happen inside agents or via `args`. See [[workflow-integration-mechanics]].
