---
allowed-tools: Glob, Grep, Read, Task, Workflow
description: Lens-diverse multi-agent idea divergence + independent critique for one brainstorm cluster — N generator lenses fan out, independent critics judge the merged pool, Claude curates. Fired by /idea_brainstorm --fan_out or standalone on a broad/enumeration-pressured cluster.
---

# /idea_brainstorm_fanout — Lens-Diverse Divergence + Independent Critique

Fans a single cluster's **Diverge** phase out across N generator agents — each carrying a **distinct divergence lens** — then runs an independent critique pass over the merged pool, and hands the raw pool + critic annotations back to [`/idea_brainstorm`](../skills/idea_brainstorm/SKILL.md) for curation. It replaces the single-agent Diverge sub-phase with a fan-out; it does **not** replace Filter / Hone / Cluster / present, the per-cluster user-react loop, or the Hard Gate — those stay in the skill's main loop.

- **Interleaved (default):** fired per-cluster by `/idea_brainstorm --fan_out` at the top of Step 3, before Filter.
- **Standalone:** `/idea_brainstorm_fanout <cluster>` on one broad cluster when the single-agent pass under-populated it.

It **augments** the curation pipeline — it never replaces it.

## The safety crux (read first)

**The fan-out is a GENERATOR + CRITIC, not a CURATOR.** It populates and annotates the candidate pool; it never decides what survives. Filtering, honing, ranking, the bracketed marker tags, and the per-cluster user-react pacing **stay with Claude** in the skill's main loop (`/idea_brainstorm` Step 3 phases 2–5). Curation is the skill's core value-add and is judgment — per the worker-delegation rule, judgment never leaves Claude. A fan-out that returns "here are the 5 best" has overreached: it must return the *raw pool + critic notes*, and Claude curates.

**Lens-diversity is the entire value lever — not agent count.** Four agents running the *same* prompt is four times the cost for ~1× the coverage. The value comes from each lens being a different generative constraint that surfaces candidates the others structurally cannot. If you can't articulate why each lens is orthogonal, you haven't designed the lens set yet (see the rubric below).

## When to use

- A cluster is **broad or enumeration-pressured** — the user asked for "every X you can think of," or the design axis is wide (many orthogonal sub-shapes).
- The single-agent Diverge pass **under-populated** a cluster (handoff-readiness criterion 1, approach-diversity, would fail).
- A cluster's completeness matters enough to justify an independent **coverage-gap** pass (what did all generators collectively miss?).

**Skip for** narrow/tactical clusters (single-agent Diverge is enough — fan-out adds noise and cost), mature domains (go straight to `/architecture_brainstorm`), and any cluster already past Filter. Default `/idea_brainstorm` stays single-agent; this is opt-in.

## Step 0: Assemble the cluster CONTEXT (Claude-side — push-don't-pull)

The generators judge **pushed** content; they do NOT discover. Fanned-agent `Grep`/`Glob`/`semantic-search` returns intermittent false-empties (`gotcha_workflow_fanout_search_false_absence`), so a generator told to "search the vault for the boundary" will fabricate a clean run off an empty read. Claude assembles ONE flat `contextPrefix` string from the live brainstorm + the design doc / scratch ledger already in context:

- **Scope** — IN (what this cluster owns) / OUT (what sibling clusters own; tell generators NOT to produce it).
- **Boundary** — the named systems the candidates must *reference, never redefine* (Resource shapes, type families, invariants). Quote them; do not make the agent re-derive them.
- **Raw seed pool** — the user-surfaced seeds, verbatim (expand around, don't restate).
- **Already-kept survivors** — so generators produce *different* candidates, not regenerate locked ones.
- **Tier / bin context** — the slots candidates will fill.

This is the same push-don't-pull discipline `/architecture_brainstorm_redteam` Step 0 uses, for the same reason.

## Step 1: Select the lenses (the design step)

Pick **~4 orthogonal generative constraints that span the cluster's design axis.** A lens is a *force function for invention*, not a topic label. The set is good when:

1. **Orthogonal** — each lens surfaces candidates the others structurally can't. Overlapping lenses ("by-type" + "by-category") waste a slot.
2. **Spanning** — together they cover the cluster's primary design axis (its win-conditions, its roles, its motivations — whatever the axis is).
3. **Balanced** — at least one **external-anchored** lens (canon-import — proven-fun prior art) and at least one **project-anchored** lens (theme / systems-exploit — fit + novelty). All-canon drifts off-theme; all-theme misses proven shapes.

**Do NOT hard-code a fixed lens list.** Different cluster TYPES need different lenses — the lens set is chosen per cluster, from a menu like:

| Lens archetype | Generative constraint |
|---|---|
| `canon-import` | Mine a named genre / source space; reskin each to the project's theme. |
| `axis-inversion` | Enumerate one categorical axis exhaustively (win-conditions, roles, motivations) — ≥1 candidate per axis value. |
| `theme-exploit` | Candidates that could ONLY exist given the setting's signature fiction — theme is load-bearing, not cosmetic. |
| `systems-exploit` | Candidates that lean hard on the project's signature mechanics (the mechanic is the *core* of the idea). |
| `by-taxonomy` | Fill the gaps in an existing taxonomy (roles, factions, tiers) — start from what exists, enumerate what's missing. |
| `by-composition` | How primitives GROUP / combine into higher-order units. |
| `by-counterplay` | Design around the opposing force — the counter, the risk, the failure mode, the tension. |

**Worked lens-sets (by cluster archetype — proof the rubric is generic, not a frozen list):**

- **Enumerable-content cluster** (e.g. *combat encounter archetypes* — the fight/challenge shapes): `canon-import` · `objective-inversion` (sweep the win-condition axis) · `theme-exploit` (fights that need the setting) · `systems-exploit` (fights built on the signature mechanic). *Axis = the win-condition space.*
- **Entity-roster cluster** (e.g. *enemy roster & compositions*): `by-role` (fill the combat-role taxonomy) · `by-faction-flavor` (express each role mechanically-differently per faction) · `by-composition` (how roles group into packs) · `by-counterplay` (enemies that make a specific counter load-bearing). *Axis = role × faction × grouping.*
- **Systems / economy cluster** (e.g. *meta-progression, reward loops*): `by-player-motivation` (what each candidate makes the player want) · `by-economic-loop` (the resource it sources/sinks) · `canon-import` (roguelike meta-systems) · `by-time-horizon` (in-run vs between-run vs long-tail). *Axis = the motivation × economy surface.*

The lens *keys* and *axis* change per cluster; the rubric (orthogonal, spanning, balanced) does not.

## Step 2: Dispatch

**Dispatch is MANDATORY — do NOT self-generate inline.** The independence is the value: one model brainstorming four lenses sequentially in its own context cross-contaminates them (lens 2 sees lens 1's output and converges). Separate agents stay blind to each other — that's what produces the divergence. Same discipline as `/architecture_brainstorm_redteam`'s MANDATORY-dispatch crux.

**Keep `args` FLAT** (`gotcha_workflow_args_generation_fidelity` — large/nested/escape-dense args produce malformed JSON that `JSON.parse` throws on). Push the CONTEXT ONCE via `contextPrefix`; keep each lens `instr` to its mandate only; declare per-cluster typed fields via the flat `genFields` descriptor list (the engine builds the schema from it — do NOT nest a JSON Schema object into `args`):

```
Workflow({
  scriptPath: ".claude/workflows/idea_fanout.js",
  args: {
    contextPrefix: "<the Step-0 CONTEXT as ONE flat string>",
    generators: [
      { key: "canon-import",        instr: "<mandate only>" },
      { key: "objective-inversion", instr: "<mandate only>" },
      { key: "theme-exploit",       instr: "<mandate only>" },
      { key: "systems-exploit",     instr: "<mandate only>" }
    ],
    genFields: [
      { name: "winAxis",   desc: "CompletionRule axis the candidate maps to", required: true },
      { name: "dependsOn", desc: "which referenced system it leans on" }
    ]
    // critics omitted -> engine defaults (dedup / fit / coverage-gap); pass args.critics:[{key,instr}] for cluster-specific mandates
    // model omitted per agent -> phase default (generators opus, critics sonnet); drop a mechanical lens to model:"sonnet" to save cost
  }
})
```

**Model — asymmetric defaults: generators default `opus`, critics floor to `sonnet`.** Divergence wants the strong model; rigor (dedup / fit / coverage-gap) does not — and fan-out only fires on breadth-critical clusters, so the generator spend is justified by the trigger. An omitted per-agent `model` takes its phase default — it must NOT silently inherit the session model (under Fable that would spawn Fable generators). Override per agent via `args.generators[i].model` / `args.critics[i].model`: drop a *mechanical* lens (axis-inversion / by-taxonomy) to `sonnet` to save cost, and reserve `fable` for an explicit max-fidelity request.

**Fallback (a JSON-parse failure on dispatch, or a very large CONTEXT):** dispatch the generators as parallel `Task` subagents — each one flat prompt (CONTEXT + its lens mandate inline) with an explicit `model: "sonnet"` (the `Task` path bypasses the engine's floor) and the `GEN_GUARD` discipline (work from CONTEXT only, no self-filter, no condense). Merge and critique by hand. This mirrors the redteam command's `Task` fallback.

## Step 3: Liveness, then hand back to Claude for curation

**The engine returns** `{ rawCount, candidates:[...], critiques:[{key,notes,proposedAdditions}], perGenerator:[{key,count,dead}], perCritic:[{key,notes,additions,dead}] }`.

**Liveness is mandatory — a silent-empty lens is NOT a clean lens.** A live divergence lens always returns ~`genCount` candidates, so a `perGenerator` entry with `count: 0` or `dead: true` means it errored / timed out / returned malformed JSON — NOT that the lens found nothing (`arch_rule_autonomous_loop_positive_liveness`). Before curating: confirm every generator's `count > 0` and `dead: false`, and every critic's `dead: false`. If any died → do NOT treat the pool as complete; re-dispatch the missing lens (or surface `fan-out incomplete — N/<dispatched> generators returned`).

**Then curation runs in the skill's main loop, NOT here.** Hand the raw pool + critic annotations back to `/idea_brainstorm` Step 3:

- **Merge** the `coverage-gap` critic's `proposedAdditions` into the pool (Claude re-types them — the engine returns them as name/shape/rationale stubs).
- **Top-up** — add any context-privileged candidates the orchestrator can draw from the live conversation (user tone, unstated intent, chat-only seeds the generators never received — they saw only the pushed CONTEXT). This is a top-up, NOT a parallel re-run of the general Diverge: lean on what only the orchestrator knows.
- **Filter** (skill Step 3 phase 2) — apply the explicit checklist; let the `fit` critic's `conflict`/`uncertain` notes inform the mechanic-fit cut, and surface `[mechanic-uncertain]` rather than silently culling.
- **Dedup** using the `dedup` critic's clusters as input, not gospel — Claude makes the keep call.
- **Hone → Cluster & rank → present** per cluster (skill Step 3 phases 3–5), then the per-cluster user-react loop. **One cluster at a time** — the fan-out runs per cluster, not all clusters at once.

The critique pass is advisory annotation for Claude's curation; it does not pre-decide survivors.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Run 4 agents on the same divergence prompt to get more ideas." | Lens-diversity is the lever, not agent count. Identical prompts = 4× cost, ~1× coverage. The engine rejects <2 lenses. |
| "The fan-out returned its top 5 — present those." | Overreach. It returns the *raw pool + critic notes*; Claude curates (Filter/Hone/rank). Curation is judgment and never leaves the main loop. |
| "Let the generators search the vault for the boundary themselves." | Fanned-agent search false-empties (`gotcha_workflow_fanout_search_false_absence`). Push the boundary into `contextPrefix`; generators work from it. |
| "Use one fixed lens list for every cluster." | Different cluster TYPES need different lenses. Pick ~4 per cluster via the rubric (orthogonal / spanning / balanced); the worked sets are examples, not a frozen menu. |
| "Critics are just the generators re-reading their own output." | Critics are FRESH, independent agents over the merged pool (`red_team_must_be_independent_dispatch`). Generators self-grading share premises and bias the verdict. |
| "Zero candidates came back from a lens — it found nothing." | A live divergence lens always returns ~`genCount`. `count:0`/`dead:true` = it died. Check `perGenerator` before curating; re-dispatch. |
| "Nest the per-cluster schema object into `args` so fields are typed." | A nested schema is the escape-dense `args` shape that breaks the Workflow call (`gotcha_workflow_args_generation_fidelity`). Use the flat `genFields` descriptor list; the engine builds the schema. |
| "Fan out all clusters in one Workflow, then curate the lot." | Per-cluster pacing is mandatory (skill Step 3). Fan out one cluster, curate, present, get the user-react — then the next cluster. |
| "Have the generators condense to their best few." | No-condense is load-bearing (`feedback_no_unilateral_condensation`): raw seeds must survive verbatim for Claude to expand. Generators return raw; the `GEN_GUARD` enforces it. |

## Cross-references

- [`/idea_brainstorm`](../skills/idea_brainstorm/SKILL.md) — fires this command at Step 3 via `--fan_out`; owns Filter/Hone/Cluster/present + the per-cluster user-react loop + the Hard Gate.
- [`/architecture_brainstorm_redteam`](architecture_brainstorm_redteam.md) — the mirror precedent (command + `review_fanout.js` engine + per-step skill hooks); shares the push-don't-pull, flat-`args`, MANDATORY-dispatch, and liveness discipline.
- `.claude/workflows/idea_fanout.js` — the engine (parameterized: `contextPrefix` + `generators` + optional `critics` / `genFields` / `genCount` / per-agent `model`).
- **File-based memory:** `gotcha_workflow_fanout_search_false_absence` (push-don't-pull), `gotcha_workflow_args_generation_fidelity` (flat args), `gotcha_workflow_single_flight_concurrency` (no nested fan-out of GdUnit4/LSP), `red_team_must_be_independent_dispatch` (fresh critics), `feedback_no_unilateral_condensation` (raw seeds survive), `arch_rule_autonomous_loop_positive_liveness` (silent-empty ≠ clean).
