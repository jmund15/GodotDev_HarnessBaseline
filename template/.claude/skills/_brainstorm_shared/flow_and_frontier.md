# Phase cardinality, decision frontier, interview rounds (§7–§9)

> Detail body of the brainstorming shared surface, read at the step named in [`common.md`](common.md).
> Section numbers are stable across the split; a § cited here that is not in this file is mapped to its file by the `common.md` index.

---

## §7. Workflow phase cardinality

The three brainstorming phases (`/idea_brainstorm` → `/architecture_brainstorm` → plan production + impl) generally fan **forward**:

> 1 ideation → N architecture brainstorms → N×M implementation sessions

**But the topology is not strictly hierarchical.** All of the following are normal and supported:

| Flow | When it happens |
|---|---|
| **Forward fan-out** (default) | One ideation splits into N clusters; each cluster spawns its own arch brainstorm; each arch design produces M Parts; each Part eventually maps to an impl session. |
| **Cluster merge** | Two clusters from ideation collapse into one arch brainstorm because their solutions share architecture. |
| **Ideation skip** | Mature-domain topics (canonical patterns populate the space) go straight to arch brainstorm; no ideation phase fires. |
| **Reverse signal** | Implementation surfaces a design gap → Part State transitions to `arch-rework` (design gap) or `idea-rework` (creative gap). |
| **Zero-impl outcome** | Some arch designs produce no `plan-pending` Parts — the design IS the answer (a usage convention, naming standard, or pure architectural commitment). Valid and supported by the State vocab. |
| **Workshop terminate** | `→ workshop` clusters / `workshop-pending` Parts pause the chain pending user decision. |

**Plan production is owned by [`/part_drive`](../../commands/part_drive.md)** — `--plan-only` to an approval-ready plan, unflagged through to shipped code. The brainstorming surface describes the *handoff* (a `plan-pending` Part with bounded files-nameable scope), not their internals.

**Per-skill diagrams.** Each brainstorm SKILL keeps a slim "you are here" ASCII diagram at its own §3 for orientation; the canonical cardinality + flow vocabulary lives here.

---

## §8. Decision frontier (`decisions.md`)

Every topic folder owns a `decisions.md` beside its design docs — the durable record of what is decided, what is still open, and what was ruled out.

**Path:** `<topic-folder>/decisions.md`. Durable — never deleted, no gitignored variant.

**Schema** — six sections, all present from creation (an empty section reads as *nothing here yet*; a missing one reads as *never considered*). Bullets, not prose:

```markdown
## Destination     <- one or two lines: what this topic is for. Every session orients here first.
## Decided         <- one line per resolved fork + wikilink to the doc section carrying the detail
## Frontier        <- open, unblocked questions, each with its recommended answer
## Blocked         <- open question + the named question(s) that must resolve first
## Fog             <- in-scope, not yet phrasable as a precise question + the step that would sharpen it
## Out of scope    <- consciously ruled out + why; never graduates back
```

**Append-on-classify.** An entry lands in the SAME turn its fork is classified — a taste-fork batched per [`design_contract`](design_contract.md) clause 2, an open question raised in a frontier round (§9), an out-of-scope call. Never batched to design-lock or doc-save: a batch living only in session context is a batch a dead session loses.

**Fog litmus:** a question belongs in `## Frontier` only if it can be stated precisely NOW — *not* whether it can be answered now. Can't state it → `## Fog`, carrying the fact-gathering step (an `/explore` lens, a code read, a playtest) that would sharpen it into a Frontier question.

**No-fog gate:** a Part or slice is not sized, state-assigned, or locked while a decision governing it sits in `## Fog`. Run the named sharpening step first, or author the Part `arch-pending` with the fog entry as its Trigger.

**Out-of-scope is a scoping act, not a step on the route** — it stays out of `## Decided`, which records the route actually walked. Its section exists so the next session doesn't re-litigate it.

**Per-skill `## Decided` cadence** (on top of append-on-classify): `/idea_brainstorm` appends one block per cluster after user acknowledgement; `/architecture_brainstorm` one per design section after approval. Per-skill bullet schemas live in each SKILL's authoring step (idea Step 3 phase 6 / arch Step 5).

**Consumption:** the final `write_doc` call (idea Step 5 / arch Step 6) passes it as `reference_files=[<path>]` so the worker recovers commitment detail lost from chat memory. The design doc's `## Decision Ledger` is a link to this file's `## Decided`, never a second copy — one home per authored value.

---

## §9. Interview mechanic — frontier rounds

**Default for a fork BATCH: one round, every unblocked fork.** Number them; each carries a recommended answer and a one-line consequence of taking it. The user answers any subset; unanswered forks carry to the next round alongside whatever the answers unblocked. A fork whose answer depends on another fork still open in this round belongs to a LATER round.

**Delivery:** ≤4 forks with enumerable options → `AskUserQuestion` (recommended option first, per that tool's convention). Otherwise a numbered prose round.

**One question per turn is still right for a single DEEP fork** — one whose answer reshapes what the next question even is. Rounds own batches; single-question turns own depth.

**Facts are the agent's job, never the user's.** Every factual question in a round is resolved with tools BEFORE the round is presented; only judgment forks reach the user. A fact-gathering step still running is an unsettled prerequisite — only the forks downstream of it wait, so ask the rest of the round now.

**The round IS the frontier.** Every fork in it was appended to `decisions.md` `## Frontier` when it was classified (§8); answers move entries to `## Decided` in the turn they arrive. Composing a round is reading the frontier, not re-deriving it.

Every option offered must be live per the `architecture_brainstorm` Step 4 *live-option litmus* — filler options train rubber-stamping.
