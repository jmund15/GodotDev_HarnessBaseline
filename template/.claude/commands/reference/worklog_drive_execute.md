---
disable-model-invocation: true
---

# `/worklog` — DRIVE recipe: execute the fill-set

Read at DRIVE Step 5, when 4g proceeds. Selection and the plan body live in `worklog_drive.md`; per-item close-out runs `worklog_complete.md`. Stages, halts, slices and close follow [`drive_spine.md`](../../skills/_brainstorm_shared/drive_spine.md).

### Step 5 — Execute the fill-set

Group the fill-set and drive each item at the depth [`execution_depth.md`](../../skills/_brainstorm_shared/execution_depth.md) §Admission table assigns.

- **Dispatch and slices** follow the spine's §Slices; depth governs process artifacts, not who executes (`execution_depth.md` §Boundary).
- **Gates** follow `change_control` §Gate cadence, provenance-blind across the batch.
- **Per-item close-out** is the spine's §Close write-back: run COMPLETE with the commit ref, in the session the item lands.

**Re-scope valve.** Trigger: the diff left the item's declared scope, which is a re-triage trigger under [`execution_depth.md`](../../skills/_brainstorm_shared/execution_depth.md) §Re-triage. Action: update the item's `scope:` value in `Worklog.md` (ADD-style edit), leave it `[ ]`, and report the re-scope. Budget mode continues at the re-triaged depth only if the remaining user-supplied scope budget covers the new value; otherwise the item stays re-scoped-but-undriven. Named-items mode continues at the re-triaged depth unless the new scope value is 4 → flag and route per the scope-4 rule.

**Done-condition.** Every fill-set item ends in exactly one terminal state: COMPLETEd with a ref, or left `[ ]` with a re-scope note. The closing report lists all items with their state — an unlisted item means the drive is not done.
