---
allowed-tools: Bash(git grep:*), Bash(git ls-files:*), Bash(python3 .claude/tools/review_ledger.py:*), Bash(python3 .claude/tools/lens.py:*), Bash(python3 .claude/tools/post_check.py:*), Glob, Grep, Read, Workflow
description: Red-team a drafted architecture or a live Socratic design step
---

# /architecture_brainstorm_redteam — Adversarial Design Red-Team

Red-teams an architecture design for rigor holes, then classifies every finding as a **rigor-hole** (analytical — Claude/the design can address it) or a **taste-fork** (depends on the user's vision/appetite — human-only). It runs in **two modes**:

- **Mode A — standalone (default):** one adversarial pass over a *drafted* design doc (or named sections) before it hardens into roadmap Parts.
- **Mode B — interleaved:** dispatched *by* [`/architecture_brainstorm`](../skills/architecture_brainstorm/SKILL.md) when that skill is invoked with `--red_team`, firing a phase-scoped lens subset *between* Socratic steps so taste-forks become the next question and rigor-holes adjust the live design.

Both modes **augment** the Socratic dialogue — they never replace it (`feedback_dont_compress_socratic_on_rich_prompt`).

## The safety crux (read first)

**The critic is a RED-TEAM, not a PROXY-USER.** It attacks the design's rigor and *surfaces* the decisions; it never *makes* a decision that is the user's to make.

**[Shared]** See [`_brainstorm_shared/appetite_invariant.md`](../skills/_brainstorm_shared/appetite_invariant.md); it is the load-bearing classification contract for the critic panel. Critic-specific application: a finding whose answer depends on a tunable appetite is a **TASTE-FORK** the critic *presents* (with options) and **NEVER picks** — in either mode, and most especially in `--auto` where no user is present.

## When to use

- **Mode B — interleaved:** the per-step adversarial pass during the `architecture_brainstorm` skill's Socratic-and-approaches Steps 2 and 4 and its Part-authoring Step 5 procedure. Its taste-forks become Socratic seeds for the user; its rigor-holes feed back into the live design before the next question or commitment review.
- **Mode A — standalone:** the full-doc hardening pass at the `architecture_brainstorm` skill's Doc-and-review Step 7 self-review checklist, before the design hardens into roadmap Parts. This is also the independent adversarial pass the self-review procedure invokes.

Whether the design challenge is admitted follows [`execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md) §Admission table, Design challenge row.

## Step 0: Gather the input (mode-dependent, Claude-side — push-don't-pull)

The critics judge **pushed** content; they do NOT discover (intermittent fan-out search returns false-empties — `gotcha_workflow_fanout_search_false_absence`). Claude assembles, via reliable reads/git:

- **Mode A (drafted):** the design doc text (or the specific sections under review) + the `architecture_brainstorm` skill's Socratic-and-approaches Step 3 procedure (verified existing owner families and authored slots in the touched domain — `git grep` for `abstract class` / `interface I` + the relevant `semantic-search` hits) + the domain's gotchas + the framework-boundary rule.
- **Mode B (interleaved):** the **single in-flight decision** — `{ the Socratic question, the user's tentative pick OR the live candidate approaches from the `architecture_brainstorm` skill's Socratic-and-approaches Step 4 procedure }` + the same abstraction inventory + domain gotchas. There is no drafted doc yet; do NOT fabricate one.

If Step 0.5 adds `rt-systemic` or `rt-vision`, extend the push accordingly: the relevant sections of adjacent topic-folder design docs (for `rt-systemic`) and a digest of the project's vision owner, the skill or doc its CLAUDE files name for product direction (for `rt-vision`). The lenses compare pushed adjacent-design excerpts; when excerpts are missing, report a coverage gap and obtain the relevant first-party source through a scoped search with liveness counts. Verify an empty result outside the fan-out before calling the design clean.

## Step 0.5: Compose the panel — fixed floor + risk-derived bespoke lenses

The core lenses (Step 1) are the **floor**: guaranteed-minimum coverage, never dropped from a first round within their phase eligibility. They are deliberately generic; extending the panel to fit THIS design is the orchestrator's judgment (`feedback_prescribe_verification_not_cognition` — which lenses to run is cognition; that the floor runs at the seat width `orchestration` §2 sets, with liveness and taste-classification enforced, is verification).

**Seat map** (`orchestration` §2 *Sizing the width*): one seat per admitted lens; when `orchestration` §2's size or room rule merges, merge in this order: first structure (`rt-boundary` + `rt-abstraction`); then scope (`rt-yagni-scope` + `rt-testability`); last those two seats with `rt-failuremode` as one seat for the core lenses. In round 1 the core five stay the floor within their phase eligibility. Conditional and bespoke lenses keep their own seats, and Mode B's phase subsets run one seat per lens. The review rung below sets a seat's effort.

**Conditional core lenses** — add each when `execution_depth.md` §Mandate admission admits it; `python3 .claude/tools/lens.py get --shared <keys>` returns its mandate (`rt-systemic`, `rt-vision`).

**Bespoke lenses** — start from the sensitivity tags the design touches (`execution_depth.md` §Sensitivity tags), then add risks derived from the design's profile: domains touched, memorialized-gotcha density, what the design uniquely bets on (concurrency, migration or rollback included), stated-direction evolution pressure. Add a bespoke lens for each distinct, uncovered, committed design risk; its mandate names the failure mode and available evidence. If the review no longer fits, partition the design review without losing a risk. Eligible only where the design has committed shape: Steps 4/5/7 and Mode A.

**Completeness critic (optional)** — ask an independent completeness mandate only: *"given this design, which failure mode does no dispatched lens hunt?"* Its answer feeds the NEXT round's bespoke slots or is reported as an accepted gap — it never expands the current round mid-flight.

Keep the core and triggered lenses; combine overlapping mandates only if each still reports coverage, and split independent risks across panels when a single panel exceeds the input or integration budget.

Compose ONCE per pass; `--auto` rounds reuse the round-1 composition, and liveness (Step 1) asserts against the composed set.

## Step 1: Dispatch the critic panel

**Workflow sub-agent dispatch is MANDATORY — do NOT self-critique inline.** A model grading its own design defeats the adversarial independence the panel exists to provide (mirrors `plan_check`'s MANDATORY-dispatch discipline). When Workflow is unavailable, report `BLOCKED: Workflow unavailable` before dispatch and stop: the pass records through its run id (Step 2), and a Task panel has none.

The core lenses (+ any Step 0.5 additions), each judging the pushed input. The mandates live in [`agents/redteam_agents.md`](agents/redteam_agents.md): run `python3 .claude/tools/lens.py get --shared <keys>` for only the seated lenses; `--shared` prepends the classification rule every lens applies, so each lens prompt carries it. Each lens's `interleave:` header line says which boundary it can fire at; most need committed design shape, so early Socratic steps run a reduced subset. The core five are `rt-boundary`, `rt-abstraction`, `rt-failuremode`, `rt-yagni-scope` and `rt-testability`.

**Phase-scoped subsets for Mode B:** a step seats the core lenses whose `interleave:` line names it, plus Step 0.5 additions once approaches have shape; Step 5 / Mode A seats all core lenses plus Step 0.5 additions.

**Model — pin every lens to the architect tier at its review rung, never the session model.** The ladder's selection line and *Review seats* bullet own the floor, and the fanout rows' `±` cells mark them down on architecture; a purely mechanical bespoke lens (rubric-matching, reference enumeration) may drop to that tier.

**Ledger plan.** Before each pass, write the composed seats to a roster file (`{key, mandates, tier, effort}`, where `mandates` is each lens's `mandates:` header line in the registry, pins resolved per `orchestration` §5) and run `python3 .claude/tools/review_ledger.py plan --stage design --subject doc:<design path> --roster <roster.json> --files <design path>`; dispatch only the seats it returns.

**Dispatch.** `review_fanout.js` §USAGE CONTRACT owns the flat `args` shape and the large-design fallback to `dispatch.js` brief files. Push the input once via `contextPrefix`; each lens `prompt` is its registry mandate only. The design path the Ledger plan names is each seat's must-read entry; write a design that is not yet a file to a scratch file first. Pass it as the seat's `mustRead` (`agents/review_agents.md` *Seat coverage contract*). Pass `args.ledger` `{stage: "design", subject: "doc:<design path>", roster: <roster.json>, scopeJson: <scope file>}`, where the scope file holds `{"files": ["<design path>"]}` so a pass with no fold still records its rows over the design; consume the result per [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md) Step 1.5.

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: {
    contextPrefix: "<the pushed input + abstraction inventory + gotchas, as ONE flat string>",
    agents: [
      { key: "rt-boundary",    prompt: "<mandate only>", tier: "architect", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
      { key: "rt-failuremode", prompt: "<mandate only>", tier: "architect", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] },
      { key: "rt-yagni-scope", prompt: "<mandate only>", tier: "architect", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] }
      // + rt-abstraction / rt-testability per the phase subset above (tier: "architect", model: "<architect>", effort: "<review rung>", mustRead)
      // + Step 0.5 conditional (rt-systemic / rt-vision) + bespoke design lenses (tier: "architect", model: "<architect>", effort: "<review rung>", mustRead)
      // a purely mechanical bespoke lens may drop to the fanout tier; the orchestrator tier only via an explicit per-lens pin
    ]
  }
})
```

**Liveness is mandatory — a silent-empty round is NOT a clean round.** `review_fanout.js` marks a lens that errors, times out or returns an invalid shape `failed` or `uncovered` in `perAgent` and raises a flag. Before reporting or converging, confirm every dispatched lens key reads as returned. A merged seat reports each mandate's coverage per `orchestration` §2 *Sizing the width*, and a mandate it did not reach counts as missing. Any missing lens is INCOMPLETE, never CLEAN: surface `panel incomplete — N/<dispatched> lenses returned; cannot certify` and re-dispatch the missing lenses or halt.

Each lens returns findings per the schema, classified by the registry's shared rule (rigor-hole → `FIX`/`PLAN`, taste-fork → `ASK`). One more class:
- **Dead-end** → a rigor-hole with **no addressable fix within the design's constraints** (e.g. "cannot satisfy invariant X without breaking the framework boundary"). Encoded as `critical: true, action: PLAN` and named DEAD-END in the description. A rigor-hole with a named fix is FIX or a non-critical PLAN; `critical: true, action: PLAN` is reserved for DEAD-END: in this command `critical` marks a dead-end and overrides the protocol's §Critical Flag, so a severe rigor-hole states its severity in `description`. It halts (see below) — it is not a taste-fork.

## Step 2: Report

The post-check recorded the pass from `args.ledger`.

### Mode A — the structured hand-off block

**Read every finding before adjudicating any.** The three groups below organize the report; they do not license reading only the critical group. A non-critical rigor-hole is still a defect with one right answer, and the cheapest place to catch it is before the design locks — a panel whose findings are partially read has been paid for and mostly wasted.

Present findings in three groups, then a machine-branchable verdict consumed by the `architecture_brainstorm` skill's Doc-and-review Step 7 self-review checklist:

```
─── RIGOR-HOLES (addressable — fold into the design) ───
<FIX/PLAN findings with the design section + the correction>

─── TASTE-FORKS (human-only — Socratic seeds; the critic did NOT decide these) ───
<ASK findings: the question + options with their wins-when conditions + why it's taste>

─── DEAD-ENDS (no fix within current constraints — escalate) ───
<critical PLAN findings; each recommends reframe or re-architecture>

RIGOR-HOLES:N   TASTE-FORKS:M   DEAD-ENDS:K   (lenses returned: X/<dispatched>)
```

| Verdict | Condition | Consuming-skill action |
|---|---|---|
| **CLEAN** | 0 rigor-holes, 0 dead-ends, X = dispatched (liveness OK); taste-forks batched | proceed to the user-review gate with the taste-batch |
| **HARDEN** | any rigor-hole, no dead-end | fold rigor-holes into the doc; re-review only the units holding design no reviewer saw, per `orchestration` §2 *Later rounds*; a reviewer's own fix that the post-check passes needs no re-review (`orchestration` §2 *Later rounds*) |
| **HALT** | any dead-end | reframe constraints or a fresh `/architecture_brainstorm` pass; do not loop |
| **INCOMPLETE** | X < dispatched (a lens didn't return) | cannot certify — re-dispatch missing lenses; never read as CLEAN |

- **Default Mode A does NOT auto-revise.** Rigor-holes feed back into the design (Claude folds them in, or the Socratic addresses them); taste-forks go to the user; dead-ends halt for a reframe.

### Mode B — the compact per-decision block

No full hand-off report — emit a tight block the arch skill folds back into the live loop, then return control:

```
─── red-team · <the question / tentative pick / approach under review> ───
RIGOR (adjust current approach): <FIX/PLAN findings, or "none">
TASTE → next Socratic question: <each ASK reframed as the question to ask the user next>
DEAD-END: <only if a critical PLAN surfaced — HALT and surface to the user>
(lenses returned: X/<dispatched>)
```

Routing back into `/architecture_brainstorm`: **rigor-holes** adjust the current tentative pick/approach before the next question; **taste-forks** become the **next Socratic question**; a **dead-end** halts the brainstorm for a reframe.

## Optional: `--auto` mode (Mode A only — trusted topics)

`/architecture_brainstorm_redteam <design> --auto` runs a bounded **generate ↔ critique ↔ revise** loop for topics you trust the agents to harden. (When reached via `/architecture_brainstorm --red_team --auto`, this loop runs as the **Step-7 Mode A pass**; the interleaved Mode B steps still run human-gated, never in `--auto`.) Per round: snapshot the design as a blob subject ([protocol Step 4 *Fold subject*](agents/orchestrator_action_protocol.md#fold-subject)): root `.`, files `<design path>`, out `<snapshot manifest>`. Then dispatch the panel with `args.spillDir` and `args.fold` set to one `subjects` entry `{root: ".", snapshot, files: [<design path>]}`; the fold addresses **rigor-holes only**, and the session consumes the round per `orchestrator_action_protocol.md` Step 1.5 → **every** taste-fork accumulates into a running batch as an **open placeholder the design is parameterized over** — load-bearing or not, NEVER absorbed as a resolved decision. Each round after the first judges only the units revised since the previous round (delta review, per `orchestration` §2 *Later rounds*). Converge on complete, live lens coverage with no new rigor-holes; halt on a dead-end or on non-convergence (`_brainstorm_shared/drive_spine.md` §Halts).

**Machine-checked valves (each closes a fabrication path the audit identified — do NOT rely on prose alone):**
- **Liveness valve.** Before treating "0 new rigor-holes" as convergence, assert the round's `perAgent` shows every lens the round seated returned. Any missing lens is the INCOMPLETE verdict above: HALT `panel incomplete — N/<seated> returned; cannot certify convergence`.
- **Dead-end valve.** After each round, scan returned findings for `critical === true && action === 'PLAN'`. Any hit → HALT unconditionally (this IS the dead-end shape per Step 1; do not depend on noticing the word "DEAD-END" in a description string). Reframe, don't iterate.
- **No-owner ASK valve.** **In `--auto` there is no owner present:** `ASK` findings are NEVER auto-resolved — they only accumulate into the taste-batch (`_brainstorm_shared/appetite_invariant.md`).

**Hard rules for `--auto`:**
- **The `--auto` output is always a DRAFT-FOR-REVIEW, never an approved design.** Convergence is advisory, not approval.
- **No taste-fork is ever auto-answered or silently absorbed** — `_brainstorm_shared/appetite_invariant.md` owns the rule, including the downstream-inherited default.
- **Batch-first, never deferred:** the user answers the taste-batch FIRST, then the design is reconciled against those answers. The answered batch is the approval per `_brainstorm_shared/design_contract.md` clause 8.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Run the full panel between every Socratic question." | Most core lenses need committed design shape — early-Step-2 they no-op. Mode B runs the phase-scoped subset (Step 1 table). Full panel is Step 5 / Mode A. |
| "This question looks analytical, the critic can just answer it." | If its answer depends on coupling/scope/risk/feel appetite, it's taste — present options, never pick. Erring toward taste is the safe default (`_brainstorm_shared/appetite_invariant.md`). |
| "`--auto` converged, so the design is ready." | `--auto` output is a draft for review. Convergence ≠ approval; the user still answers the taste-batch and approves. |
| "Zero findings came back — the design is clean." | A missing `perAgent` lens is INCOMPLETE, not CLEAN (Step 1 liveness). |
| "This taste fork doesn't block the current design, fold it in quietly." | Any fork that sets a convention/default/naming precedent is load-bearing-for-downstream → surfaced, never absorbed. |
| "It's a dead-end but let me keep the `--auto` loop iterating to fix it." | The dead-end valve HALTs on any `critical PLAN` (Step 1). |
| "Push the whole design into each critic prompt to be thorough." | Push it through the shared `contextPrefix` and keep prompts to mandates (Step 1 dispatch; `gotcha_workflow_args_generation_fidelity`). |
| "I'll just answer the panel mandates myself to save a dispatch." | Dispatch is MANDATORY. A model critiquing its own design isn't adversarial — it's the failure the panel exists to prevent. |
| "Use this instead of the Socratic brainstorm to save time." | It AUGMENTS the Socratic dialogue (`feedback_dont_compress_socratic_on_rich_prompt`); it sharpens questions and catches rigor holes; it does not replace the human design conversation. |
| "Add a bespoke 'overall quality' lens to catch whatever the core misses." | A bespoke mandate names the concrete failure mode it hunts (Step 0.5) — an unfalsifiable lens produces noise findings that cost triage. The completeness critic is the sanctioned catch-all, and it feeds the next round, not this one. |
| "More lenses = more rigor; add every imaginable concern." | Add a lens for each distinct, uncovered, committed design risk; partition independent risks when one panel exceeds the input or integration budget. |
