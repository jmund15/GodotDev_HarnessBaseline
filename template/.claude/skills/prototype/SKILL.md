---
name: Prototype
description: >-
  Use when a design fork turns on how something reads, responds, or behaves at runtime and
  discussion cannot settle it — build throwaway variants on a `prototype/<slug>` branch:
  one viability probe, or 2–3 structurally different forks when comparing approaches. Look,
  answer in one line. The question must still be OPEN:
  converged design → implement, Logic-Domain question → a failing test, known root cause
  → `debugging`. SKIP for prototype-grade ASSET authoring
  (`sprite_authoring` / `shader_authoring`) and for constant-tuning a shipped system.
---

# Prototype

> **`prototypes/`** (lowercase, plural, repo root) is this skill's throwaway home. **`Prototype/`** (capital, singular) is the live arena-floor subsystem — unrelated production code, governed by `.claude/rules/pp_prototype_arena.md`. Never write prototype code into `Prototype/`.

Throwaway code that answers a question. The question decides the shape, and the deliverable is the answer — never the code.

## Containment

Two mechanical layers and the workflow they enforce:

1. **`main` cannot compile prototype `.cs` — and `.cs` is all this layer covers.** `{{PROJECT_NAME}}.csproj` permanently carries `<Compile Remove="prototypes\**\*.cs" />` + `<None Include="prototypes\**\*.cs" />`, so a prototype `.cs` that leaks onto `main` is inert by construction: it cannot compile, so it cannot have production callers. Never delete those two lines on `main`. Godot does not compile scenes, so this layer says nothing about a `prototypes/**/*.tscn` or `.tres` — one that lands on `main` is fully live, and registration in this project is by directory placement.
2. **`main` cannot commit any prototype file, whatever its extension.** `.claude/hooks/prototype_containment_guard.py` (PreToolUse on `Bash`) denies a `git commit` that stages a path under `prototypes/` while HEAD is not a `prototype/*` branch — closing exactly the gap layer 1 leaves. It fails open by design (detached HEAD, a failing `git` call, nothing staged) and cannot see a commit made outside the `Bash` tool: a PowerShell, IDE, or Godot-editor commit bypasses it.
3. **Work happens on `prototype/<slug>`.** That branch's FIRST commit removes the two csproj lines, so the prototype compiles and runs there, and the layer-2 branch check passes for the same reason. The branch is never merged — park it and leave a pointer.

*Consequence of 1 + 2, not a third layer:* `/regression_gate`'s no-carve-outs promise stays literally intact — prototype code is never part of a main-bound `.cs` change. Never claim a gate carve-out for a prototype; there is nothing to carve out.

## Phases

### 1. State the question

Write `prototypes/<slug>/QUESTION.md`: the question, plus **the shape of its answer** — "a number", "one of three enum values", "yes/no". A prototype whose answer shape you cannot state is not yet scoped; sharpen the question before building anything.

### 2. Pick the branch and the host

| Branch | Question it answers | Examples |
|---|---|---|
| **MODEL** | Does this model produce sane outcomes? | synergy resolution shapes, an HSM transition graph with reachable dead states, data-model expressiveness |
| **FEEL** | Does this read and respond right? | movement, camera, input response, timing, telegraph readability, juice |

Both branches are **C#**. Godot forbids a GDScript file inheriting from a C# script, so a GDScript prototype can never extend the component/state classes it must integrate with, and the project has no GDScript baseline.

**MODEL-vs-Logic discriminator.** If a test can assert the answer without the running game, the question is Logic-Domain — write the failing GdUnit4 test instead (faster, and keepable). A MODEL prototype is warranted only when sane-ness is a runtime judgment: performance, emergent behavior, or how the model reads in context.

**Host inside a real gameplay scene** — real enemies, real spell pipeline. An isolated `prototypes/<slug>/prototype.tscn` only when nothing plausible hosts the question. A throwaway route on its own is a vacuum: every variant looks fine in isolation.

### 3. Build variants — one probe, or 2–3 comparison forks

Different *approaches*, not tuned constants — a constant sweep is a tuning pass, not a prototype. The count follows the question: a **comparison fork** ("which approach?") gets 2–3 structurally different variants; a **viability probe** ("does this one read/feel right?") gets exactly one — the `[Export]` tuning loop sharpens it. If you cannot name how a second variant would differ structurally, you do not need it. Switch variants via an `[Export]` enum on the prototype root so the running scene selects them.

### 4. Surface the state on both channels

- **Human** — a `CanvasLayer` overlay showing the state that drives the feel judgment.
- **Agent** — `JmoLogger`, never `GD.Print`, even in throwaway code: `analyze_godot_logs` parses only the former, so a `GD.Print` playtest is un-mineable afterward; `JmoLogger` stamps node path + file:line + method for free, which makes a verdict actionable in one read; and `jmodot/debug_logging_enabled` already exists as the toggle. Tag lines `[Subsystem][PROTO-<slug>]`; composition and cleanup: `logging_methodology`.

  Prototypes INVERT the production logging rule (CLAUDE.md: log state changes, not state). Here the log IS the instrument — the agent cannot see the screen at all and the owner cannot see it later. Two constraints keep heavy logging useful rather than corrosive:
  - **Gate it behind an `[Export]`.** Per-event logging during a FEEL playtest costs framerate and buries the log the owner would mine afterward. Verification pass on, feel pass off.
  - **Emit `key=value` data, never prose.** `variant=SprayAndStains stains=5 rayHit=5 rayMiss=0 unitNormals=yes` is verifiable at a glance; "blood burst fired!" states only what the caller already knew.

### 4b. Prove it runs before handing it over

`dotnet build` + `godot --headless --import` verify that code COMPILES and that a class REGISTERS. They execute none of the prototype's logic. A probe handed over on that evidence alone relocates the debugging onto the owner, which is the one cost a prototype exists to avoid.

Before handing over, the prototype must have been OBSERVED executing its own mechanism:
- a `RunSelfCheckOnReady`-style export (default OFF) that exercises **every structural variant**, not just the authored one — an unexercised variant is where a first-run crash hides;
- it emits DATA per variant (counts, hit/miss, degenerate-value checks), not prose, so the verdict is machine-readable in `get_debug_output`;
- the whole body is exception-guarded — a probe that logs its own failure teaches everything, one that crashes on load teaches nothing;
- the agent runs it and reads the output. "Built and imported" is never the handover bar.

**A bare `run_project` does not satisfy this.** `run/main_scene` is `boot.tscn`, so a plain run lands in the lifecycle/menu flow; autoloads make the tree real while the gameplay context is not, so anything that raycasts, collides, or reads run state behaves differently than in play. Use `run_project`'s optional `scene` parameter against a scene that actually hosts the mechanism.

The route is not one-size-fits-all; key it on what the probe needs in order to execute at all:

| What the probe needs in order to execute at all | Route |
|---|---|
| Nothing beyond its own node | isolated `prototypes/<slug>/prototype.tscn` + `run_project(scene:)` |
| World geometry to query (raycast, collision, overlap) | isolated scene CONTAINING that geometry |
| A real subsystem to drive it (spawn pipeline, HSM transitions, damage flow) | `ISceneRunner` harness — the only route with deterministic ticks and real wiring |
| Cross-system emergence (traits x reactions x AI) | the real gameplay scene; self-check reports data, human judges |

Row 3 is the carve-out to the auto-stop tripwire: when the mechanism cannot exist outside a driven subsystem, a throwaway scene-runner harness IS the cheapest way to observe it, and refusing on "tests mean you stopped prototyping" grounds just relocates the debugging onto the owner. The tripwire targets tests as DELIVERABLE, not as instrument.

This is why a `prototypes/<slug>/prototype.tscn` is often warranted ALONGSIDE the real-scene host rather than instead of it (Phase 2): the isolated scene is what makes the self-check meaningful and collapses the owner's iteration loop, while the real-scene host stays the venue for the feel judgment isolation would flatter.

### 5. Write scenario runners

Named scripted sequences that reset to a known state before running. Without them a variant comparison is not reproducible and the verdict is a mood.

### 6. Capture and park

`prototypes/<slug>/ANSWER.md` — the verdict **and the question it settled**. Bank it in the topic's `decisions.md` `## Decided` — the canonical home per `common.md §6.3` (a roadmap-borne answer then re-routes the Part via `/update_roadmap`); the real Part's plan pins it under `Constraints` citing the `ANSWER.md` path. Commit to `prototype/<slug>`, leave the pointer on the `prototype-pending` roadmap Part (worklog entry only when no Part exists), and stop.

## Iteration cost

C# needs a rebuild per change, and building while the Godot editor is open is a known hazard (`feedback_never_build_while_godot_editor_open`). Put every feel-tunable value behind an `[Export]` so the running scene absorbs the tuning loop, and batch structural edits into one rebuild.

**Inline by default — no fan-out in the loop.** Build, run, you play, tweak. Only the human playing the build produces the verdict, and variants share one scene/branch — they are not parallel artifacts, so no dispatch replaces the loop. Optional second opinion: when the verdict is judgment-heavy, dispatch ONE independent critic lens (QUESTION.md + screenshots + `[PROTO-<slug>]` logs) to test whether the evidence actually settles the question.

## Guardrails

- **A prototype branch goes stale.** Cut from an older main, it routinely misses landed fixes. If the playtest surfaces a defect that smells already-fixed (or a fix you start writing feels familiar), check `git log main` FIRST and merge main in rather than re-implementing — then re-import (`godot --headless --import`) and re-verify the class cache before judging the playtest.
- **One sitting.** Still building it a day later means the question was too big — split it.
- **Auto-stop tripwire.** Adding a test, wiring real persistence (`.tres` authoring, registry entries, save state), or generalising for a case you might want later — you have stopped prototyping. A prototype that needs tests is no longer a prototype. A `RunSelfCheckOnReady` self-check is instrumentation on the probe itself, not a GdUnit4 suite — `ISceneRunner` buys assertions the prototype does not need, since the verdict is the owner's and it is visual.
- **Handover bar is "observed running", not "compiles".** Build + import is the floor, never the ceiling.
- **The deliverable is an answer, never a patch.** The variant code was written under prototype constraints. Promotion reruns the normal design→TDD path as a rewrite; never lift the branch's code.

## When not to prototype

| Instead of a prototype | Do this |
|---|---|
| Logic-Domain question — spell architecture, synergies, inventory, math, data structures | Write the failing GdUnit4 test. Faster, shows full state, and it is keepable. |
| Design already converged | Implement it — `/part_drive` or `/feature_drive`. |
| Known root cause | `debugging`. |
| Prototype-grade sprite, shader, or UI asset | `sprite_authoring` / `shader_authoring` — they own the render→screenshot→critique loop. |
| "Prototype the whole feature" | A full-feature prototype has no natural stopping point and becomes the production build by momentum. Split until one question remains. |

## Cross-references

- `architecture_brainstorm` — files a `prototype-pending` Part on an ungrillable feel-fork and continues; the one-line answer returns as a resolved fork.
- `_brainstorm_shared/design_contract.md` clause 7 — the narrow carve-out that lets a feel-unknowable fork exit the Hard Gate as `prototype-pending`.
- `logging_methodology` — `[PROTO-<slug>]` composition + cleanup grep.
- `.claude/rules/pp_prototype_arena.md` — the unrelated `Prototype/` arena subsystem.
