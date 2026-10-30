# Socratic, Inventory & Approach Mechanics

> Detail layer for [`architecture_brainstorm/SKILL.md`](../SKILL.md) — read at Steps 1–4 (and the §4 flag block) while executing those steps. Each `Step N — procedure` section is the full body of that step; the sections after it carry the detail that body names.

## Flag mechanics

Read at the §4 opening, before Steps 2/4/5/7.

**`--red_team`.** Fires an adversarial [`/architecture_brainstorm_redteam`](../../../commands/architecture_brainstorm_redteam.md) **Mode B** pass at the Socratic boundaries where load-bearing commitments form. Every hook judges only the decision no reviewer has seen: the newly picked answer, the drafted approaches, or the new decision group. A design-hole needs an evidence-backed resolution folded into the current pick; a taste-fork joins the owner's decision ([`appetite_invariant.md`](../../_brainstorm_shared/appetite_invariant.md)); a dead-end halts for a reframe.
- **Step 2:** after the user answers, before the next question, on the tentative pick, with `rt-boundary` + `rt-failuremode` + `rt-yagni-scope`, the three that fire pre-commitment. Taste-forks append to `## Frontier` for the next round.
- **Step 4:** once approaches are drafted; the Step-4 subset adds `rt-abstraction`, plus `rt-testability` if an approach names test surface. Taste-forks surface as the choice the user makes *between* approaches.
- **Step 5:** once a decision group is drafted, before owner approval, an independent panel for that group.

**`--design_panel`.** Off by default, independent of `--red_team`; fires once, at Step 4's *Constraint-diverse design panel*, before the approach converges.

**`--auto`.** Governs ONLY the Mode A pass at [Step 7](doc_and_review.md#step-7--procedure) and its bounded generate↔critique↔revise loop. Never drives Mode B interleaving — auto-advancing live Socratic dialogue auto-answers taste-forks (appetite-invariant violation). Valid without `--red_team`.

## Step 1 — procedure

**[Shared]** [`existing_doc_check.md`](../../_brainstorm_shared/existing_doc_check.md) §1 owns the three required asks. Step 3 owns the FULL abstraction inventory.

The digest ALSO surfaces any `ideation-complete` idea bank (`ideas.md` in a topic folder) on this topic. Exists → load it; Steps 2 and 4 draw from it. Absent and not a mature domain → redirect to `/idea_brainstorm`.

**Consumption mode** — cluster-scoped vs whole-doc default → §*Step 1 — cluster-scoped vs whole-doc consumption* below. It fixes this invocation's scope and Step 6's filename.

**Spawn-placement decision** (this session spawns a child, or is one): [`doc_conventions.md §5.1`](../../_brainstorm_shared/doc_conventions.md) — three placements discriminated by a three-criterion test, mapped by the §5.1 table (NOT "all three must hold"). Resolve here so Step 6's save path is unambiguous. Which criterion picks each placement → §*Step 1 — the three spawn placements*.

## Step 1 — cluster-scoped vs whole-doc consumption

**Cluster-scoped consumption** (upstream `ideas.md` has Per-Cluster Routing) — scope this invocation to the named cluster only:

- Read the cluster section: survivors (Step 4 sourcing), Findings (priors), Open Questions (Socratic seeds), Routing (timing).
- Other clusters → SEPARATE invocations producing separate `arch-<cluster>.md` in the SAME parent folder.
- Cross-cluster hybrids → SEPARATE arch session per hybrid (`arch-<hybrid-slug>.md`, same parent folder).
- Cross-cluster Open Questions are not this session's responsibility.
- Frontmatter: `derived_from: ../ideas.md`, `cluster: <cluster-slug>`.

**Default (no Per-Cluster Routing):** whole-doc consumption (mature domain, user-direct framing, legacy doc) → fresh topic folder with `arch.md` per `doc_conventions.md §5`.

## Step 1 — the three spawn placements

- Just an extension paragraph → **same-folder** (no new roadmap).
- Own brainstorm session + independent maturity, parent-confined audience → **child subfolder** (submap). Criterion 3 ("only serves this parent") distinguishes submap from sibling and is not a submap gate (§5.1 *Common gotchas*).
- All three pass (multi-parent audience) → **sibling folder**.

## Step 2 — procedure

**[Shared]** Interview mechanic — [`flow_and_frontier.md §9`](../../_brainstorm_shared/flow_and_frontier.md): a round presents every unblocked judgment fork at once, numbered, each with a recommended answer and its consequence; facts get resolved with tools before the round goes out; a single deep fork still gets its own turn.

- Append every fork to `decisions.md` `## Frontier` as classified, before the round is presented ([`§8`](../../_brainstorm_shared/flow_and_frontier.md)). Fog litmus (§8) decides Frontier vs `## Fog`: can you state the question precisely now — not answer it.
- Every option must be live per Step 4's **live-option litmus** — winnable under some appetite. Filler trains rubber-stamping.
- Stop asking when the space is narrow enough for concrete approaches.
- Run the full Socratic even when the prompt front-loads candidates (`feedback_dont_compress_socratic_on_rich_prompt.md`).
- **Sourcing:** §*Step 2 — question sourcing*.

The always-on design-litmus seeding (subclass axis · authored field · required system · behavior bool) → §*Step 2 — design-litmus seeding*. The `--red_team` hook → §*Flag mechanics*.

## Step 2 — question sourcing

- **Sourcing:** upstream idea bank → its honed candidates; else canonical patterns + existing codebase abstractions.

## Step 2 — design-litmus seeding

**Design-litmus seeding (always).** The moment a tentative pick introduces a structural element, ask its litmus in that step — Step 7's audit is the backstop, not the first encounter:
- new subclass → the axis question (`architecture_philosophy` §Orthogonal axis);
- new `[Export]`/authored field → home, consumer, one-concern;
- new required system → ownership seam + misconfiguration failure mode;
- new bool selecting behavior → which existing `*Strategy`/`*Config` family owns the concern.

## Step 3 — procedure

Before proposing approaches, inventory existing owners in the domain, including bases with a single implementer and strategy/config slots. Apply [`architecture_philosophy`](../../architecture_philosophy/SKILL.md) *Coupling & Discovery* (*Node Retrieval & Coupling*, *Interface Usage*) as a design step — cross-link, never restate.

- Extend an existing hierarchy rather than inventing a parallel abstraction (`feedback_inspect_existing_abstractions_first.md`).
- Read the implementation **body** of anything the design consumes, not just its contract (`feedback_inventory_implementation_not_just_contract.md`).

Re-run an inventory subset when a staleness risk factor holds — factors and drift gotchas → §*Step 3 — inventory-currency check (gate before Step 4)*.

## Step 3 — inventory-currency check (gate before Step 4)

**Inventory-currency check (gate before Step 4).** Re-run an inventory subset when, since the initial inventory, a relevant file, branch, submodule pointer or consumed contract changed, or the source cannot be shown current. Check `git status` for upstream work and `git submodule status` for pointer drift; stale inventory yields approaches modeled on deleted/renamed types or missing shipped infrastructure. Trust test-file `using` imports over directory existence when checking greenfield-vs-shipped. Drift gotchas: `archive_git_submodule_pr_merge_strategy.md`.

## Step 4 — procedure

The deliverable is a **legible choice**, not a fixed option count. Classify the space first, because the shape sets how options are presented. **Decision-shaped:** two or more approaches are each winnable under some appetite; present each with trade-offs and a recommendation, differing on a load-bearing axis (ownership, lifecycle, coupling direction, data flow), not cosmetics. **Constraint-shaped:** constraints force one approach; present it, the constraints that force it and the **nearest rejected alternative with the specific constraint that kills it**, and never pad to hit a count.

**Live-option litmus** (every alternative here, every Step 2 option): *"what appetite or evidence would make this option win?"* No answer → filler. Naming it a rejected-alternative-with-reason is honest.
**Discriminator for the one-approach case:** constraint-shaped requires the Step 3 inventory as backing — you can name the constraint that kills the nearest alternative. Can't → back to Step 3, not forward with padding.

**Check the frame first:** if every candidate feels forced, the question may be wrong — reopen Step 2 with the reframe.

Approach presentation template, idea-bank sourcing and the `--design_panel` blind-arm procedure (mandates, dispatch, output schema, judging-stays-here rule) → §*Step 4 — approach template, sourcing, design panel*. The `--red_team` hook → §*Flag mechanics*.

## Step 4 — approach template, sourcing, design panel

```
Approach A — <one-line summary>
  Trade-offs: <pros> // <cons>
  Code shape: <the paths or classes that distinguish this approach>
  Recommendation: <yes/no/conditional>

Approach B — ...
Approach C — ...
```

**Sourcing:** with an upstream idea bank, approaches are *clusters of related candidates* — architectural framings; the idea-bank entries are the instances those framings generate.

**Constraint-diverse design panel (`--design_panel` only).** Before converging, dispatch independent blind design attempts for the distinct live design constraints, using the same chosen module and identical context plus one constraint mandate per arm. Choose as many nonredundant mandates as this design needs; each arm must not see the others.

- **Mandates** (choose the nonredundant live constraints): minimize the interface by exposing only entry points actual callers require · maximise flexibility · optimise for the most common caller (default case trivial) · ports-and-adapters.
- **Dispatch** through `/delegate`: one job per arm, `{label, promptPath, model, effort, shape: "author"}` plus the jobs file's `spillDir`, with `model` (an architect-tier row) and `effort` resolved for this session's transport through `orchestration`. Each arm's prompt goes to a scratchpad file, never inlined into `args` (`gotcha_workflow_args_generation_fidelity`). Arms author PROPOSALS into their spill files; no repo edits.
- **Fixed per-arm output schema**, which makes the designs comparable: `approach-name` · `component-list` · `key-seam-contracts` (invariants, ordering, error modes) · `what-this-optimizes` · `what-this-sacrifices`.
- **Judging never leaves this session.** Pick a winner, graft runner-up ideas surviving on merit (a hybrid is a legitimate winner), state ONE opinionated recommendation. A neutral menu of N designs is a failed panel; taste-forks go to the frontier round (`flow_and_frontier.md §9`), not an unranked list.
