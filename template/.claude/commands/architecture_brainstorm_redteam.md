---
description: Red-team a drafted architecture or a live Socratic design step
argument-hint: <design.md> [<section>...] [--auto]
---

# Architecture Red-Team

Attacks a design for holes through `/review`'s seat route, the same one `/review` diff mode uses, with [`agents/redteam_agents.md`](agents/redteam_agents.md) as the question set. Each finding is classed design-hole, taste-fork or dead-end. Two modes:

- **Mode A, standalone:** one pass over a drafted design doc, or the sections named, before it hardens into roadmap Parts.
- **Mode B, interleaved:** `architecture_brainstorm --red_team` fires it at a Socratic boundary on one in-flight decision (`skills/architecture_brainstorm/reference/red_team.md` owns the hooks).

Both modes add to the Socratic dialogue and never replace it.

**The critic is a red-team, not a proxy user.** It attacks the design and surfaces the owner's decisions; it never makes one. `skills/_brainstorm_shared/appetite_invariant.md` owns the classification. Here, a finding whose answer depends on a tunable appetite is a taste-fork the critic presents with options and never picks, above all under `--auto`, where no owner is present.

## When

- **Mode A:** at the `architecture_brainstorm` Step 7 review (`skills/architecture_brainstorm/reference/doc_and_review.md`), and for a design that spans Parts before design-lock (`skills/_brainstorm_shared/design_contract.md` clause 3).
- **Mode B:** at the Step 2, 4 and 5 hooks, only when the skill runs with `--red_team`.

## Arguments

- `/architecture_brainstorm_redteam <design.md> [<section>...]`: Mode A over the doc, or the sections named.
- Add `--auto` for the bounded loop below (Mode A only).
- Mode B is called by the skill's hooks with the decision, never typed.

With no argument, print these lines and stop. A design path that does not exist is named above the usage, and nothing is dispatched.

## 1. The subject

Make the run directory `.claude/scratch/redteam/<topic-slug>/`.

- **Mode A:** the design path, and the sections under review.
- **Mode B:** write `<run-dir>/decision.md` holding the Socratic question and either the owner's tentative pick or the live candidate approaches. No drafted doc exists yet, so never write one.
- **Both:** the existing owner families and authored slots that `architecture_brainstorm` Step 3 verified in the touched domain, as paths (`skills/architecture_brainstorm/reference/socratic_and_approaches.md` §Step 3 — procedure).

## 2. Seat the lenses

- **Core five:** `rt-boundary`, `rt-abstraction`, `rt-failuremode`, `rt-yagni-scope` and `rt-testability`. Mode A and the Step 5 hook seat all five. An earlier Mode B hook seats the lenses whose `interleave:` line names its step.
- **Triggered:** seat `rt-systemic` when a neighbouring committed design shares a touched subsystem, and add those designs' paths to the subject. Seat `rt-vision` when the design changes what the product offers its users, and add the path of the vision owner the project's `CLAUDE.md` names.
- **Bespoke:** for each distinct, committed design risk no seated lens covers (a concurrency bet, a migration, a rollback), add a lens whose mandate names that failure mode and its evidence. Only Steps 4, 5 and 7 and Mode A have committed shape to attack.
- **Completeness:** the brief also asks which failure mode no seated lens hunts. The answer seeds the next pass's bespoke lenses, or the report names it as an accepted gap. It never widens the current pass.

Seat the lenses once per pass. `--auto` rounds reuse the first round's set.

## 3. Review

Run `/review` §1 to §3 on the subject, with these changes:

- **The questions** are `.claude/commands/agents/redteam_agents.md` with the seated lenses named, and each bespoke mandate written out, in place of `/review`'s six.
- **The seats.** Mode A is architecture-scale, so it takes the cross-system seat as well. Mode B takes one seat.
- **The claim check** runs as written. A Mode B quote cites `<run-dir>/decision.md`.

Dispatch is mandatory: a model grading its own design gives up the independence the pass exists for. When no seat can launch, report `BLOCKED: no seat launched` and stop.

**Liveness.** A seat that returns no findings file, or whose `## Coverage` lacks a seated lens, leaves the pass INCOMPLETE, never APPROVE: report `panel incomplete — <n>/<seated> lenses returned; cannot certify`. Then re-dispatch the missing lenses, or halt.

## 4. Report

Read every finding before deciding any. Design-holes fold into the design: the session applies each one it keeps, per `/review` §4. Taste-forks go to the owner, and dead-ends halt.

### Mode A

```
─── DESIGN-HOLES (fold into the design) ───
<each kept design-hole: the section and the correction>

─── TASTE-FORKS (the owner's; the critic did not decide them) ───
<each: the question, the options with their wins-when conditions, why it is taste>

─── DEAD-ENDS (no fix within the constraints) ───
<each: the constraint, and whether to reframe or re-architect>

DESIGN-HOLES:N   TASTE-FORKS:M   DEAD-ENDS:K   (lenses returned: X/<seated>)
```

| Verdict | Condition | The skill's next step |
|---|---|---|
| APPROVE | no kept design-hole, no dead-end, every seated lens returned | the owner review, with the taste batch |
| REVISE | a kept design-hole, no dead-end | fold, then re-review only when `/review` §4's round test admits it |
| HALT | any dead-end | reframe the constraints or start a fresh `/architecture_brainstorm` pass; never loop |
| INCOMPLETE | a seated lens did not return | re-dispatch the missing lenses; never read as APPROVE |

### Mode B

Print this block and return to the skill:

```
─── red-team · <the question, tentative pick or approaches> ───
DESIGN-HOLE (adjust the current approach): <kept design-holes, or "none">
TASTE → next Socratic question: <each taste-fork, phrased as the question to ask the owner>
DEAD-END: <only if one surfaced: halt and surface to the owner>
(lenses returned: X/<seated>)
```

A design-hole adjusts the tentative pick or approach before the next question. A taste-fork becomes the next Socratic question. A dead-end halts the brainstorm for a reframe.

## `--auto` (Mode A, trusted topics)

`--auto` folds design-holes without the owner, for topics the owner trusts the agents to harden. Under `/architecture_brainstorm --red_team --auto` it runs as the Step 7 pass. The Mode B hooks stay owner-gated.

Each round runs §3, and the session folds the kept design-holes into the doc. A second round runs only when the fold changed the design's principles, solution or reach (`/review` §4). After that, the remaining findings go to the owner. Every taste-fork joins a running batch as an open placeholder the design is parameterized over, never as a resolved decision.

Three checks run after each round:

- **Liveness:** every seated lens returned, or the pass is INCOMPLETE and halts.
- **Dead-end:** any dead-end heading halts unconditionally. Reframe; do not iterate.
- **No owner:** a taste-fork is never resolved under `--auto`. It only joins the batch.

The output is a draft for review, never an approved design. The owner answers the taste batch first, and the design is reconciled to those answers. That answered batch is the approval (`skills/_brainstorm_shared/design_contract.md` clause 8).

## Rationalizations to refuse

| Rationalization | Reality |
|---|---|
| "Run all five lenses between every Socratic question." | Most lenses need committed shape. An early hook seats the lenses its step names (§2). |
| "This question looks analytical; the critic can answer it." | An answer that depends on coupling, scope, risk or feel appetite is taste: present options, never pick. |
| "`--auto` converged, so the design is ready." | `--auto` output is a draft. The owner still answers the taste batch and approves. |
| "No findings came back, so the design is clean." | A missing findings file or coverage line is INCOMPLETE, not APPROVE. |
| "This taste-fork doesn't block the design; fold it in quietly." | A fork that sets a convention, default or naming precedent binds later work: surface it. |
| "It's a dead-end, but keep looping to fix it." | A dead-end halts (`--auto` checks). |
| "I'll answer the lenses myself and save a dispatch." | Dispatch is mandatory. A model critiquing its own design is the failure the pass exists to prevent. |
| "Use this instead of the Socratic brainstorm to save time." | It sharpens questions and catches holes. It does not replace the owner's design conversation. |
| "Add a bespoke 'overall quality' lens to catch the rest." | A bespoke lens names the failure mode it hunts. The completeness question is the catch-all, and it feeds the next pass. |
