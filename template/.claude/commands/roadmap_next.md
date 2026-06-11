---
description: Scan all roadmaps ({{PROJECT_NAME}} + Jmodot trees) for actionable Parts (deps satisfied per state predicate), score by Leverage + State-Proximity + Readiness + MVP-Demand, recommend top 3. State filter + optional scope (pp-only / jmodot-only); default excludes idea-pending + user-owned + workshop-pending.
---

# /roadmap_next

Surveys every `roadmap.md` under `BrainstormingDesigns/` in **both** the {{PROJECT_NAME}} and Jmodot vault trees, filters Parts to a configurable state set (and optional scope), applies per-state "actionable" predicates (deps satisfied where deps apply), scores survivors on four mechanically-computable criteria, and recommends the top 3 with next-action handoffs.

**Companion to `/plan_part`** (per-Part briefing) and `/update_roadmap` (Part state transitions). This command answers "**what should I work on next?**" across the full roadmap surface; the others answer "how do I work on Part X?"

**Read-only.** Parses roadmaps, computes scores, prints a report. No file writes, no agent dispatch.

---

## When to invoke

User-typed at the start of a planning session, or when:
- Just finished shipping a Part and want to know what's freshly unblocked.
- Looking for the highest-leverage next pickup across multiple roadmaps.
- Auditing whether `## Currently ready to execute` derived views are stale (this command bypasses derived views — reads Parts tables directly).

## When to skip

- **Single specific Part in mind already** — go straight to `/plan_part <name>` or `/architecture_brainstorm`.
- **In-progress execution session** — focus on current Part; don't survey the queue mid-task.

---

## Arguments

| Form | Behavior |
|---|---|
| `/roadmap_next` | Default: state set `plan-pending`, `arch-pending`, `arch-rework`, `idea-rework`; scope **both** ({{PROJECT_NAME}} + Jmodot). |
| `/roadmap_next plan-pending` | Single state filter. |
| `/roadmap_next plan-pending arch-rework` | Multi-state filter (positional, space-separated). |
| `/roadmap_next pp-only` | Scope filter — recommend only {{PROJECT_NAME}} Parts (`pp` accepted). |
| `/roadmap_next jmodot-only` | Scope filter — recommend only Jmodot Parts (`jmodot` accepted). |
| `/roadmap_next plan-pending pp-only` | Scope + state tokens compose, any order. |
| `/roadmap_next --all` | Include `idea-pending` + `workshop-pending` + `submap-pending` in the filter. `user-owned` always excluded. |
| `/roadmap_next --recommend-only` | Skip the full actionable table; show top-3 only. |

**Scope** narrows which Parts are *recommended*, never which are *analyzed* — both vault trees are always discovered + parsed so cross-tree Leverage stays accurate (Phase 1, Anti-patterns). Scope vocabulary: `both` (default) / `pp` / `pp-only` / `jmodot` / `jmodot-only`.

**Excluded states (never reported):**
- `complete` / `abandoned` — no work to do.
- `user-owned` — not agent-runnable; surface via `/worklog` triage if blocking.

---

## State-specific "actionable" predicate

| State | Actionable when... | Next-action handoff |
|---|---|---|
| `plan-pending` | every Dep in state `complete` | `/plan_part "<name>"` |
| `arch-rework` | all Deps `complete` OR no Deps; Trigger names concrete drift | `/architecture_brainstorm` |
| `arch-pending` | all Deps `complete` OR no Deps; Trigger has design surface | `/architecture_brainstorm` |
| `idea-rework` | always actionable | `/idea_brainstorm` |
| `idea-pending` | only with `--all` flag (greenfield = "what to build" not "what's ready") | `/idea_brainstorm` |
| `workshop-pending` | only with `--all` flag (user decision required) | user-decision-then-route |
| `submap-pending` | only with `--all`; recurse into child roadmap referenced in Source cell | enter child roadmap |

---

## Scoring (0–12 total, four criteria)

### 1. Leverage (0–3) — downstream Parts unblocked
- `0` — leaf (no downstream Parts depend on it)
- `1` — unblocks exactly 1 downstream Part
- `2` — unblocks 2 downstream Parts
- `3` — unblocks 3+ Parts OR is named as a cross-roadmap dep (e.g., a Part in roadmap A is named as Dep by a Part in roadmap B)

Computation: build full reverse dep graph across ALL roadmaps. Cross-roadmap deps surface via Source-cell `[[../<other-roadmap>/...]]` wikilinks and explicit "Cross-roadmap references" sections.

### 2. State-Proximity (0–3) — activation energy to ship
- `3` — `plan-pending` (one plan-mode session → execution)
- `2` — `arch-rework` (re-brainstorm → plan → execution)
- `1` — `arch-pending` OR `idea-rework` (one brainstorm round → plan → execution)
- `0` — `idea-pending` (full pipeline; rare to be top-3 even with `--all`)

### 3. Readiness Signal (0–3) — how well-anchored
For `plan-pending`:
- `3` — Source-cell anchor resolves cleanly; design doc has API + Test Pin block for this Part
- `2` — Source-cell present but multi-anchor (Part spans 2+ design sections)
- `1` — Source-cell present but anchor vague or doc thin
- `0` — Source-cell missing or unresolved

For `arch-*`:
- `3` — Trigger names concrete files/types/patterns ("CombatLogger precedent", "extends X family")
- `2` — Trigger conceptual but bounded ("re-evaluate inheritance vs composition")
- `1` — Trigger exploratory ("explore the design space")

For `idea-*`:
- `3` — user has stated direction explicitly in roadmap notes
- `1` — default (idea space is open by definition)

### 4. MVP Demand (0–3) — incomplete-MVP reach
Counts the **incomplete MVPs** (per `## MVP Checkpoints` §6.11; an MVP is incomplete unless its Status is `✅ Verified`) within the candidate's **one-hop reach** = `{MVPs that list the candidate itself as a Required Part}` ∪ `{MVPs that list any *direct dependent* of the candidate}`. Set-union over distinct MVPs — a Part that both serves MVP-1 and unblocks another MVP-1 Part counts MVP-1 once.
- `0` — in the reach of no incomplete MVP (← every candidate on a roadmap with no `## MVP Checkpoints` lands here, making this criterion inert)
- `1` — reaches exactly 1 incomplete MVP
- `2` — reaches 2
- `3` — reaches 3+ incomplete MVPs **OR** completing the candidate would *close* an incomplete MVP (it is that MVP's only remaining unchecked Required Part, with no unresolved cross-roadmap clause blocking closure)

**Reach is one-hop and within-roadmap:** `direct-membership(candidate) ∪ ⋃ membership(direct dependents)`, same-roadmap MVPs only — cross-roadmap clauses (no `#Parts` link) don't contribute. (Why one-hop and why coequal-capped: Anti-patterns.)

**Shared-path caveat (don't "fix"):** when a dependent has *other* unmet deps, completing the candidate alone won't advance that MVP — reach over-credits, intentionally (the candidate is still on the critical path). Consistent with `Leverage`, which also counts a dependent without checking its other deps.

### Tie-breakers (apply in order)
1. **Closes an MVP** wins (candidate is an incomplete MVP's last unchecked Required Part — completing it flips that MVP to `🧪 Ready for playtest`, the strongest ship signal a single Part can produce).
2. Higher State-Proximity wins (closer to ship beats higher leverage at distance).
3. More recent Dep-completion (look at the Dep's `Shipped <date>` text in its Trigger cell; most recent unblock = freshest signal).
4. Smaller roadmap (fewer total Parts) — focus benefit.

---

## Procedure

### Phase 1 — Discover roadmaps + parse args

1. `Glob` for `BrainstormingDesigns/**/roadmap.md` under **both** vault trees, tagging each result with its `tree` (`pp` | `jmodot`):
   - **{{PROJECT_NAME}}** — `{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\Claude\BrainstormingDesigns\`
   - **Jmodot** — `{{VAULT_ROOT}}\DevProjects\Jmodot\Claude\BrainstormingDesigns\`
2. Parse positional args. Match each token against the **scope set** {`both`, `pp`, `pp-only`, `jmodot`, `jmodot-only`} first, then the **state set**. Empty state args → default state set; no scope token → `both`. `--all` flag adds `idea-pending`, `workshop-pending`, `submap-pending`. `--recommend-only` toggles output verbosity.
3. **Scope filters the candidate pool, NOT discovery.** Always glob + parse BOTH trees regardless of scope, so Phase 3 builds the complete cross-tree dep graph — a `pp-only` Part's Leverage must still count Jmodot dependents (e.g. PP's `Graph Engine Core` is depended on by Jmodot procgen Parts; PP `hub-world` depends on Jmodot `grab→jmodot`). The scope token is applied in Phase 4 to gate which Parts can be *recommended*.
4. If a positional token ∉ scope set ∪ known states → emit usage table and exit.

### Phase 2 — Parse Parts tables (bundled)

Per CLAUDE.md §9 — 3+ files for synthesis → route through `mcp__ai-worker__read_files`:

```
read_files(
  paths=[<each roadmap.md absolute path>],
  question="For each roadmap, extract every Parts table row as a structured record:
            {roadmap, tree (pp|jmodot — infer from the path), pos, part_name, state,
             deps (parsed to list), trigger (TRUNCATED to first 400 chars),
             source (TRUNCATED to first 200 chars)}.
            Also extract any 'Cross-roadmap references' section listing this roadmap's
            Parts named as Deps by other roadmaps.
            Also, if a '## MVP Checkpoints' section exists, extract per MVP:
            {mvp_id, status (the Status line: In progress / Ready for playtest / Verified),
             required_parts (the Required-Parts checkbox list — for each, the linked Part
             name from [[#Parts|<Part Name>]] and whether it is checked [x] or unchecked [ ];
             flag cross-roadmap clauses, which have no clean #Parts link)}.
            If no MVP Checkpoints section, return mvps: [] for that roadmap.
            You MUST return one entry per input path (N total). Do NOT silently
            omit any file. If a roadmap can't be parsed, include an entry with a
            'reason' field rather than omitting it.
            Return JSON-shaped output."
)
```

**Output-shape verification (MANDATORY post-call):** count the returned entries against the input `paths` list (the worker may return a bare array or `{roadmaps:[...]}` — count whichever). If `len(returned) < len(paths)` (empirically observed 2026-05-19 — worker returned 3 of 4 roadmaps on a long-context bundled call), enumerate which roadmap paths are missing and retry them via individual `Read` calls. Do NOT proceed to Phase 3 with partial data — an incomplete leverage graph silently misranks recommendations (a missing roadmap's cross-roadmap dep edges vanish, leaf-shaped Parts get over-promoted, downstream-blocking Parts get under-promoted). The check is mechanical: 1 if-statement; not a judgment call.

**Output-cap guard:** the `trigger`/`source` truncation above is load-bearing, not cosmetic. A full-verbatim extraction across the now-12-roadmap surface overflows the worker's output token cap (observed 2026-05-29: 116 KB on the 8 {{PROJECT_NAME}} roadmaps *alone*, before Jmodot). The 400/200-char caps preserve all readiness signal (concrete-type names that drive `arch-*` Readiness appear early in a trigger; `plan-pending` API/Test-Pin Readiness is confirmed by reading the design doc in Phase 5, never from the trigger field). If the call *still* overflows, it saves output to a file and returns a path — either re-issue split per-tree batches (PP paths, then Jmodot paths) and merge, or parse the saved JSON from disk (e.g. via PowerShell `ConvertFrom-Json`) rather than re-running.

### Phase 3 — Build dep graph

1. Forward graph: `part → [deps]` (per-Part dep list from Parts table).
2. Reverse graph: `part → [downstream-parts]` (invert forward graph).
3. Cross-roadmap edges: parse Source-cell wikilinks (`[[../<roadmap>/...]]`, incl. cross-tree `[[../../<tree>/...]]`) AND "Cross-roadmap references" sections; resolve `deps` entries by Part **name** against the full parsed set (names match across roadmap *and* vault-tree boundaries — e.g. PP `hub-world` → Jmodot `grab→jmodot` "Framework contracts…"; Jmodot procgen → PP "Graph Engine Core"). Add reverse-graph edges across both boundary kinds.
4. MVP membership index (per roadmap, only if `mvps` non-empty): `part → {incomplete MVP ids that list it as a Required Part}`. Drop MVPs whose Status is `✅ Verified`. This is the *direct*-membership map; one-hop reach (Phase 5) unions it with the membership of each direct dependent.

### Phase 4 — Apply state filter + actionable predicate

For each Part:
1. **Scope gate:** if a scope token was given (`scope ≠ both`) and `part.tree ≠ scope` → skip. (Out-of-scope Parts remain in the Phase 3 graph as *dependents*, so in-scope candidates keep accurate Leverage — only their eligibility to be *recommended* is removed.)
2. If `part.state ∉ state_filter` → skip.
3. Apply state-specific actionable predicate (see table above). If predicate fails → skip.
4. Survivors enter the candidate pool.

### Phase 5 — Score and rank

For each candidate:
1. Compute `leverage` (0–3) from reverse-graph fan-out.
2. Compute `state_proximity` (0–3) from state.
3. Compute `readiness` (0–3) per state-specific rubric (Phase 2's `source` / `trigger` fields).
4. Compute `mvp_demand` (0–3): `reach = direct-membership(candidate) ∪ ⋃ direct-membership(d) for d in reverse-graph[candidate]`, over incomplete MVPs (membership index, Phase 3 step 4). `mvp_demand = min(|reach|, 3)`. Also set `closes_mvp = true` if any incomplete MVP has the candidate as its sole remaining unchecked Required Part AND no unresolved cross-roadmap clause; `closes_mvp` forces `mvp_demand = 3`. Candidates on MVP-less roadmaps → `mvp_demand = 0`, `closes_mvp = false`.
5. `total = leverage + state_proximity + readiness + mvp_demand`.
6. Sort by `(total DESC, closes_mvp DESC, state_proximity DESC, dep_recency DESC, roadmap_size ASC)`.
7. Take top 3.

### Phase 6 — Emit report

Format (full version; truncate full actionable table if `--recommend-only`):

```
╔══════════════════════════════════════════════════════╗
║   ROADMAP NEXT — <DATE>                              ║
╠══════════════════════════════════════════════════════╣
║ Scope:         <both (PP + Jmodot) | pp-only | jmodot-only> ║
║ State filter:  <comma-separated list>                ║
║ Scanned:       <N> roadmaps (<a> PP + <b> Jmodot), <M> Parts║
║ Actionable:    <K> (scope ∧ state ∈ filter ∧ deps ✓) ║
╚══════════════════════════════════════════════════════╝

## Top 3 recommended

### 1. <Part Name>  [score: <T>/12]
   Roadmap:    <roadmap-slug>
   State:      <state>  | Leverage: <L> (<rationale fragment>)
                         | Proximity: <P> (<state>)
                         | Readiness: <R> (<source/trigger note>)
                         | MVP Demand: <D> (<reaches MVP-x, MVP-y | closes MVP-z | none>)
   Next:       <handoff command>
   Why:        <one-line rationale citing top score component or fresh-unblock signal; flag "closes MVP-z → Ready for playtest" when closes_mvp>

### 2. <…>
### 3. <…>

## Full actionable list (sorted by score)

| Roadmap | Part | State | Score |
|---|---|---|---|

## Notes
- <state-set summary: e.g. "0 arch-* Parts currently actionable">
- <stale-derived-view warning if "Currently ready to execute" diverges from computed actionable set>
```

---

## Constraints

- **Read-only.** No file writes. No worklog adds. No state transitions (use `/update_roadmap` for those).
- **No agents.** Bounded sequential parse + scoring; no `Task` dispatch.
- **Bundled read.** Phase 2 MUST route through `read_files` (3+ roadmaps = synthesis-shaped).
- **Source-of-truth: Parts tables.** Derived views (`## Currently ready to execute`, `## Blocked / awaiting deps`) are advisory only and may be stale; this command computes from canonical Parts tables.
- **Cloud compatible.** No LSP / Godot MCP dependencies.

---

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Read the `## Currently ready to execute` derived view; faster than parsing the full table" | Derived views drift from Parts tables when `/update_roadmap regen` lags Part transitions. Empirically observed 2026-05-19: derived view listed P7 (which was `complete` per Parts table) and omitted P10 (which was actionable per Parts table). Always parse the canonical table. |
| "Skip the reverse dep graph; score Parts in isolation" | Leverage is the highest-signal component for next-pickup priority. Computing it requires the full graph; skipping it ranks leaves equal to bottlenecks. |
| "Ignore cross-roadmap deps — surveys one roadmap at a time" | Cross-roadmap fan-out is what distinguishes high-leverage shared-infrastructure Parts (P10 Hub Scaffold, Graph Engine Core) from leaf one-offs. Single-roadmap surveys understate leverage. |
| "`pp-only` should skip the Jmodot tree entirely — it's faster and the user only wants PP" | Scope filters the *candidate pool* (Phase 4), not the *dep graph* (Phase 3). Skip Jmodot discovery and cross-tree dep edges vanish — a shared-infra PP Part (Graph Engine Core, depended on by Jmodot procgen) loses Leverage and gets under-ranked. Same failure as the single-roadmap-survey row above. Always parse both trees; gate recommendations only. |
| "Treat all `arch-pending` and `arch-rework` Parts as equally actionable" | `arch-rework` is closer to ship (design exists, needs revisit) than `arch-pending` (design needs initial brainstorm). State-Proximity rubric reflects this. |
| "Recommend top 3 by total score only; skip tie-breakers" | Score ties are common with 0–12 range across small candidate pools. Tie-breakers (closes-MVP → proximity → recency → focus) encode the project's "ship next" intuition. |
| "Treat `user-owned` Parts as recommendable" | `user-owned` is not agent-runnable. Surface them via `/worklog` triage if they're blocking downstream Parts; never in `/roadmap_next` recommendations. |
| "MVP Demand should dominate — a Part serving 3 MVPs is obviously the next pickup" | MVP Demand is a *coequal* 0–3 criterion (≤25% of total), not a primary sort key. A Part demanded by 3 MVPs but blocked by unmet deps isn't actionable (Phase 4 drops it); a high-demand *ready* Part still has to beat Leverage/Proximity/Readiness on total. The cap is the overtuning guard. Cross-roadmap effect is deliberate: inert on MVP-less roadmaps (all score 0), but MVP-bearing roadmaps out-total MVP-less ones by ≤+3 — an intended tilt toward committed-to-ship roadmaps, not a bug. |
| "Propagate MVP demand transitively (A→B→C) so foundational Parts reflect everything they enable" | One-hop only, by design. Transitive closure makes a foundational Part accumulate the whole roadmap's demand and always rank top — re-introducing the dominance the cap exists to prevent. One hop covers the "A blocks B, B serves N MVPs" case; that's the intended scope. |

---

## Cross-references

- [`_brainstorm_shared/common.md §6`](../skills/_brainstorm_shared/common.md) — roadmap.md schema (Parts table, State vocab §6.3, Source-cell anchor rules §6.2, MVP Checkpoints §6.11 — the Required-Parts checkbox + Status schema MVP Demand reads)
- [`/update_roadmap`](update_roadmap.md) — Part state transitions + derived-view regeneration (the source-of-truth maintainer this command reads)
- [`/plan_part`](plan_part.md) — handoff for `plan-pending` recommendations
- `/architecture_brainstorm` skill — handoff for `arch-pending` / `arch-rework` recommendations
- `/idea_brainstorm` skill — handoff for `idea-pending` / `idea-rework` recommendations
- CLAUDE.md §9 *Tool Routing — Pre-Call Litmus* — Phase 2 bundled `read_files` rule
- `feedback_audit_then_reconcile.md` — derived-view-drift signal motivating "Parts table is canonical" rule
