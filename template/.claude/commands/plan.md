---
description: Turn a design topic, Part, MVP checkpoint or owner design doc into an approved plan and its state file
---

# /plan — design and plan to the owner's approval

**Input:** `/plan <intake> | <slug>`. The four intake kinds:

1. **A design topic:** free text, or an `ideation-complete` idea-bank doc or cluster.
2. **An `arch-pending` or `arch-rework` Part**, resolved on the nearest-ancestor `roadmap.md`.
3. **An MVP checkpoint** (`MVP-N` in that roadmap's `## MVP Checkpoints`): its unchecked Required Parts, in Pos order, are the topic.
4. **An owner-authored design doc**, the usual intake for NPCs, features and systems.

A `<slug>` resumes the initiative in `.claude/work/<slug>.md`. A `plan-pending` Part's design is already locked: `/drive <part>` plans and ships it. With no argument, ask which topic before doing anything.

**Ends at:** the owner's design-lock and approval stop. `/plan` never builds; `/drive` ships what the owner approved. It never enters the client's Plan Mode, which cannot write the plan and state files this command produces.

## Owner's words and decisions

The state file carries the initiative across compactions and sessions, and the plan file carries what an executor needs. Write the state file before the first dispatch, then update it in the turn each fact arrives. Writing it points this session at it, so the compact hook re-prints it.

```markdown
# <title>
**Plan:** .claude/plans/<slug>.md  **Roadmap:** <roadmap path — Part, or ad-hoc>  **Base:** <HEAD sha at grounding>
## Goal
## Owner decisions        <- each quoted, with its source (session id, message, doc path)
## Agent decisions        <- each marked agent, with its reason
## Slices                 <- one line per job-map row: id, status (todo | in progress | done | halted: <cause>), commit
## Open questions
## Standing facts         <- push authority and other standing permissions, each in the owner's words; for each outward action, the tool or file that owns its call
```

The owner's words go in verbatim. A summary of them is an agent decision and is marked as one. A recipe (a command line, a publish step) is never copied into the state file. Name the tool that owns it, because a copied recipe goes stale when the tool changes.

## Steps

1. **Ground.** Run the existing-doc check ([`existing_doc_check.md`](../skills/_brainstorm_shared/existing_doc_check.md) §1), which dispatches [`/explore`](explore.md). This is the grounding floor, so it is one dispatch, not two. Record `HEAD` as **Base** in the state file; `/plan_part` reads it to check only what changed since. A covering design doc means resume or extend it (§1.1), not re-design it. A premise the dossier contradicts reframes the topic now.
   - For an owner-authored doc, the doc is the candidate pool and the work is refinement, never ideation. Its sections are constraints to confirm, not specifications to trust. Record its commitments and open forks, check each scope-defining premise against code, and record each superseded claim.
   - With no candidate approach at all (no idea-bank doc, canonical pattern or owner framing), stop and route to `/idea_brainstorm`. Ideation is pure taste.
   - Work with no nameable oracle (no test, run or person that can show it works) is not ready. Ground it further, spike it, or reshape it.
2. **Size.** State the size as the first output after grounding, with its reason:
   - **parts:** some milestone cannot be planned until earlier work lands (it needs a playtest, a measurement or a landed contract), or the whole cannot be planned and reviewed as one plan. This yields one design doc and roadmap Parts, and one plan per Part.
   - **milestones:** one plan with checkpoints where the drive reports.
   - **one-context:** one sitting carries it. The plan is short: each required section of [`plan_file_format.md`](../skills/_brainstorm_shared/plan_file_format.md) is one line.
3. **Design** (only when the design is open). Satisfy every clause of [`design_contract.md`](../skills/_brainstorm_shared/design_contract.md). How you get there (what you read, which agents you send, in what order) is your judgment under `orchestration`. The design's first section answers the simpler-path question: the simplest change that meets the goal, and why the design is or is not it.
   - Classify each fork by [`appetite_invariant.md`](../skills/_brainstorm_shared/appetite_invariant.md). Append it to the topic's `decisions.md` `## Frontier` in the turn you classify it, so a dead session resumes from the file. Fog goes to `## Fog`, and out-of-scope calls go to `## Out of scope`.
   - Canon answers the questions an interactive brainstorm would ask. What canon cannot answer joins the fork batch. Do not replay `architecture_brainstorm`'s Socratic pacing: it exists so taste can enter continuously, and here taste enters at the stop.
   - A taste fork in work a person sees goes to the project's UI skill's review loop where the project has one. That loop holds only the piece it shows.
   - At size parts, the design doc passes an independent challenge before the stop: `/architecture_brainstorm_redteam` Mode A `--auto` (design contract clause 3). Below parts, plan review is the design's one review.
4. **Plan.** Write `.claude/plans/<slug>.md` per [`plan_file_format.md`](../skills/_brainstorm_shared/plan_file_format.md). Its header carries `**Roadmap:** <path> — Part **<ID>**`, or `**Roadmap:** ad-hoc`. At size parts, write the plan for each Part whose seams are settled; a later Part is planned by `/drive` once the work it depends on lands.
5. **Review.** Run [`/review`](review.md) plan mode on the plan, and on the design doc at size parts. Small, local, reversible work skips it. A fix to a shared contract or to a file in the top 5% by fix commits is never small. Fold each finding. A second round runs only when the first round's fold changed the plan's principles, solution or reach, and there are at most two rounds. A plan with a known critical finding is never presented. A finding that resolves to a taste fork joins the batch.
6. **Owner stop: design-lock and approval.** Present one packet (design contract clause 8): the design summary, the batched forks as one frontier round, the decision ledger, the per-clause compliance report, the plan path and the review result. Give consequential drift, contradicted premises and the review's outcome in the message, not in the plan. Read `Claude/Meta/Development-Focus.md` and name any misalignment with the focus. When an MVP recommendation fires (`architecture_brainstorm` Part authoring, the three conditions), put it in this packet with its required Playtest plan.
   - The answered batch is the approval. Reconcile the doc, plan and state file to the answers. Ask again only when reconciling adds content no answer implies, or two answers conflict.
   - After approval at size parts, run `/update_roadmap` to apply the Parts, and present its diff. It is the only writer of `roadmap.md`.
7. **Hand off.** Print the resume line `/drive <slug>` and stop.

## Stops before the owner stop

Each stop states the evidence ("plan says X / reality is Y") and ends the run. A missing Part, or a Part in a state this command does not take, is a pre-flight refusal: name the redirect (`/plan_part`'s state table) and stop.

- A scope-defining premise fails, and grounding cannot settle it.
- The file list cannot be bounded. Enumerate first, or split into Parts by behaviour. A count written `unknown` is never a bound.
- A blocking taste fork the design cannot proceed past. N batched forks at the stop are a success state, not a stop.
- Review stops converging: revisions stop adding evidence, or a critical finding has no fix.
- An existing owner (a family, a module, or another session's in-flight plan over the same files) already owns the concern. Add the work to that owner or ask the owner of the project. Never write a parallel plan.

## Rationalizations to refuse

| Rationalization | Reality |
|---|---|
| "The owner invoked an autonomous command, so this taste fork is my call." | Invocation delegates process, never appetite. Batch it. |
| "Design-lock is a formality for this small topic." | The blast-radius litmus scales the stop's depth, never its existence (clause 8). |
| "I critiqued the design myself while drafting." | Self-review never substitutes for independent review (clause 3). |
| "This decision is obvious; no ledger entry needed." | An unlogged decision is an unauthorized one (clause 6). |
| "I'll write the frontier up when I present." | A fork is logged in the turn it is classified (clause 2). |
| "The design is in the conversation, so skip the plan file." | Conversation context dies at compaction; the plan and state file survive it. |
