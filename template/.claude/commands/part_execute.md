---
allowed-tools: Bash(dotnet:*), Bash(git:*), Glob, Grep, Read, Edit, Write, Task, TaskCreate, TaskUpdate, SlashCommand
description: Autonomously execute an approved plan — TDD per slice, gate, commit. Halts on its stop valves.
---

# /part_execute — Autonomous Plan-Execution Loop

The serial grind that runs AFTER a plan is approved. Plan approval (in the planning session) is the **single human gate**; everything downstream — handoff stance, TDD per slice, the regression gate, the readiness battery, the roadmap flip — is mechanical execution of a contract you already signed. This command sequences those steps into one autonomous run with explicit halt valves, so you can hand off an approved Part and walk away.

## Usage

`/part_execute <plan-file-path>` — invoke in a **fresh, lower-effort executor session** (the planning/auditing happened at high effort; execution of an unambiguous plan is safely lower-effort per `orchestration` §5 *Model & Effort Selection*). Not in the planning session. For a Part too large for one context, wrap the invocation in `/loop` — the loop body is still this command.

## The single gate (upstream, already passed)

By the time you run this, the plan has been authored, `/plan_check`'d, audited to satisfaction, and **approved by the user**. That approval is the execution directive. Per `feedback_honor_execution_directive`: do **not** re-ask "continue or hand off?" mid-stream. The halt valves below are the only legitimate pauses — and they fire on *new information the plan didn't anticipate*, never on "am I still allowed to proceed?"

## Procedure

### Step 1 — Handoff: adopt the executor stance

Run [`/plan_handoff <plan-file-path>`](plan_handoff.md) and adopt its execution stance verbatim for the rest of this run:
- Valve mapping: a load-bearing empirical claim that fails that stance's verification rule is **halt valve (a)**.
- The plan is self-sufficient **for SCOPE**, never for design rules — `plan_handoff`'s stance states the carve-out.

If the plan path is missing or unreadable, abort exactly as `plan_handoff` specifies and stop.

### Step 2 — Build the slice checklist

Read the plan's ordered steps/slices. Create one tracked task per slice (`TaskCreate`) so the long run has live, user-visible progress; mark each `in_progress` on entry and `completed` on green. The slice list is the plan's, in the plan's order — do **not** re-decompose or re-sequence (that was a plan-time decision).

**Resume + reconcile contract.** On a `/loop` re-invocation (a Part too large for one context), reconcile against the existing tasks rather than re-`TaskCreate`ing the checklist — skip `completed` slices, resume the `in_progress` one. The loop terminates when all slices are `completed` and the gate + battery are green, or a valve fires. On any valve firing, annotate the active slice's task (`TaskUpdate`) with the halt reason so the board reflects the pause rather than dangling at `in_progress`.

### Step 3 — The execution loop (one slice at a time, in order)

For each slice:

1. **Classify the domain** (per CLAUDE.md *Hybrid TDD*): Logic vs Gameplay. The plan usually states it; if not, infer from the touched subsystem.
2. **Logic domain — strict TDD:** RED (write the failing `[TestSuite]` test first) → **VERIFY the specific expected failure** (per `feedback_test_name_must_match_exercised_path` — confirm the setup drives the SUT into the branch the title names) → GREEN (minimum production code to pass) → assess REFACTOR (refactor when it adds value; skip when it doesn't). One focused test per stated behavior, sized like the neighbouring suite in `Tests/<domain>/`.
3. **Gameplay domain — automate deterministic, flag subjective:** drive input→outcome / state-transition / signal-wiring / scene-structure expectations through ISceneRunner integration tests. Work that is genuinely subjective ("feels responsive?", juice, timing) cannot be test-gated → **halt valve (c)**: implement the mechanism, then flag the specific behaviors for manual playtest rather than asserting them green.
4. **A green test proves the slice WORKS — not that it's done.** `JmoLogger.Error` triggers test failure — treat any error log surfaced by the run as a real failure, not noise.
5. **Per-slice spec verifier** — when a slice touches 3+ files or introduces a configuration surface beyond the plan's Authored-surfaces section, run one `dispatch.js` job (`shape: review`, read-only, `executor·low`, `general-purpose`) whose prompt file carries the slice's plan section verbatim, the diff, and the single question "does the diff satisfy this section, and what does it do beyond it?" Spec conformance only, never code quality; the input is plan + diff, never the executor transcript. Pass `agentType: "general-purpose"` with the resolved `model` and `effort`.
6. **Reuse check (per slice, self-reported):** for every named configuration surface this slice introduced BEYOND the plan's Authored-surfaces section — type, `[Export]`, parameter, behavior flag, helper — answer `rules/design_litmus.md` #1: name the family that owns the concern, or record "none exists" in the slice rationale. A mid-slice invention is exactly what plan-time checks cannot see; this line is the only guard at the moment it happens.
7. Mark the slice complete against a quoted filter run — the command and its pass/fail counts — never against a self-assessment. Then advance.

**Self-introduced regressions are fixed in-session, never parked** (per `feedback_fix_self_introduced_regression_immediately`). If a slice breaks a sibling behavior, that is part of this slice's work. A bug the slice did not introduce is a `/worklog` follow-up named in the report, not a fix — unless the slice depends on it.

### Step 4 — Regression gate (single-flight, serial)

After all slices are green, verify **by chain position** (`change_control` §Gate cadence):

- **Mid-chain Part** — run a **union `-Filter` over this Part's accumulated blast zone** (plain tests; `-StaticOnly` optional for the static guards). Do **not** run the gate and do **not** commit: the work accumulates to the drive close, whose full gate backs every commit in the drive. A red union run is **halt valve (d)**.
- **Chain-final Part, or a standalone Part with no chain** — run the FULL **`/regression_gate`** (mandatory for any `.cs` change, no carve-outs), marked `# gate: final`. It is the separate single-flight serial gate — do **not** fan it out, do **not** run it concurrently with anything. A gate failure that isn't a trivial in-scope fix is **halt valve (d)**; re-verify a fix at its blast-radius width (`-RetryOnly` / `verify.ps1 -Scope <domains>`), never a reflex full re-run.

### Step 5 — Readiness battery (static, read-only) — *feature branches only*

**Skip on `main`.** `/pr_ready` is a pre-PR/pre-merge battery; a direct-to-`main` commit has no branch diff to gate against, so run it ONLY when `git branch --show-current` is not the default branch (`main`). On `main`, go straight to Step 6 — `/regression_gate` (Step 4) remains the gate, and `/pr_ready`'s lenses re-run at PR time on whatever branch the work eventually merges through.

On a feature branch: with the gate green, run [`/pr_ready`](pr_ready.md) over the Part's diff — the parity / consume-new-APIs / worklog-roadmap / doc-coverage lenses that each catch a "done but not actually done" class regression. Any **BLOCKER** is **halt valve (d)**: stop and surface, do not commit over it. WARN/INFO are reported, not blocking. An **empty / timed-out / partial** battery result is NOT a pass — re-run once; if still inconclusive, halt (valve d). A clean battery must be a *positive* "all lenses returned, 0 BLOCKERs," never "nothing came back" (`gotcha_workflow_fanout_search_false_absence`).

### Step 5.5 — Close-out design review

One design-review pass over the FULL working diff: dispatch one executor-tier agent via `dispatch.js` (`executor·medium`; mandate: composition-vs-inheritance, authored-surface coherence against `rules/design_litmus.md`, existing-family reuse, designer ergonomics, plus `checklists/code_quality.md` Design items). Every finding is explicitly dispositioned — fix now / worklog with reason / refuse with evidence — in the final report. Not auto-blocking; **undispositioned findings are**. **SKIP the dispatched pass** when ALL hold: (a) pure refactor/parity-gated Part with zero new authored surfaces (no new consumer-facing types, exports, scene nodes, or Jmodot code), (b) `/plan_check` ran with the architecture lenses, (c) the per-slice diff review is clean. Record the skip + basis in the final report. Parts that author new surfaces always run it.

### Step 6 — Close out

When gate + battery are clean:
1. Apply `/update_roadmap mark complete <part>` (the roadmap flip is the write-back; surface its batch diff in the final report).
2. Land the categorical commits — split by `feat`/`fix`/`refactor`/`chore` per CLAUDE.md Git policy. **Concurrent-session index hygiene** (`gotcha_concurrent_session_hazards`): a staged entry you didn't stage is foreign — preserve it and coordinate an isolated commit, per `part_drive.md` Step 6's rule, never unstage it. Do not push.

The plan approval authorizes every step its Definition of Done named; ask the user only for a step it did not.

## The four halt valves

These are the *only* pauses **once the loop begins**. (The Step-1 `plan_handoff` pre-flight abort on a missing/unreadable plan is separate — it fires before the loop starts, not as one of the four.) Each fires on information the plan could not have known — surface it plainly with the specific evidence and stop; do not improvise a fix to an out-of-plan problem.

| Valve | Trigger | Action |
|---|---|---|
| **(a) Plan wrong** | A referenced file/type/symbol doesn't exist as described — **or a depended-on prior-Part deliverable is present but incomplete / behaviorally-absent** (it compiles but behaves wrong); a needed decision isn't in the plan's Decision record; an integration step yields results the plan didn't anticipate. | STOP, quote the mismatch (plan says X / reality is Y), ask. A half-built dependency is (a), not (b) — diagnose it as a plan-fact change, don't spend valve (b)'s attempt budget thrashing on it. |
| **(b) Stuck** | Attempts on a slice have stopped producing new diagnostic information. | Halt — do not thrash. Report what was tried and the failure, ask. |
| **(c) Subjective gameplay** | Feel/juice/timing work that no automated test can assert. | Implement the mechanism, flag the specific behaviors for manual playtest, continue to the next testable slice. |
| **(d) Gate/battery blocker or inconclusive** | `/regression_gate` failure (not a trivial in-scope fix), any `/pr_ready` BLOCKER, **or an empty / timed-out / partial gate-or-battery result** — a fanned lens that returns nothing is the `gotcha_workflow_fanout_search_false_absence` class, NOT a pass. | STOP before any commit; surface the finding. For an inconclusive result, re-run once, then surface — never read an absent result as green. |

## Autonomy discipline

- **Don't reduce planned scope** (per `feedback_dont_unilaterally_reduce_planned_scope`). The plan is the contract; cutting a slice needs explicit re-authorization, which means a halt — not a silent drop.
- **Don't compress the TDD/Socratic gates** because the plan is rich (per `feedback_session_start_hook_does_not_override_skill_procedure` + `feedback_dont_compress_socratic_on_rich_prompt`). A detailed plan is *starter material*, not a license to skip RED-before-GREEN.
- **Don't open a gate with an advisory verdict.** `/pr_ready`'s WARN/INFO are advisory; its **BLOCKER tier is gating** (valve d) and is never silently auto-passed. (Reserve "advisory" for `/plan_check`'s fully-non-blocking sense — a `/pr_ready` BLOCKER blocks.)

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Run the slices as a `Workflow` to parallelize." | Slices that share a seam or a file serialize; independent lanes may run as `dispatch_chains.js` chains (`orchestration` §7). `Workflow` carries no execution state — state lives in the plan file. |
| "Gate + battery green, I'll push to save a round-trip." | Land the categorical commits (Step 6); never push without instruction. |
