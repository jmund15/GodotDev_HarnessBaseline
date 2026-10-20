# Probe verification

The detail of `SKILL.md` Probe flow step 4b. The bullets below list what an observed run must show; `SKILL.md` states the rule they serve.

## 4b. Prove it runs before handing it over

- a `RunSelfCheckOnReady`-style export (default OFF) exercising **every structural variant**, not just the authored one;
- an input-driven probe feeds a REAL input event through the pipeline, never a direct component call: `Input.ParseInputEvent(new InputEventKey(...))` against the actual InputMap action; a component self-check cannot observe HSM transition legs;
- it emits DATA per variant (counts, hit/miss, degenerate-value checks), not prose, so the verdict is machine-readable in `get_debug_output`;
- the whole body is exception-guarded;
- the agent runs it and reads the output. `dotnet build` + `godot --headless --import` show only that code compiles and a class registers.

**Verification obligations — set by the QUESTION, not the probe's size.** An open design fork is design = open and a multi-mechanism answer is oracle = runtime (`execution_depth.md` §Fields); the bar above suffices for a question with neither. A question with either adds these obligations, because per-seam drivers can each be green while the composed encounter is broken: failures live BETWEEN mechanisms (spawn spread vs sensor reach, one system's frame order vs another's):

- a **deterministic `ISceneRunner` driver per seam**: throwaway, under the integration suite's prototype folder, deleted at parking. Reproduce the authored preconditions of the claimed scenario, including its group size and acquisition wait, on the real playtest environment rather than a synthetic stand-in. Pin the seam by asserting authored state directly: authored children, condition false in the open, raycast mask, spec pair, collision layer.
- an **encounter driver**: one driver over the REAL authored playtest host (actual entity scenes, real spawn layout), driving input and asserting playtest-visible OUTCOMES, not mechanism states ("punch 1 leaves the attached rider alive, punch 2 kills"). Constants a playtest round produced get their intent asserted here;
- an **agent-observed playtest before handover**: drive the real authored host (MCP boot with driven input, or the real encounter root + roster + layout under an `ISceneRunner`) and read the playtest-visible state out of the log. The handover note says what was observed; a boot to the menu observes nothing, and "suites green" is a report about mechanisms;
- a **filtered adjacent-suite run** (`dotnet test --filter` over suites whose domain overlaps the probe): a probe can turn a shipped suite red (submodule moves, seam edits) and hand it over broken;
- a **correctness-gap pass**: one independent read of QUESTION.md + the drivers + the host scene, hunting playtest-observable paths no driver covers, and auditing the DRIVERS' own premises against the authored design. Findings are obligations: every uncovered path gets driven, or the handover note names it owner-verification WITH the reason. A gap the round's own fix opened (a widened sensor reaching a neighbor encounter, a retuned constant's untested tail) may not be deferred at all: it gets a driver. A finding implicating a framework seam (Jmodot) is fixed in the framework (paired submodule branch, pushed before the parent pointer), never worked around in the scene.

**A bare `run_project` does not satisfy this.** When `run/main_scene` is a lifecycle or menu scene, a plain run lands in that flow: the tree is real, the gameplay context is not, and anything that raycasts, collides, or reads run state behaves differently. Pass `run_project`'s optional `scene` parameter, targeting a scene that hosts the mechanism, with a linked worktree as `projectPath` (`rules/godot_files.md` §MCP tools).

| What the probe needs in order to execute at all | Route |
|---|---|
| Nothing beyond its own node | isolated `prototypes/<slug>/prototype.tscn` + `run_project(scene:)` |
| World geometry to query (raycast, collision, overlap) | isolated scene CONTAINING that geometry |
| A real subsystem to drive it (spawn pipeline, HSM transitions, damage flow) | `ISceneRunner` harness: the only route with deterministic ticks and real wiring |
| Cross-system emergence (several systems interacting) | the real gameplay scene; self-check reports data, human judges |

Row 3 is the carve-out to the auto-stop tripwire, which targets tests as DELIVERABLE, not as instrument. An isolated `prototype.tscn` is often warranted ALONGSIDE the real-scene host: isolation makes the self-check meaningful, the real host is the venue for the feel judgment.
