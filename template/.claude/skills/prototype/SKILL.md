---
name: Prototype
description: >-
  Use for one of two routes. PROBE: a design fork that turns on how something reads, behaves,
  or responds at runtime and the question is still OPEN — throwaway variants on a
  `prototype/<slug>` branch, answer-only. PROVISIONAL: a CLOSED design deliberately shipped
  minimal because full implementation is deferred (named roadmap Part or explicit user
  trigger) — inline, totality-tracked via `// PROVISIONAL(<slug>)` + `.claude/prototype_registry.md`.
  Converged and not deferred → `/part_drive` or `/feature_drive`. SKIP for prototype-grade
  ASSET authoring (`sprite_authoring` / `shader_authoring`) and constant-tuning a shipped system.
---

# Prototype

> **`prototypes/`** (lowercase, plural, repo root) is this skill's throwaway home. A same-named directory elsewhere in the project may be live production code governed by its own rule file — check before writing, and never write prototype code there.

## Mode fork

First decision:

| Signal | Mode | Where | Deliverable |
|---|---|---|---|
| Question OPEN — a fork turns on how something reads, behaves, or responds at runtime | **PROBE** | `prototype/<slug>` branch; merges only after conversion (§Conversion) | the ANSWER (`ANSWER.md`); the code is throwaway |
| Question CLOSED — full implementation deferred (bandwidth or dependency) | **PROVISIONAL** | the current branch (main / feature / worktree), deliberately shipped | minimal tracked surface + a `.claude/prototype_registry.md` entry |

Provisional carries full production obligations — TDD strict in the Logic domain, `/regression_gate` on `.cs` commits — because it is a disposition (architecture deferred), never a quality carve-out.

## Containment

Both modes. Provisional code shipping from `prototypes/<slug>/` uses the two layers below via its registry row; provisional code in production folders is tracked by markers + registry row only (`## PROVISIONAL`).

1. **`main` compiles a prototype slug only where a line names it — `.cs` only.** `{{PROJECT_NAME}}.csproj` permanently carries the blanket exclusion (`<Compile Remove="prototypes\**\*.cs" />` + `<None Include="prototypes\**\*.cs" />`), so an UNREGISTERED prototype `.cs` on `main` cannot compile and so cannot have production callers. Never delete the blanket Remove. A slug that ships provisionally opts back in with its own `<Compile Include="prototypes\<slug>\**\*.cs" />` — one reviewable line per slug, never a widened glob. An opted-in slug is fully live and CAN have production callers; those callers are its retirement cost and go in its registry Surface column. Scenes are not compiled, so a `prototypes/**/*.tscn` or `.tres` on `main` is fully live regardless, and registration there is by directory placement.
2. **`main` cannot commit a prototype file whose slug is not registered, whatever its extension.** `.claude/hooks/prototype_containment_guard.py` (PreToolUse on `Bash`) denies a `git commit` staging a path under `prototypes/` while HEAD is not a `prototype/*` branch, unless that slug carries a non-`absorbed` row in `.claude/prototype_registry.md`. Default-deny: an unreadable or absent registry allowlists nothing. It fails open by design (detached HEAD, a failing `git` call, nothing staged), and a commit made outside the `Bash` tool — PowerShell, IDE, Godot editor — bypasses it.
3. **Work happens on `prototype/<slug>`.** That branch's FIRST commit adds the slug's `<Compile Include>` line, so the prototype compiles and runs there. A branch carrying no registered slug is never merged — park it and leave a pointer. A branch whose slugs have CONVERTED to provisional surfaces merges normally (§Conversion).

**A slug that ships stays at `prototypes/<slug>/`.** Promotion does not relocate it: the path is the free signal that the code is still provisional, and both layers key off the registry row, not the location.

*Consequence of 1 + 2:* a REGISTERED slug's `.cs` gates like any other. Never claim a `/regression_gate` carve-out for a prototype.

## Probe flow

### 1. State the question

Write `prototypes/<slug>/QUESTION.md`: the question, plus **the shape of its answer** — "a number", "one of three enum values", "yes/no". A prototype whose answer shape you cannot state is not yet scoped; sharpen it before building.

The file carries two inventories that set the verification tier (§4b):

- **Design forks** — every semantic choice the design does not settle ("does cower only fire when cornered?"). Ask each via `AskUserQuestion` at question time; a guessed fork is a shipped bug.
- **Seams** — every production surface the probe drives (a shed call, an HSM transition evaluation, a sensor, an emitter wiring) **and the host scene's authored wiring** (spawn layout vs sensor reach, scene-authored values, the frame order two driven surfaces meet in). The seam list IS the verification scope; a probe naming no seams needs none.

### 2. Pick the branch and the host

| Branch | Question it answers | Examples |
|---|---|---|
| **MODEL** | Does this model produce sane outcomes? | synergy resolution shapes, an HSM transition graph with reachable dead states, data-model expressiveness |
| **FEEL** | Does this read and respond right? | movement, camera, input response, timing, telegraph readability, juice |

Both branches are **C#** — Godot forbids a GDScript file inheriting from a C# script, so a GDScript prototype cannot extend the component/state classes it must integrate with.

**MODEL-vs-Logic discriminator.** If a test can assert the answer without the running game, the question is Logic-Domain — write the failing GdUnit4 test instead (faster, keepable). A MODEL prototype is warranted only when sane-ness is a runtime judgment: performance, emergent behavior, or how the model reads in context.

**Host inside a real gameplay scene** — real entities, real runtime pipeline. An isolated `prototypes/<slug>/prototype.tscn` only when nothing plausible hosts the question; in isolation every variant looks fine.

### 3. Build variants — one probe, or 2–3 comparison forks

Different *approaches*, not tuned constants — a constant sweep is a tuning pass. A **comparison fork** ("which approach?") gets 2–3 structurally different variants; a **viability probe** ("does this one read/feel right?") gets exactly one, sharpened by the `[Export]` tuning loop. If you cannot name how a second variant would differ structurally, you do not need it. Switch variants via an `[Export]` enum on the prototype root.

### 4. Surface the state on both channels

- **Human** — a `CanvasLayer` overlay showing the state that drives the feel judgment.
- **Agent** — `JmoLogger`, never `GD.Print`, even in throwaway code: `analyze_godot_logs` parses only the former. Tag lines `[Subsystem][PROTO-<slug>]`; composition and cleanup: `logging_methodology`.

  Prototypes INVERT the production logging rule (CLAUDE.md: log state changes, not state) — here the log IS the instrument. Two constraints:
  - **Gate it behind an `[Export]`.** Verification pass on, feel pass off — per-event logging costs framerate and buries the log.
  - **Emit `key=value` data, never prose.** `variant=SprayAndStains stains=5 rayHit=5 rayMiss=0` is verifiable at a glance.

### 4b. Prove it runs before handing it over

Before handing over, the prototype must have been OBSERVED executing its own mechanism:

- a `RunSelfCheckOnReady`-style export (default OFF) exercising **every structural variant**, not just the authored one;
- an input-driven probe feeds a REAL input event through the pipeline, never a direct component call — `Input.ParseInputEvent(new InputEventKey(...))` against the actual InputMap action; a component self-check cannot observe HSM transition legs;
- it emits DATA per variant (counts, hit/miss, degenerate-value checks), not prose, so the verdict is machine-readable in `get_debug_output`;
- the whole body is exception-guarded;
- the agent runs it and reads the output. `dotnet build` + `godot --headless --import` show only that code compiles and a class registers; "built and imported" is never the handover bar.

**Verification tier — set by the QUESTION, not the probe's size.** Count the seams driven and the open forks. **Tier 1** (0–1 seams, no open forks): the bar above suffices. **Tier 2** (2+ seams, or any open fork) adds four obligations, because per-seam drivers can each be green while the composed encounter is broken — failures live BETWEEN mechanisms (spawn spread vs sensor reach, shed vs hitbox frame order):

- a **deterministic `ISceneRunner` driver per seam** — throwaway, under `Tests/Integration/Prototypes/`, deleted at parking. Two sub-obligations. **Reproduce the authored precondition** of the scenario claimed — the group size the gate requires, the sensor's acquisition wait, the trio (not a solo, whose flee IS the design), the environment the owner plays on (the playtest's own floor/walls, never a synthetic stand-in). **Pin the seam itself** — defects live in seams (a composite's child collection, a condition's raycast mask, a spec's pin pair, a collision layer), so assert the seam directly: child list == authored children, condition false in the open, the playtest floor's layer;
- an **encounter driver** — one driver over the REAL authored playtest host (actual enemy scenes and player, real spawn layout), driving input and asserting playtest-visible OUTCOMES, not mechanism states ("punch 1 leaves the attached rider alive, punch 2 kills"). Constants a playtest round produced get their intent asserted here;
- an **agent-observed playtest before handover** — drive the real authored host (MCP boot with driven input, or the real encounter root + roster + layout under an `ISceneRunner`) and read the playtest-visible state out of the log. The handover note says what was observed; a boot to the menu observes nothing, and "suites green" is a report about mechanisms;
- a **filtered adjacent-suite run** (`dotnet test --filter` over suites whose domain overlaps the probe) — a probe can turn a shipped suite red (submodule moves, seam edits) and hand it over broken; 5–15 s catches it;
- a **correctness-gap pass** — one independent read of QUESTION.md + the drivers + the host scene, hunting playtest-observable paths no driver covers, and auditing the DRIVERS' own premises against the authored design. Findings are obligations: every uncovered path gets driven, or the handover note names it owner-verification WITH the reason. A gap the round's own fix opened (a widened sensor reaching a neighbor encounter, a retuned constant's untested tail) may not be deferred at all — it gets a driver. A finding implicating a framework seam (Jmodot) is fixed in the framework (paired submodule branch, pushed before the parent pointer), never worked around in the scene.

**A bare `run_project` does not satisfy this.** `run/main_scene` is `boot.tscn`, so a plain run lands in the lifecycle/menu flow — the tree is real, the gameplay context is not, and anything that raycasts, collides, or reads run state behaves differently. Pass `run_project`'s optional `scene` parameter, targeting a scene that hosts the mechanism.

| What the probe needs in order to execute at all | Route |
|---|---|
| Nothing beyond its own node | isolated `prototypes/<slug>/prototype.tscn` + `run_project(scene:)` |
| World geometry to query (raycast, collision, overlap) | isolated scene CONTAINING that geometry |
| A real subsystem to drive it (spawn pipeline, HSM transitions, damage flow) | `ISceneRunner` harness — the only route with deterministic ticks and real wiring |
| Cross-system emergence (traits x reactions x AI) | the real gameplay scene; self-check reports data, human judges |

Row 3 is the carve-out to the auto-stop tripwire, which targets tests as DELIVERABLE, not as instrument. An isolated `prototype.tscn` is often warranted ALONGSIDE the real-scene host: isolation makes the self-check meaningful, the real host is the venue for the feel judgment.

### 5. Write scenario runners

Named scripted sequences that reset to a known state before running; without them a variant comparison is not reproducible.

### 6. Capture and park

`prototypes/<slug>/ANSWER.md` — the verdict **and the question it settled**. Bank it in the topic's `decisions.md` `## Decided` per `common.md §6.3` (a roadmap-borne answer then re-routes the Part via `/update_roadmap`); the real Part's plan pins it under `Constraints` citing the `ANSWER.md` path. Commit to `prototype/<slug>`, leave the pointer on the `prototype-pending` roadmap Part (worklog entry only when no Part exists), and stop.

## Extraction

Probe-mode only; provisional surfaces already live on the working branch.

Main-worthy changes authored on a probe branch are extracted to main per-commit. The branch itself is never merged **while its slugs remain probes**. Once a slug converts, §Conversion replaces this route.

| Main-worthy — extract | Never extracted |
|---|---|
| production bug fixes discovered during the probe | the probe code itself — never lift the branch's code |
| harness/memory/doctrine writes (auto-memory, MEMORY.md, worklog mirrors, skills) — including `/session_end`'s | tuning constants |
| incidental production-quality fixes unrelated to the probe | the verdict's design consequences (route via design→TDD) |

**Discipline on the branch:** commit main-worthy work in its own commit at discovery time, never mixed with probe commits — a single-concern commit extracts with `git cherry-pick <sha>`, a mixed one forces a hand-port. The containment guard's advisory names non-prototype staged paths so the loss is visible.

**Extract at session end, before parking:** cherry-pick single-concern commits onto main; hand-port mixed ones; an extracted `.cs` fix runs `/regression_gate` like any main commit. Record the extraction in `ANSWER.md`.

## Conversion

A probe answered its question, the owner wants the surface kept rather than rewritten, and full implementation is deferred. The slug converts from PROBE to PROVISIONAL **in place** and its branch merges — the third route, distinct from extraction and promotion. Conversion is the owner's call, never the agent's.

**All five before the branch merges — PROVISIONAL's entry checklist, applied per slug:**
1. **Registry row** naming the condition that retires the slug, with a Surface column listing its files AND its production call sites.
2. **`<Compile Include>` allowlist line** per converted slug in `{{PROJECT_NAME}}.csproj` — one reviewable line each, never a widened glob. The blanket `<Compile Remove>` stays.
3. **`// PROVISIONAL(<slug>)` markers** on every converted `.cs`.
4. **Probe scaffolding retired** — debug overlays, self-checks (a `RunSelfCheckOnReady` left true is what this catches), per-tick logging, key handlers, and any edit the probe made to shipped host content.
5. **Gates apply as normal** — TDD strict in the Logic domain, `/regression_gate` on `.cs` commits.

The path stays `prototypes/<slug>/`. **A slug that does NOT convert stays a probe** — its branch is still never merged, its main-worthy commits still leave by extraction.

## PROVISIONAL

A closed design shipped deliberately minimal because full implementation is deferred, by bandwidth or by dependency. The surface must be usable in ongoing work, and its later absorption mechanical rather than forensic.

**Entry checklist — all five before authoring:**
1. **Minimal surface.** The smallest thing that works; no speculative seams, no generality for an unpromised future.
2. **Markers.** A `// PROVISIONAL(<slug>): <one-line boundary>` header on each provisional `.cs` file.
3. **Registry entry** in `.claude/prototype_registry.md`, committed in the same commit (enforced: `.claude/hooks/provisional_totality_guard.py` denies a Bash `git commit` whose staged markers have no entry).
4. **Condition.** A named roadmap Part OR an explicit user trigger — ask the user when neither exists; an entry without one is invalid. Named Part is the strong default (`feedback_deferred_feature_stays_tracked_as_part`).
5. **Gates.** TDD strict in the Logic domain; `/regression_gate` on `.cs` commits.

**Marker rules.** Marker prose must avoid the FULL session_audit 1.5a merge-blocker alternation (`deferred|TODO|FIXME|follow-up|follow up|stub|not yet wired|no-op until|placeholder`; `PROVISIONAL` itself does not collide). The marker is disposition metadata, the justified exception to comment-discipline default-none (`feedback_comment_discipline`); marker lines carry no other commentary. Markers are `.cs`-only: a provisional `.tscn`/`.tres` is tracked by the registry entry alone.

**Boundaries.** Provisional is NOT the probe auto-stop tripwire — tests and persistence here are the production obligations above. Asset-maturity gates still bind: prototype-grade art never drives a design decision. `Prototype/` (capital) remains off-limits.

**Shared-tree discipline.** Pathspec commits, never `git add` + `git commit` on a shared tree; commit the registry entry with the code — untracked files can silently vanish (`gotcha_concurrent_session_hazards`).

**Enforcement boundary.** The totality hook sees `.cs` markers only; a scene-only provisional surface's registry obligation is author-discipline plus the promotion sweep below.

## Promotion

Provisional → production. The condition is met, or the surface must grow beyond minimal.

**Route by shape:**

| Situation | Route |
|---|---|
| Owning Part exists (plan-pending or further) | `part_drive` / `part_execute` — the Part's plan absorbs |
| No Part, architecturally loaded | `/architecture_brainstorm` creates the design home, then `part_drive` |
| No Part, small and resolvable in conversation | `/feature_drive` ships the production replacement; no roadmap home at all |

The prototype skill never authors design docs — one authoring path (`arch_rule_one_authoring_path_across_spawn_sources`).

**Checklist every promotion plan must carry:**
1. **Sweep.** Grep `PROVISIONAL(` repo-wide, `.claude/worktrees/` excluded (the Grep tool skips them via .gitignore; state the exclusion — a raw-grep variant sweeps peer checkouts, `gotcha_nested_worktree_defeats_path_containment`) → surface inventory; cross-check every slug against the registry. Marker-without-entry or entry-without-markers is a finding (`arch_rule_fallback_visible_to_every_honourer`).
2. **Verify shipped state before drafting.** `git log --all -- <path>` — the surface may already be shipped and committed (`gotcha_plan_pending_part_already_shipped`).
3. **Full design.** The provisional code is an INPUT to the production design, never the seed. Domain split applies; TDD strict in Logic.
4. **Replace or absorb.** The production change either absorbs the provisional files (with a parity audit) or deletes them in the same change — never orphans them beside the real system. Every deletion step verified against `git log --all -- <path>` (known-failure catalog #19).
5. **Close-out.** Registry entry → `absorbed` with the Part/PR ref; marker sweep returns 0 (same worktree-excluded scope as step 1).

## Iteration cost

C# needs a rebuild per change, and building while the Godot editor is open is a known hazard (`feedback_never_build_while_godot_editor_open`). Put every feel-tunable value behind an `[Export]` so the running scene absorbs the tuning loop, and batch structural edits into one rebuild.

**Inline by default — no fan-out in the loop.** Only the human playing the build produces the verdict. Optional second opinion: when the verdict is judgment-heavy, dispatch ONE independent critic lens (QUESTION.md + screenshots + `[PROTO-<slug>]` logs) to test whether the evidence settles the question.

**Log-first — after you play, mine the log BEFORE diagnosing.** Every fix starts from `/analyze_godot_logs` on the playtest log (CLAUDE.md *Logs are Truth*). **Capture the log before it moves** — appdata `godot.log` is shared by every worktree and gate run, and any of them truncates it on launch; copy it to a stable name the moment the playtest ends.

## Guardrails

**Probe guardrails.**

- **A prototype branch goes stale.** If the playtest surfaces a defect that smells already-fixed, check `git log main` FIRST and merge main in rather than re-implementing — then re-import (`godot --headless --import`) and re-verify the class cache before judging.
- **One sitting.** Still building it hours later means the question was too big — split it.
- **Round-3 circuit breaker.** A third playtest round surfacing defects in a mechanism an earlier round already "fixed" means the probe is under-instrumented, not that the next fix is close. Stop fixing; spend the round making the mechanism observable (§4b), then resume. Cheap tell: a round whose driver-and-fix lines exceed the probe's own is maintaining scaffolding.
- **Auto-stop tripwire.** Wiring real persistence (`.tres` authoring, registry entries, save state), or generalising for a case you might want later — you have stopped prototyping. Adding a test is the tripwire only for a test that would SURVIVE parking: a permanent suite pinning the prototype's behavior treats the design as settled. The Tier 2 drivers §4b mandates are the exception.
- **Handover bar is "observed running", not "compiles".**
- **The deliverable is an answer, never a patch.** Promoting a probe to PRODUCTION reruns the normal design→TDD path as a rewrite; never lift the branch's code. Conversion to a PROVISIONAL surface is the one route that does ship it (§Conversion).

**Provisional guardrails.**

- **Condition is mandatory.** Ask the user; never assume a trigger.
- **Markers and entry land atomically.** Same commit — the totality hook denies the rest; anything the matcher misses (non-Bash commits, `git merge`) is the promotion sweep's backstop.
- **Gates are never carved out.**

## When not to prototype

| Instead of a prototype | Do this |
|---|---|
| Logic-Domain question — the project's Logic-Domain subsystems, math, data structures | Write the failing GdUnit4 test — faster, shows full state, keepable. |
| Design already converged, no deferral | Implement it — `/part_drive` or `/feature_drive`. |
| Design converged, full implementation deferred, surface usable now | PROVISIONAL mode — ship the minimal tracked surface; never a probe branch. |
| Full implementation deferred, no usable surface needed now | No prototype at all — track the deferral as a roadmap Part (`feedback_deferred_feature_stays_tracked_as_part`). |
| Known root cause | `debugging`. |
| Prototype-grade sprite, shader, or UI asset | `sprite_authoring` / `shader_authoring` — they own the render→screenshot→critique loop. |
| "Prototype the whole feature" | No natural stopping point — it becomes the production build by momentum. Split until one question remains. |

## Cross-references

- `.claude/prototype_registry.md` — the provisional-surface totality record.
- `architecture_brainstorm` — files a `prototype-pending` Part on an ungrillable feel-fork; the one-line answer returns as a resolved fork.
- `_brainstorm_shared/design_contract.md` clause 7 — the carve-out letting a feel-unknowable fork exit the Hard Gate as `prototype-pending`. PROBE-only; a provisional surface is post-design-lock implementation.
- `_brainstorm_shared/common.md` §6.3 — Part-state vocabulary. `prototype-pending` is PROBE-scoped; the registry is the provisional tracking layer and no new roadmap state exists.
- `logging_methodology` — `[PROTO-<slug>]` composition + cleanup grep.
- `.claude/hooks/prototype_containment_guard.py` — probe containment (commit-time; advisory on non-prototype staged paths — `## Extraction`); `.claude/hooks/provisional_totality_guard.py` — provisional totality (commit-time).
- Any project rule governing a same-named production directory — unrelated to this skill.
