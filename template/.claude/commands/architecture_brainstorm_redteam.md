---
allowed-tools: Bash(git grep:*), Bash(git ls-files:*), Glob, Grep, Read, Task, Workflow
description: Adversarial red-team of an architecture design — parallel critic lenses classify findings rigor-hole (addressable) vs taste-fork (human-only). Two modes: standalone drafted-doc pass + interleaved per-Socratic-step; --auto folds rigor-holes only. Never decides taste.
---

# /architecture_brainstorm_redteam — Adversarial Design Red-Team

Red-teams an architecture design for rigor holes, then classifies every finding as a **rigor-hole** (analytical — Claude/the design can address it) or a **taste-fork** (depends on the user's vision/appetite — human-only). It runs in **two modes**:

- **Mode A — standalone (default):** one adversarial pass over a *drafted* design doc (or named sections) before it hardens into roadmap Parts.
- **Mode B — interleaved:** dispatched *by* [`/architecture_brainstorm`](../skills/architecture_brainstorm/SKILL.md) when that skill is invoked with `--red_team`, firing a phase-scoped lens subset *between* Socratic steps so taste-forks become the next question and rigor-holes adjust the live design.

Both modes **augment** the Socratic dialogue — they never replace it (`feedback_dont_compress_socratic_on_rich_prompt`).

## The safety crux (read first)

**The critic is a RED-TEAM, not a PROXY-USER.** It attacks the design's rigor and *surfaces* the decisions; it never *makes* a decision that is the user's to make.

**[Shared]** The rigor-hole-vs-taste-fork rule — lexical tripwire, always-taste set, errs-toward-taste default — lives in [`_brainstorm_shared/appetite_invariant.md`](../skills/_brainstorm_shared/appetite_invariant.md). Read it; it is the load-bearing classification contract for the critic panel. Critic-specific application: a finding whose answer depends on a tunable appetite is a **TASTE-FORK** the critic *presents* (with options) and **NEVER picks** — in either mode, and most especially in `--auto` where no user is present.

## When to use

- **Mode B — interleaved (Steps 2/4/5 of `/architecture_brainstorm --red_team`):** the per-step adversarial pass. Its taste-forks become Socratic seeds for the user; its rigor-holes feed back into the live design before the next question/section.
- **Mode A — standalone (at `/architecture_brainstorm` Step 7 spec self-review, or against any drafted design doc):** the full-doc hardening pass before the design hardens into roadmap Parts. This is also what the brainstorm's Step-7 "Mode A adversarial pass" invokes.

Skip for designs already approved (it's a pre-approval hardening tool) and for trivial/mechanical changes.

## Step 0: Gather the input (mode-dependent, Claude-side — push-don't-pull)

The critics judge **pushed** content; they do NOT discover (intermittent fan-out search returns false-empties — `gotcha_workflow_fanout_search_false_absence`). Claude assembles, via reliable reads/git:

- **Mode A (drafted):** the design doc text (or the specific sections under review) + the Step-3 abstraction inventory (existing 2+ subclass families in the touched domain — `git grep` for `abstract class` / `interface I` + the relevant `semantic-search` hits) + the domain's gotchas + the framework-boundary rule.
- **Mode B (interleaved):** the **single in-flight decision** — `{ the Socratic question, the user's tentative pick OR the 2–3 candidate approaches from arch Step 4 }` + the same abstraction inventory + domain gotchas. There is no drafted doc yet; do NOT fabricate one.

## Step 1: Dispatch the critic panel

**Sub-agent dispatch (Workflow or the Task fallback) is MANDATORY — do NOT self-critique inline.** A model grading its own design defeats the adversarial independence the panel exists to provide (mirrors `plan_check`'s MANDATORY-dispatch discipline). Inline self-review is the failure this command guards against.

Five lenses, each judging the pushed input (read-only; no tests, no LSP). The **interleave column** says which boundary each lens can fire at — four of five need committed design shape, so early Socratic steps run a reduced subset:

| key | lens | mandate | interleave eligibility |
|---|---|---|---|
| `rt-boundary` | framework boundary / layering | Does the design make Jmodot reference `{{PROJECT_NAME}}.*`, or leak game-specific logic into the framework? Cite the violated seam. | Step 2 (premise leak), 4, 5, 7 |
| `rt-abstraction` | missed existing abstraction | Does it invent a parallel type where an existing 2+ subclass family already covers the concern? Name the family from the pushed inventory (`feedback_inspect_existing_abstractions_first`). | Step 4, 5, 7 (needs a proposed type) |
| `rt-failuremode` | known-gotcha walk | Does it walk into a memorialized failure mode — init-timing, disposal/lifecycle, pooling reset scope, magnitude-as-type-discriminator, autoload order, OnExit/OnEnter clobber? Cite the gotcha. | Step 2 (tentative pick), 4, 5, 7 |
| `rt-yagni-scope` | YAGNI vs stated direction | Is it over-engineered for an *imagined* need? **A stated evolution direction is NOT a YAGNI hole** (CLAUDE.md Core Principle 8, *Modular when direction is known*) — only flag speculation the user did not state. Also flag >2-subsystem scope creep. | Step 2 (speculative premise), 4, 5, 7 |
| `rt-testability` | test-first feasibility | Can each commitment be driven test-first (Logic → concrete `[TestCase]` names; Gameplay → ISceneRunner or explicit subjective flag)? Feeds the plan stage. | Step 5, 7 (needs commitments) |

**Phase-scoped subsets for Mode B:** Step 2 → `rt-boundary` + `rt-failuremode` + `rt-yagni-scope` (premise-challenge only). Step 4 → those three + `rt-abstraction` (+ `rt-testability` if an approach already names test surface). Step 5 / Mode A → all five.

**Dispatch — keep `args` FLAT (per `gotcha_workflow_args_generation_fidelity`).** Push the input ONCE via the shared `contextPrefix`; keep each lens `prompt` to its mandate only (do NOT duplicate the design into every prompt — that deeply-nested, escape-dense shape makes the tool call's JSON malformed):

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: {
    contextPrefix: "<the pushed input + abstraction inventory + gotchas, as ONE flat string>",
    agents: [
      { key: "rt-boundary",    prompt: "<mandate only>" },
      { key: "rt-failuremode", prompt: "<mandate only>" },
      { key: "rt-yagni-scope", prompt: "<mandate only>" }
      // + rt-abstraction / rt-testability per the phase subset above
    ]
  }
})
```

**Fallback (large design or a JSON-parse failure on dispatch):** dispatch the lenses as parallel `Task` subagents instead — each carries one flat prompt (mandate + the input inline). This is the more robust shape for large prose and is the documented substitute when Workflow `args` would be heavy (`gotcha_workflow_args_generation_fidelity`).

**Liveness is mandatory — a silent-empty round is NOT a clean round.** `review_fanout.js` returns an **empty findings array for any lens that errors, times out, or returns malformed JSON** (its guard: `Array.isArray(r.findings) ? r.findings : []`). So "0 findings" can mean "ran and found nothing" OR "never ran." Before reporting or converging, read the workflow's `perAgent: [{key, count}]` and confirm **every dispatched lens key is present**. If any lens is missing → do NOT report CLEAN; surface `panel incomplete — N/<dispatched> lenses returned; cannot certify` and re-dispatch the missing lenses (or halt). This closes the false-absence path inside the red-team itself.

Each lens returns findings per the schema. **Classification embedded in every mandate:**
- **Rigor-hole** → `action: FIX` (a concrete design correction) or `PLAN` (needs a redesign pass). `category: rule | bug | improvement`.
- **Taste-fork** → `action: ASK` with `options[]` (viable alternatives, recommended-first) — the critic presents, **NEVER picks**.
- **Dead-end** → a rigor-hole with **no addressable fix within the design's constraints** (e.g. "cannot satisfy invariant X without breaking the framework boundary"). Encoded as `critical: true, action: PLAN` and named DEAD-END in the description. It halts (see below) — it is not a taste-fork.

## Step 2: Report

### Mode A — the structured hand-off block

Present findings in three groups, then a machine-branchable verdict (this IS the surface `/architecture_brainstorm` Step 7 consumes):

```
─── RIGOR-HOLES (addressable — fold into the design) ───
<FIX/PLAN findings with the design section + the correction>

─── TASTE-FORKS (human-only — Socratic seeds; the critic did NOT decide these) ───
<ASK findings: the question + options (recommended-first) + why it's taste>

─── DEAD-ENDS (no fix within current constraints — escalate) ───
<critical PLAN findings; each recommends reframe or re-architecture>

RIGOR-HOLES:N   TASTE-FORKS:M   DEAD-ENDS:K   (lenses returned: X/<dispatched>)
```

| Verdict | Condition | Consuming-skill action |
|---|---|---|
| **CLEAN** | 0 rigor-holes, 0 dead-ends, X = dispatched (liveness OK); taste-forks batched | proceed to the user-review gate with the taste-batch |
| **HARDEN** | ≥1 rigor-hole, 0 dead-ends | fold rigor-holes into the doc; re-review the touched sections |
| **HALT** | ≥1 dead-end | reframe constraints or a fresh `/architecture_brainstorm` pass; do not loop |
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

Routing back into `/architecture_brainstorm`: **rigor-holes** adjust the current tentative pick/approach before the next question; **taste-forks** become the **next Socratic question** (never a silent pick — the appetite invariant holds inside the live dialogue exactly as in Mode A); a **dead-end** halts the brainstorm for a reframe.

## Optional: `--auto` mode (Mode A only — trusted topics)

`/architecture_brainstorm_redteam <design> --auto` runs a bounded **generate ↔ critique ↔ revise** loop for topics you trust the agents to harden. `--auto` is **Mode-A-only** — a per-step auto-loop in Mode B would auto-advance the Socratic dialogue, violating the never-decide-taste crux. (When reached via `/architecture_brainstorm --red_team --auto`, this loop runs as the **Step-7 Mode A pass**; the interleaved Mode B steps still run human-gated, never in `--auto`.) Per round: dispatch the panel → Claude addresses **rigor-holes only** → **every** taste-fork accumulates into a running batch as an **open placeholder the design is parameterized over** — load-bearing or not, NEVER absorbed as a resolved decision. Converge when a round surfaces no new rigor-holes, or after **2 rounds**.

**Machine-checked valves (each closes a fabrication path the audit identified — do NOT rely on prose alone):**
- **Liveness valve.** Before treating "0 new rigor-holes" as convergence, assert the round's `perAgent` shows all 5 lenses returned. Any missing lens → HALT `panel incomplete — N/5 returned; cannot certify convergence`. A round where the critics silently failed is NOT a converged round.
- **Dead-end valve.** After each round, scan returned findings for `critical === true && action === 'PLAN'`. Any hit → HALT unconditionally (this IS the dead-end shape per Step 1; do not depend on noticing the word "DEAD-END" in a description string). Reframe, don't iterate.
- **ASK-override valve.** The [Orchestrator Action Protocol](agents/orchestrator_action_protocol.md) default-applies an `ASK` finding's first/recommended option when the user replies tersely. **That default is OVERRIDDEN in `--auto`:** with no user present, `ASK` findings are NEVER auto-resolved — they only accumulate into the taste-batch. Following the linked protocol's default-apply here would silently resolve taste.

**Hard rules for `--auto`:**
- **The `--auto` output is always a DRAFT-FOR-REVIEW, never an approved design.** Convergence is advisory, not approval.
- **No taste-fork is ever auto-answered or silently absorbed** — not even a low-stakes-looking one (a naming/convention/default pick a later Part would inherit is treated as load-bearing-for-downstream and halts the loop immediately).
- **Two-step approval, never combined:** the user answers the taste-batch FIRST → the design is reconciled against those answers → THEN approval is requested on the reconciled design. There is no single "approve-design-and-defer-taste" action.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Run the full 5-lens panel between every Socratic question." | Four of five lenses need committed design shape — early-Step-2 they no-op. Mode B runs the phase-scoped subset (Step 1 table). Full panel is Step 5 / Mode A. |
| "This question looks analytical, the critic can just answer it." | If its answer depends on coupling/scope/risk/feel appetite, it's taste — present options, never pick. Erring toward taste is the safe default (`_brainstorm_shared/appetite_invariant.md`). |
| "`--auto` converged, so the design is ready." | `--auto` output is a draft for review. Convergence ≠ approval; the user still answers the taste-batch and approves. |
| "Zero findings came back — the design is clean." | Check the `perAgent` liveness count first. `review_fanout.js` returns `[]` for a lens that errored/timed-out; 0 findings with a missing lens is INCOMPLETE, not CLEAN. |
| "This taste fork doesn't block the current design, fold it in quietly." | Any fork that sets a convention/default/naming precedent is load-bearing-for-downstream → surfaced, never absorbed. |
| "It's a dead-end but let me keep the `--auto` loop iterating to fix it." | The dead-end valve HALTs on any `critical PLAN`. No fix exists within the constraints — reframe, don't loop. |
| "Push the whole design into each of the 5 critic prompts to be thorough." | That's the deeply-nested, escape-dense `args` shape that breaks the Workflow call (`gotcha_workflow_args_generation_fidelity`). Push ONCE via `contextPrefix`; mandates only in prompts; or fall back to parallel `Task`. |
| "I'll just answer the panel mandates myself to save a dispatch." | Dispatch is MANDATORY. A model critiquing its own design isn't adversarial — it's the failure the panel exists to prevent. |
| "Use this instead of the Socratic brainstorm to save time." | It AUGMENTS the Socratic dialogue (`feedback_dont_compress_socratic_on_rich_prompt`); it sharpens questions and catches rigor holes; it does not replace the human design conversation. |
