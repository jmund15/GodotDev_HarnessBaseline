---
allowed-tools: Bash(dotnet:*), Bash(git:*), Glob, Grep, Read, Edit, Write, Task, TaskCreate, TaskUpdate, SlashCommand
description: Autonomously execute an approved plan — handoff stance, TDD per slice, regression_gate, pr_ready, propose roadmap-complete + commit. Halts on 4 valves.
---

# /part_execute — Autonomous Plan-Execution Loop

The serial grind that runs AFTER a plan is approved. Plan approval (in the planning session) is the **single human gate**; everything downstream — handoff stance, TDD per slice, the regression gate, the readiness battery, the roadmap flip — is mechanical execution of a contract you already signed. This command sequences those steps into one autonomous run with explicit halt valves, so you can hand off an approved Part and walk away.

## Usage

`/part_execute <plan-file-path>` — invoke in a **fresh, lower-effort executor session** (the planning/auditing happened at high effort; execution of an unambiguous plan is safely lower-effort per `process_rule_plan_high_execute_lower`). Not in the planning session. For a Part too large for one context, wrap the invocation in `/loop` — the loop body is still this command.

## The single gate (upstream, already passed)

By the time you run this, the plan has been authored, `/plan_check`'d, audited to satisfaction, and **approved by the user**. That approval is the execution directive. Per `feedback_honor_execution_directive`: do **not** re-ask "continue or hand off?" mid-stream. The halt valves below are the only legitimate pauses — and they fire on *new information the plan didn't anticipate*, never on "am I still allowed to proceed?"

## Procedure

### Step 1 — Handoff: adopt the executor stance

Run [`/plan_handoff <plan-file-path>`](plan_handoff.md) and adopt its execution stance verbatim for the rest of this run:
- The plan file is authority; follow it exactly where unambiguous.
- Verify load-bearing empirical claims (file paths, type existence, prior-art assertions) before acting on them — per `feedback_verify_explore_agent_empirical_claims`. A claim that fails verification is **halt valve (a)**.
- Mechanical execution is yours — don't ask permission for unambiguous steps.
- Do **not** read prior session transcripts or memory snapshots; the plan is self-sufficient by design — when produced by [`/plan_drive`](plan_drive.md), the critique trail's load-bearing conclusions were folded into the plan's **Decision record**, so the *why* travels inside the plan, not in a side artifact.

If the plan path is missing or unreadable, abort exactly as `plan_handoff` specifies and stop.

### Step 2 — Build the slice checklist

Read the plan's ordered steps/slices. Create one tracked task per slice (`TaskCreate`) so the long run has live, user-visible progress; mark each `in_progress` on entry and `completed` on green. The slice list is the plan's, in the plan's order — do **not** re-decompose or re-sequence (that was a plan-time decision).

**Resume + reconcile contract.** On a `/loop` re-invocation (a Part too large for one context), reconcile against the existing tasks rather than re-`TaskCreate`ing the checklist — skip `completed` slices, resume the `in_progress` one. The loop terminates when all slices are `completed` and the gate + battery are green, or a valve fires. On any valve firing, annotate the active slice's task (`TaskUpdate`) with the halt reason so the board reflects the pause rather than dangling at `in_progress`.

### Step 3 — The execution loop (one slice at a time, in order)

For each slice:

1. **Classify the domain** (per CLAUDE.md *Hybrid TDD*): Logic vs Gameplay. The plan usually states it; if not, infer from the touched subsystem.
2. **Logic domain — strict TDD:** RED (write the failing `[TestSuite]` test first) → **VERIFY the specific expected failure** (per `feedback_test_name_must_match_exercised_path` — confirm the setup drives the SUT into the branch the title names) → GREEN (minimum production code to pass) → assess REFACTOR (refactor when it adds value; skip when it doesn't).
3. **Gameplay domain — automate deterministic, flag subjective:** drive input→outcome / state-transition / signal-wiring / scene-structure expectations through ISceneRunner integration tests. Work that is genuinely subjective ("feels responsive?", juice, timing) cannot be test-gated → **halt valve (c)**: implement the mechanism, then flag the specific behaviors for manual playtest rather than asserting them green.
4. **A green test is the only proof a slice is done.** `JmoLogger.Error` triggers test failure — treat any error log surfaced by the run as a real failure, not noise.
5. Mark the slice complete and advance.

**Self-introduced regressions are fixed in-session, never parked** (per `feedback_fix_self_introduced_regression_immediately`). If a slice breaks a sibling behavior, that is part of this slice's work.

### Step 4 — Regression gate (single-flight, serial)

After all slices are green, run **`/regression_gate`** (mandatory for any `.cs` change, no carve-outs). It is the separate single-flight serial gate — do **not** fan it out, do **not** run it concurrently with anything. A gate failure that isn't a trivial in-scope fix is **halt valve (d)**.

### Step 5 — Readiness battery (static, read-only) — *feature branches only*

**Skip on `main`.** `/pr_ready` is a pre-PR/pre-merge battery; a direct-to-`main` commit has no branch diff to gate against, so run it ONLY when `git branch --show-current` is not the default branch (`main`). On `main`, go straight to Step 6 — `/regression_gate` (Step 4) remains the gate, and `/pr_ready`'s lenses re-run at PR time on whatever branch the work eventually merges through.

On a feature branch: with the gate green, run [`/pr_ready`](pr_ready.md) over the Part's diff — the parity / consume-new-APIs / worklog-roadmap / doc-coverage lenses that each catch a "done but not actually done" class regression. Any **BLOCKER** is **halt valve (d)**: stop and surface, do not commit over it. WARN/INFO are reported, not blocking. An **empty / timed-out / partial** battery result is NOT a pass — re-run once; if still inconclusive, halt (valve d). A clean battery must be a *positive* "all lenses returned, 0 BLOCKERs," never "nothing came back" (`gotcha_workflow_fanout_search_false_absence`).

### Step 6 — Propose completion (do not auto-apply)

When gate + battery are clean:
1. Propose `/update_roadmap mark complete <part>` (the roadmap flip is the write-back; present its batch diff for the single approval `update_roadmap` already requires).
2. Propose the categorical commit(s) — split by `feat`/`fix`/`refactor`/`chore` per CLAUDE.md Git policy. **Propose, don't push** — committing/pushing waits for the user.

## The four halt valves

These are the *only* pauses **once the loop begins**. (The Step-1 `plan_handoff` pre-flight abort on a missing/unreadable plan is separate — it fires before the loop starts, not as one of the four.) Each fires on information the plan could not have known — surface it plainly with the specific evidence and stop; do not improvise a fix to an out-of-plan problem.

| Valve | Trigger | Action |
|---|---|---|
| **(a) Plan wrong** | A referenced file/type/symbol doesn't exist as described — **or a depended-on prior-Part deliverable is present but incomplete / behaviorally-absent** (it compiles but behaves wrong); a needed decision isn't in the plan's Decision record; an integration step yields results the plan didn't anticipate. | STOP, quote the mismatch (plan says X / reality is Y), ask. A half-built dependency is (a), not (b) — diagnose it as a plan-fact change, don't burn the 2-attempt budget thrashing on it. |
| **(b) Stuck** | A slice won't go green after **2 focused attempts** with no new diagnostic information. | Halt — do not thrash. Report what was tried and the failure, ask. |
| **(c) Subjective gameplay** | Feel/juice/timing work that no automated test can assert. | Implement the mechanism, flag the specific behaviors for manual playtest, continue to the next testable slice. |
| **(d) Gate/battery blocker or inconclusive** | `/regression_gate` failure (not a trivial in-scope fix), any `/pr_ready` BLOCKER, **or an empty / timed-out / partial gate-or-battery result** — a fanned lens that returns nothing is the `gotcha_workflow_fanout_search_false_absence` class, NOT a pass. | STOP before any commit; surface the finding. For an inconclusive result, re-run once, then surface — never read an absent result as green. |

## Autonomy discipline

- **Don't reduce planned scope** (per `feedback_dont_unilaterally_reduce_planned_scope`). The plan is the contract; cutting a slice needs explicit re-authorization, which means a halt — not a silent drop.
- **Don't compress the TDD/Socratic gates** because the plan is rich (per `feedback_session_start_hook_does_not_override_skill_procedure` + `feedback_dont_compress_socratic_on_rich_prompt`). A detailed plan is *starter material*, not a license to skip RED-before-GREEN.
- **Don't open a gate with an advisory verdict.** `/pr_ready`'s WARN/INFO are advisory; its **BLOCKER tier is gating** (valve d) and is never silently auto-passed. (Reserve "advisory" for `/plan_check`'s fully-non-blocking sense — a `/pr_ready` BLOCKER blocks.)
- **Don't ask for permission on unambiguous mechanical steps** — that's the executor stance. Reserve user interaction for the four valves and the Step 6 approval.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Run the slices as a `Workflow` to parallelize." | Execution is serial-stateful — slice N depends on slice N-1. `Workflow` is a stateless parallel fan-out; it cannot carry execution state. This is a `/loop`-shaped task. |
| "The plan is obvious — implement first, test after." | No carve-out for self-evident Logic. RED before GREEN, always. |
| "Skip `/regression_gate` — it was just a rename." | Mandatory for all `.cs` changes, no exceptions. |
| "A file the plan referenced is missing — I'll just create/substitute it." | That's halt valve (a). The plan's factual basis changed; surface it, don't paper over it. |
| "Test won't pass — let me try ten more variations." | Valve (b). After 2 informationless attempts, halt; thrashing burns budget and usually means the plan or the SUT is wrong. |
| "Gate + battery green, I'll commit and push to save a round-trip." | Propose only. Commit on request; never push without instruction. |
