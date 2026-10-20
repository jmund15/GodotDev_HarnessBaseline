# Phase 2 — Reproduce + Investigate

Read at Phase 2 of [`../SKILL.md`](../SKILL.md).

Run the loop, watch the bug appear, then gather evidence before forming any hypothesis.

## Confirm the reproduction

- [ ] The loop produces the failure mode the **user** described, not a nearby one. Wrong bug = wrong fix.
- [ ] The failure reproduces across multiple runs (or at a high enough rate to debug against).
- [ ] You captured the exact symptom (exact logged error message, orphan count, assertion failure, frame timing) so later phases can verify the fix addresses *this*.

Do not proceed until you reproduce the bug.

## Minimise until every element is load-bearing

Shrink the repro (fewer components, entities, frames, data fields, a plainer fixture scene), removing one element at a time and re-running. Stop when removing anything makes the bug vanish: every survivor is load-bearing, and that set IS the evidence Phases 3–4 reason over. Keep the original scenario: Phase 5 re-runs the fix against it.

## Read errors carefully

- Stack traces: read from the inner-most frame outward.
- Logged errors: treat as first-class evidence, and check whether the project's test runner fails on them.
- Build errors: cite the exact `file:line:error-code`. Don't paraphrase.

## Reproduce consistently

**Rule:** Intermittent reproduction *is* the bug: race condition, init order, frame timing. Don't "fix" the symptom; investigate the determinism gap.

## Check recent changes

**Rule:** Before forming hypotheses, know what changed near the failure: read the history and diff around it, and record the rung that reading supports. Derive the range from the failure; a literal revision offset (`HEAD~1`) can name a peer session's commit.

## Gather evidence in multi-component systems

**Intent:** find which boundary the failing value crosses wrongly. **Done:** every boundary the data crosses emits a state-change line and the log shows which transition disagrees. **Evidence:** the live runtime output or the post-run log.

**The Instrument → Run → Verify loop:**

1. **Instrument:** a Debug-level log at every boundary the data crosses, through the project's logger, tagged `[<Subsystem>][DIAG-<id>]`. The tag composition and level rules live in the project's logging rule, where it ships one. Log STATE CHANGES, not state, and enable the Debug level or nothing is emitted.
2. **Run:** run the app (automated), or ask the user for a manual repro with concrete steps. Don't tell the user to test while you spin up the app; they are often working on other tasks.
3. **Verify:** read the runtime output while running, or the post-run log.

**Verify authored configuration first.** Before instrumenting, read the entity's authored configuration (scene, config file, container registration) to confirm which components and strategies are actually wired. A code-expected vs scene-actual mismatch is often itself the bug.

**Boundary chain:** write the flow's chain before instrumenting (e.g. `Input → Controller → DomainCore → Behavior → Output`). Each arrow is one `[<Subsystem>][DIAG-<id>] <state-change>` log call. The bug lives at whichever boundary the log message disagrees with the next one. A log worth keeping may be commented out, except a `[DIAG-` line: Phase 6's cleanup grep matches a commented one, so delete it.

## Trace data flow backward

**Rule:** When the visible symptom is N layers downstream of the cause, walk backward one layer at a time. Don't fix at the symptom layer until you've identified the cause; a symptom-layer fix masks contention instead of resolving it.

**Worked example:** the symptom *"the player washes white after a hit"*. The hit-flash component correctly set the tint, but a per-frame visual controller overwrote it: its transient-effect reset stomped the tint with a bare base color, wiping persistent tints another service had registered. The trace ran *wash* → *reset* → *effective-color computation* → *service layering*; re-applying the tint after every hit would have masked the contention.

*"Why is X using Y?"* is a retire-Y signal. Compatibility flags, axis-restricted geometry and approximations of engine primitives are all symptom-fix tells.
