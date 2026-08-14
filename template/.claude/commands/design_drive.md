---
description: Autonomously drive a design topic to an approved design doc + roadmap Parts under the design contract — process free-form per orchestration judgment, invariants fixed, one human gate at design-lock.
---

# /design_drive — contract-driven autonomous architecture

**Invocation:** `/design_drive <topic-or-part> [--design-only]`

- `<topic-or-part>` — a design topic (free text), an `arch-pending` / `arch-rework` Part name (resolved on the nearest-ancestor `roadmap.md`), or a path to an `ideation-complete` idea-bank doc / cluster. No argument → ask which topic before doing anything.
- **Default is through-implementation:** after design-lock, chain `/part_drive` per authored `plan-pending` Part in dependency order — the user asked for the feature, not a document. `--design-only` stops at the approved design + roadmap (use when the user says design/architect only, or the roadmap is handed to other sessions).

## When to use (vs. siblings)

| You want | Use |
|---|---|
| Direct influence per decision — your taste enters continuously | `/architecture_brainstorm` (optionally `--red_team`) |
| A well-trodden shape on an existing Part (plan → ship) | `/part_drive` — add `--plan-only` to stop at an approved plan |
| A large or novel design run autonomously, taste batched to one gate | **this command** |

**Preflight (before any exploration):** load `architecture_philosophy` — plus `jmodot` and/or `pp_subsystems` when the topic touches those surfaces. Contract clause 1 makes the doctrine a citable grounding source, and an unloaded skill cannot be cited. Do not treat the auto-loaded `rules/design_litmus.md` as the doctrine; it is the pointer to it. Read `Claude/Meta/Development-Focus.md`; name any misalignment between the topic and the current focus in the preflight summary — advisory, never a gate.

**Entry preconditions (halt, don't improvise):**

- Candidate ideas EXIST — idea-bank doc, mature-domain canonical patterns, or the user's own framing. Greenfield with no candidates → halt and route to `/idea_brainstorm`; ideation is pure taste and has no autonomous variant by design.
- Run the [`common.md §1`](../skills/_brainstorm_shared/common.md) existing-doc check first. That check dispatches [`/explore`](explore.md), so it doubles as this command's exploration FLOOR — one dispatch, not two. A design doc already covering the topic → halt and surface it (resume/extend per §1.1, don't re-design). Absorb every `premise-contradiction` claim before drafting: an ungroundable premise caught here is a topic reframe, caught later it is valve (a).

## Stance

**The contract is fixed; the process is yours.** Satisfy every clause of [`_brainstorm_shared/design_contract.md`](../skills/_brainstorm_shared/design_contract.md). HOW you get there — what you read, what agents you spawn, exploration order and depth — is your judgment per the `orchestration` skill, **above the `/explore` floor from the entry preconditions.** The floor removes only one option: entering design with no established state at all. Everything above it stays judgment, and the dossier's evidence-backed claims are what contract clause 1 cites for codebase state. A gnarly seam deserves a comparative deep-read of the two subsystems it joins; a wide space deserves fan-out; a constrained space deserves neither. Do NOT reproduce `architecture_brainstorm` Steps 2–4 by rote: that skill's Socratic pacing exists to let a human's taste enter continuously; here taste enters at design-lock, so pace for design quality instead. The live-option litmus (`architecture_brainstorm` Step 4) still governs any options you present at the gate — filler options are enumeration theater in any mode.

Socratic questions the human-in-the-loop flow would ask the user are answered from canon per contract clause 1; whatever canon can't answer joins the taste-fork batch (clause 2) — appended to `decisions.md` `## Frontier` in that same turn, along with every fog entry and out-of-scope call ([`common.md §8`](../skills/_brainstorm_shared/common.md)). The frontier file is what makes this command resumable: a drive that dies mid-flight resumes from it, not from chat.

This command authorizes `Workflow` for any stage shape — adversarial panel, exploration fan-out, competing design drafts + judge panel, or anything else `orchestration` judgment calls for (per-call `model` + `effort` pins per the CLAUDE.md ladder + `orchestration` §5).

Where the contract binds process anyway (clause 5): Part authoring runs `architecture_brainstorm` Step 5's sub-procedures, the doc self-review runs its Step 7 checklist, and roadmaps route through `/update_roadmap`. Those are artifact gates, not exploration.

## Halt valves

- **(a) Ungroundable scope** — a scope-defining question no canon source answers, and the design can't reach a batchable state without it.
- **(b) Blocking taste-fork** — an appetite question (per [`appetite_invariant.md`](../skills/_brainstorm_shared/appetite_invariant.md)) the rest of the design depends on; deferring it to the batch would fabricate the answer implicitly.
- **(c) Dead-end** — the adversarial panel returns a dead-end verdict (design invalid under stated constraints) → halt for reframe.
- **(d) Panel integrity** — adversarial panel INCOMPLETE (missing lens / liveness failure) twice.
- **(e) Design-lock** — always fires; the gate per contract clause 8.

Accumulating N batched taste-forks is a SUCCESS state, not a valve (appetite invariant).

## After design-lock approval

**The answered batch IS the approval.** Once the user has answered every batched taste-fork, the design is approved — reconcile the doc to the answers and proceed directly. Do NOT ask a separate approve-the-design question, and do NOT re-gate the `/update_roadmap` batch (present its diff informationally and apply; the user redirects after the fact). A follow-up approval question is warranted ONLY when reconciliation introduces design content that is not a direct consequence of a batch answer, or when answers conflict and force a redesign.

1. Invoke `/update_roadmap` (clause 5 — single executor; multi-roadmap child-subfolder case per `architecture_brainstorm` Step 8's two-invocation sequence). If the Step 5 *MVP recommendation* conditions hold (≥5 Parts, top-level roadmap, no MVP section), surface that recommendation at design-lock — same message as the brainstorm skill, including the REQUIRED Playtest plan (common.md §6.11) — rather than silently skipping it. The user decides at the gate whether to run `/mvp_plan` now or defer.
2. Without `--through-implementation`: report the recommended starting Part and stop.
3. With it: for each `plan-pending` Part in dependency order, run `/part_drive <part>`. Its valve set applies unchanged — taste-forks now halt IMMEDIATELY, not batch: reversibility drops at the implementation boundary and no batching point exists there. Chain mode pins `/part_drive` step 2's draft-delegation rule: plan DRAFTS dispatch to an executor-tier arm — over a chain the orchestrator's context is the binding constraint. A halt in any Part stops the chain; report Parts completed vs. remaining. After each `/part_drive`'s `/update_roadmap` run, if any MVP's Status flipped to `🧪 Ready for playtest`, surface it with its Playtest plan (common.md §6.11) — the drive ends at a playtestable state, and the user should playtest before `/mvp_plan verify`. If a flipped MVP has no Playtest plan, name the gap explicitly (the `/update_roadmap` Step 5 warning fires it) rather than reporting the chain clean.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "The contract is satisfied in spirit; skip the compliance report" | The per-clause report IS the verification surface — clause 8 requires it, including what couldn't be satisfied. |
| "I critiqued the design myself while drafting" | Clause 3: independence is the point, not the critique. Fresh dispatch or it didn't happen. |
| "This decision is obvious; no ledger entry needed" | Obvious decisions are exactly what the ledger exists for — unlogged = unauthorized (clause 6). |
| "I'll write the frontier up when I present at design-lock" | Append-on-classify (clause 2). A batch that exists only in session context is one crash from gone, and reconstructing it is what design-lock is too late for. |
| "The user invoked an autonomous command, so this blocking taste-fork is my call" | Invocation delegates process, never appetite. Valve (b). |
| "Safest to reproduce the 8-step brainstorm procedure verbatim" | Rote pipeline reproduction defeats the command — clause 5 names which gates bind; the rest is judgment. If you want the pipeline, that's `/architecture_brainstorm`. |
| "Design-lock is a formality for this small topic — fold it into the close-out" | The blast-radius litmus scales the *depth*, never the *existence*, of the gate (clause 8). |
