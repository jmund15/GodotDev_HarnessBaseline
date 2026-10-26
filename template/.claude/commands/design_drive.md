---
allowed-tools: Workflow, Agent
description: Drive multi-Part design work to shipped code or design-only
---

# /design_drive — contract-driven autonomous architecture

**Input:** a design topic: free text, an `arch-pending` / `arch-rework` Part name (resolved on the nearest-ancestor `roadmap.md`), an MVP checkpoint (`MVP-N` in that roadmap's `## MVP Checkpoints`: its unchecked Required Parts, in Pos order, are the topic), an `ideation-complete` idea-bank doc or cluster, or a user-authored design doc.
**Enters at:** ground, then design.
**Owner stop:** design-lock (`design_contract.md` clause 8); the spine's halts apply.

Run the stages, halts, slices and close in [`drive_spine.md`](../skills/_brainstorm_shared/drive_spine.md). This file holds only what a design topic adds.

**Invocation:** `/design_drive <topic-or-part> [--design-only]`

- `<topic-or-part>`: the input above. No argument → ask which topic before doing anything.
- **Default is through-implementation:** after design-lock, the drive carries the design to shipped code — the user asked for the feature, not a document. `--design-only` stops at the approved design and roadmap (use when the user says design/architect only, or the work is handed to other sessions).

## Entry routing

Which entry command fits a piece of work (this one, `/architecture_brainstorm`, `/part_drive`) follows [`execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md) §Entry routing; this command's own input is the `<topic-or-part>` above.

**Preflight (before any exploration):** load the doctrine per [`design_contract.md`](../skills/_brainstorm_shared/design_contract.md) clause 1, so it is citable. Read `Claude/Meta/Development-Focus.md` and report topic misalignment per [`plan_file_format.md`](../skills/_brainstorm_shared/plan_file_format.md)'s focus-note rule.

**Entry preconditions (halt, don't improvise):**

- Candidate ideas EXIST — idea-bank doc, mature-domain canonical patterns, or the user's own framing. Greenfield with no candidates → halt per the Entry routing above; ideation is pure taste and has no autonomous variant by design.
- Run the [`existing_doc_check.md §1`](../skills/_brainstorm_shared/existing_doc_check.md) existing-doc check first. That check dispatches [`/explore`](explore.md), so it doubles as this command's exploration FLOOR — one dispatch, not two. At that dispatch, record the grounding base in the drive's task record: `task_record.py add <task> decision "design-lock base <HEAD sha>"`. A design doc already covering the topic → resume or extend per §1.1, rather than re-designing. Absorb premise-contradiction claims as [`drive_spine.md`](../skills/_brainstorm_shared/drive_spine.md) §Stages Ground requires: an ungroundable premise caught here is a topic reframe, caught later it is spine H1.

### User design doc intake (`<topic>` = a user-authored design doc)

When the topic is a user-authored design doc — the recurring intake shape for NPCs, features, and
systems — the preconditions above hold, with one addition: the doc IS the candidate pool, and the drive
is refinement-plus-continuation, never ideation. Before drafting from a user-authored doc, record its commitments and open forks, establish shipped state and worklog context, verify scope-defining premises against code, and persist new user rulings when they arrive. Read in the order that exposes those facts with the least duplicate work.

A user doc's sections are constraints to CONFIRM, not specifications to trust. Use its heading outline and per-section intent to classify decisions, ideas and open questions; use `/explore` claims and the session-once worklog-relevance check to establish what shipped and what prior drive/audit artifacts say; verify each scope-defining premise against code and record superseded claims; append user corrections and routing directives to `decisions.md` when they arrive.

## Stance

**The contract is fixed; the process is yours.** Satisfy every clause of [`_brainstorm_shared/design_contract.md`](../skills/_brainstorm_shared/design_contract.md). HOW you get there — what you read, what agents you spawn, exploration order and depth — is your judgment per the `orchestration` skill, **above the `/explore` floor from the entry preconditions.** The floor removes only one option: entering design with no established state at all. Everything above it stays judgment, and the dossier's evidence-backed claims are what contract clause 1 cites for codebase state. A gnarly seam deserves a comparative deep-read of the two subsystems it joins; a wide space deserves fan-out; a constrained space deserves neither. Do NOT reproduce `architecture_brainstorm`'s Socratic and approach procedures by rote: that skill's pacing exists to let a human's taste enter continuously; here taste enters at design-lock, so pace for design quality instead. The live-option litmus in the `architecture_brainstorm` skill's Socratic-and-approaches Step 4 procedure still governs any options you present at the gate — filler options are enumeration theater in any mode.

Canon answers the Socratic questions the human-in-the-loop flow would ask (`design_contract.md` clause 1). Whatever canon cannot answer joins the taste-fork batch. Append every fork to `decisions.md` `## Frontier`, every fog entry to `## Fog` and every out-of-scope call to `## Out of scope` in the same turn, so a dead drive resumes from the file (`design_contract.md` *The batch persists as it accumulates*).

This command authorizes `Workflow` for any stage shape: adversarial panel, exploration fan-out, competing design drafts plus a judge panel. Per-call model and effort pins follow `orchestration` §5.

Part authoring, doc self-review and roadmap routing follow `design_contract.md` clause 5.

## Halts

Halts are the spine's ([`drive_spine.md`](../skills/_brainstorm_shared/drive_spine.md) §Halts). Here, an ungroundable scope-defining question is H1 or H2. A blocking taste-fork, an appetite question the design depends on ([`appetite_invariant.md`](../skills/_brainstorm_shared/appetite_invariant.md)), is H3. A dead-end panel verdict is H5. Incomplete panel coverage that targeted recovery cannot close is H4, so a partial panel is never clean. Design-lock always fires as the owner stop.

N batched taste-forks are a SUCCESS state, not a halt ([`appetite_invariant.md`](../skills/_brainstorm_shared/appetite_invariant.md) §Why this is load-bearing).

## Size at entry

This command runs only for size = parts (`execution_depth.md` §Entry routing). Read size against the Size litmus as the first output after the `/explore` floor, before any design work, and state it in the design-lock report. A size below parts stops here and hands the dossier to `/feature_drive`, which reuses it.

## After design-lock approval

The answered batch is the approval (`design_contract.md` clause 8): reconcile the doc to the answers and proceed.

Run steps 1–3.

1. Invoke `/update_roadmap`, present its diff informationally and apply it (clause 5 — single executor; the child-subfolder case uses the two invocations in the `architecture_brainstorm` skill's Doc-and-review Step 8 procedure). If the `architecture_brainstorm` skill's Part-authoring MVP recommendation (the three firing conditions) holds, surface it at design-lock — same message as the brainstorm skill, including the REQUIRED Playtest plan (roadmap_triggers_mvp.md §6.11) — rather than silently skipping it. The user decides at the gate whether to run `/mvp_plan` now or defer.
2. With `--design-only`: report the recommended starting Part and stop.
3. Otherwise: carry every topic Part to shipped code on the spine's Parts path. A `plan-pending` Part runs `/part_drive <part>`. An `arch-pending` or `arch-rework` Part whose open piece needs no owner fork (a pin, a spike, an upstream Part) is this drive's design work under the approved lock: its pin or spike runs as a job once its inputs settle, `/update_roadmap` re-routes it, and it then runs `/part_drive`, with no second design-lock. An open piece that is an owner fork is H3.
   - **Halts:** the spine's Parts path and halts apply. A halt in any Part stops the chain; report Parts completed vs. remaining.
   - **Drafts:** chain mode pins `orchestration` reference `drive_dispatch.md` *Plan drafts*.
   - **No `--plan-only`:** design-lock approved every Part, so a converged plan goes straight to execution.
   - **Playtest surfacing:** after each `/part_drive`'s `/update_roadmap` run, if any MVP's Status flipped to `🧪 Ready for playtest`, surface it with its Playtest plan (roadmap_triggers_mvp.md §6.11). The drive ends at a playtestable state, and the user should playtest before `/mvp_plan verify`. If a flipped MVP has no Playtest plan, name the gap explicitly (the `/update_roadmap` Step 5 warning fires it) rather than reporting the chain clean.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "The user invoked an autonomous command, so this blocking taste-fork is my call" | Invocation delegates process, never appetite. Spine H3. |
| "Design-lock is a formality for this small topic — fold it into the close-out" | The blast-radius litmus scales the *depth*, never the *existence*, of the gate (clause 8). |
| "The contract is satisfied in spirit; skip the compliance report" | Clause 8. |
| "I critiqued the design myself while drafting" | Clause 3. |
| "This decision is obvious; no ledger entry needed" | Clause 6. |
| "I'll write the frontier up when I present at design-lock" | Clause 2. |
| "Safest to reproduce the brainstorm procedure verbatim" | Clause 5. If you want the pipeline, run `/architecture_brainstorm`. |
