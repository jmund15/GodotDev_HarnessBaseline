---
allowed-tools: Glob, Grep, Read, Workflow
description: Run lens-diverse fan-out on one broad or enumeration-pressured idea cluster; /idea_brainstorm invokes it with --fan_out
---

# /idea_brainstorm_fanout — Lens-Diverse Divergence + Independent Critique

Fans a single cluster's **Diverge** phase out across N generator agents — each carrying a **distinct divergence lens** — then runs an independent critique pass over the merged pool, and hands the raw pool + critic annotations back to [`/idea_brainstorm`](../skills/idea_brainstorm/SKILL.md) for curation. It replaces the single-agent Diverge sub-phase with a fan-out; it does **not** replace Filter / Hone / Cluster / present, the per-cluster user-react loop, or the Hard Gate — those stay in the skill's main loop.

- **Interleaved (default):** fired per-cluster by `/idea_brainstorm --fan_out` at the top of Step 3, before Filter.
- **Standalone:** `/idea_brainstorm_fanout <cluster>` on one broad cluster when the single-agent pass under-populated it.

## The safety crux (read first)

**The fan-out is a GENERATOR + CRITIC, not a CURATOR.** It populates and annotates the candidate pool; it never decides what survives. Filtering, honing, ranking, the bracketed marker tags, and the per-cluster user-react pacing **stay with Claude** in the skill's main loop (`/idea_brainstorm` Step 3 phases 2–5). Curation is the skill's core value-add and is judgment — per the worker-delegation rule, judgment never leaves Claude. A fan-out that returns "here are the 5 best" has overreached: it must return the *raw pool + critic notes*, and Claude curates.

**Lens-diversity is the entire value lever — not agent count.** Four agents running the *same* prompt is four times the cost for ~1× the coverage. The value comes from each lens being a different generative constraint that surfaces candidates the others structurally cannot. If you can't articulate why each lens is orthogonal, you haven't designed the lens set yet (see the rubric below).

## When to use

Fan-out is opt-in; default `/idea_brainstorm` stays single-agent. Whether it is admitted for a cluster follows [`execution_depth.md`](../skills/_brainstorm_shared/execution_depth.md) §Admission table. This command keeps the lens rubric below.

## Step 0: Assemble the cluster CONTEXT (Claude-side — push-don't-pull)

The generators judge **pushed** content; they do NOT discover. Fanned-agent `Grep`/`Glob`/`semantic-search` returns intermittent false-empties (`gotcha_workflow_fanout_search_false_absence`), so a generator told to "search the vault for the boundary" will fabricate a clean run off an empty read. Claude assembles ONE flat `contextPrefix` string from the live brainstorm + the design doc / `decisions.md` frontier already in context:

- **Scope** — IN (what this cluster owns) / OUT (what sibling clusters own; tell generators NOT to produce it).
- **Boundary** — the named systems the candidates must *reference, never redefine* (Resource shapes, type families, invariants). Quote them; do not make the agent re-derive them.
- **Raw seed pool** — the user-surfaced seeds, verbatim (expand around, don't restate).
- **Already-kept survivors** — so generators produce *different* candidates, not regenerate locked ones.
- **Tier / bin context** — the slots candidates will fill.

## Step 1: Select the lenses (the design step)

Pick **the orthogonal generative constraints that span the cluster's design axis.** Add a lens only when you can name candidates no chosen lens could surface; stop once the axis is spanned. A lens is a *force function for invention*, not a topic label. The set is good when:

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

**Dispatch is MANDATORY — do NOT self-generate inline.** The independence is the value: one model brainstorming four lenses sequentially in its own context cross-contaminates them (lens 2 sees lens 1's output and converges). Separate agents stay blind to each other — that's what produces the divergence.

**Keep `args` FLAT** (`gotcha_workflow_args_generation_fidelity` — large/nested/escape-dense args produce malformed JSON that `JSON.parse` throws on). Push the CONTEXT ONCE via `contextPrefix` (the engine prefixes it to every lens prompt, so a second push duplicates it into each); keep each lens `instr` to its mandate only; declare per-cluster typed fields via the flat `genFields` descriptor list (the engine builds the schema from it — do NOT nest a JSON Schema object into `args`):

```
Workflow({
  scriptPath: ".claude/workflows/idea_fanout.js",
  args: {
    contextPrefix: "<the Step-0 CONTEXT as ONE flat string>",
    generators: [
      { key: "canon-import",        instr: "<mandate only>", model: "<row>", effort: "<effort>", tier: "architect" },
      { key: "objective-inversion", instr: "<mandate only>", model: "<row>", effort: "<effort>", tier: "architect" },
      { key: "theme-exploit",       instr: "<mandate only>", model: "<row>", effort: "<effort>", tier: "architect" },
      { key: "systems-exploit",     instr: "<mandate only>", model: "<row>", effort: "<effort>", tier: "architect" }
    ],
    critic: { model: "<row>", effort: "<effort>", tier: "fanout" },
    genFields: [
      { name: "winAxis",   desc: "win-condition axis the candidate maps to", required: true },
      { name: "dependsOn", desc: "which referenced system it leans on" }
    ]
    // critics omitted -> the engine's dedup / fit / coverage-gap critics run on args.critic; pass args.critics:[{key,instr,model,effort}] for cluster-specific mandates
  }
})
```

**Model:** pin every generator and `args.critic` by the ladder's selection line: divergence is design work (architect tier), and criticism is a rubric check (fanout tier). A mechanical lens may take a fanout-tier row, with its `tier` beside the `model`.

**Fallback (a JSON-parse failure on dispatch, or a very large CONTEXT):** write each generator's flat prompt (CONTEXT + its lens mandate inline, under the `GEN_GUARD` discipline: work from CONTEXT only, no self-filter, no condense) to a scratch file and run them as one `/delegate` dispatch, each job pinned by the ladder's selection line. Merge and critique by hand.

## Step 3: Liveness, then hand back to Claude for curation

**The engine returns** `{ rawCount, candidates:[...], critiques:[{key,notes,proposedAdditions}], perGenerator:[{key,count,dead}], perCritic:[{key,notes,additions,dead}] }`.

**Liveness is mandatory — a silent-empty lens is NOT a clean lens.** A live divergence lens always returns ~`genCount` candidates, so a `perGenerator` entry with `count: 0` or `dead: true` means it errored / timed out / returned malformed JSON — NOT that the lens found nothing (`arch_rule_autonomous_loop_positive_liveness`). Before curating: confirm every generator's `count > 0` and `dead: false`, and every critic's `dead: false`. If any died → do NOT treat the pool as complete; re-dispatch the missing lens (or surface `fan-out incomplete — N/<dispatched> generators returned`).

**Then curation runs in the skill's main loop, NOT here.** Hand the raw pool + critic annotations back to `/idea_brainstorm` Step 3:

- **Merge** the `coverage-gap` critic's `proposedAdditions` into the pool (Claude re-types them — the engine returns them as name/shape/rationale stubs).
- **Top-up** — add any context-privileged candidates the orchestrator can draw from the live conversation (user tone, unstated intent, chat-only seeds the generators never received — they saw only the pushed CONTEXT). This is a top-up, NOT a parallel re-run of the general Diverge: lean on what only the orchestrator knows.
- **Filter** (skill Step 3 phase 2) — apply the explicit checklist; let the `fit` critic's `conflict`/`uncertain` notes inform the mechanic-fit cut, and surface `[mechanic-uncertain]` rather than silently culling.
- **Dedup** using the `dedup` critic's clusters as input, not gospel — Claude makes the keep call.
- **Hone → Cluster & rank → present** per cluster (skill Step 3 phases 3–5), then the per-cluster user-react loop. **One cluster at a time** — the fan-out runs per cluster, not all clusters at once.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Run 4 agents on the same divergence prompt to get more ideas." | The engine rejects <2 lenses. |
| "Critics are just the generators re-reading their own output." | Critics are FRESH, independent agents over the merged pool. Generators self-grading share premises and bias the verdict. |

## Cross-references

- [`/idea_brainstorm`](../skills/idea_brainstorm/SKILL.md) — fires this command at Step 3 via `--fan_out`; owns Filter/Hone/Cluster/present + the per-cluster user-react loop + the Hard Gate.
- The coding layer's architecture red-team command — the mirror precedent (command + `review_fanout.js` engine + per-step skill hooks); shares the push-don't-pull, flat-`args`, MANDATORY-dispatch, and liveness discipline.
- `.claude/workflows/idea_fanout.js` — the engine (parameterized: `contextPrefix` + `generators` + optional `critics` / `genFields` / `genCount` / per-agent `model`).
- **File-based memory:** `gotcha_workflow_fanout_search_false_absence` (push-don't-pull), `gotcha_workflow_args_generation_fidelity` (flat args), machine-wide single-flight tools (no per-agent test, build or language-server run), `feedback_no_unilateral_condensation` (raw seeds survive), `arch_rule_autonomous_loop_positive_liveness` (silent-empty ≠ clean).
