---
disable-model-invocation: true
---

# /codify — the two mechanizable surfaces

Read at `/codify` Step 3, after `reference/mechanism_litmus.md`. Check the surfaces in order.

- **A tool boundary.** Can a `PreToolUse`/`PostToolUse` matcher *decide* it from a command shape, file path, tool name or live state, never from a statement the agent writes into the call? The matcher reads the call that makes Step 0's choice (the edit that writes the file, the plan or brief that orders the run), never a later call that only meets its result (the commit, the delegate's run); a later check is a backstop only when that call cannot be decided, and the report names why. Then it routes to a hook **plus one documented home** (`instruction_quality` §3) that the agent reads before the action the hook blocks. A rule that would reach the agent only through the hook's own deny makes the hook its teacher: land the home at the decision point first, or add no hook. A rule whose only home is a hook file is invisible to doctrine and exempt from `/rule_consistency`; a rule restated beyond that one home adds hesitation.
- **A gate over an artifact.** Can a script or a read-only lens *decide* it from a diff, a plan file, a PR body, or the repo state at gate time? Then it routes to the gate that already runs there — a lint in the project's regression gate (`change_control` §Gate cadence names it), a Claude-side grep or lens in the coding layer's pre-PR battery or plan-review panel, or a registry template in `agents/*_agents.md` (via `tools/lens.py`) — and the command that runs the gate is its documented home.
