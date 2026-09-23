---
disable-model-invocation: true
---

# `/worklog` — DRIVE recipe: execute the fill-set

Read at DRIVE Step 5, once 4g proceeds. Selection, scoring and the plan body live in `worklog_drive.md`; per-item close-out runs `worklog_complete.md`.

### Step 5 — Execute the fill-set

Group the fill-set and drive each item at its ladder tier ([`execution_depth.md`](../../skills/_brainstorm_shared/execution_depth.md)).

- **Dispatch shape** per the `orchestration` skill §5/§11 — the depth ladder governs process artifacts, not who executes. Independent items may fan out via the generic engines (`dispatch.js`) with explicit model + effort pins; dependent or same-file items serialize. **Test and build runs are single-flight:** one gate, serially, orchestrator-side, never per-agent. Pass `agentType: "general-purpose"` with the resolved `model` and `effort`.
- **Gates are provenance-blind.** Any gated change anywhere in the batch → one full run of the project's regression gate (`change_control` §Gate cadence names it) before commits, however small the items.
- **Per-item close-out:** run COMPLETE with the commit ref, in the same session the item lands.

**Re-scope valve.** Trigger: the item's real file or decision count exceeds its logged scope tier mid-drive. Action: update the item's `scope:` value in `Worklog.md` (ADD-style edit), leave it `[ ]`, and report the re-scope. Budget mode continues into the deeper tier only if the remaining scope-point budget covers the new value; otherwise the item stays re-scoped-but-undriven. Named-items mode continues at the deeper tier unless the new tier is 4 → flag and route per the scope-4 rule.

**Done-condition.** Every fill-set item ends in exactly one terminal state: COMPLETEd with a ref, or left `[ ]` with a re-scope note. The closing report lists all items with their state — an unlisted item means the drive is not done.
