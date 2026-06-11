---
description: Audit all roadmaps for cross-roadmap drift — submap-count sync, dep resolution, forbidden #part anchors, MVP Pos-refs, state-snapshot staleness, submap doc placement. Read-only; fixes route through /update_roadmap + /mvp_plan.
---

# /roadmap_audit

Surveys every `roadmap.md` under `BrainstormingDesigns/`, builds the cross-roadmap dependency + submap graph, and runs five consistency checks that the single-roadmap executors can't see. `/update_roadmap` maintains *one* roadmap; this command checks the *seams between* roadmaps — the denormalized references (submap counts, cross-folder deps, MVP Pos-numbers, state snapshots) that drift silently when a child roadmap mutates and its parent isn't resynced.

**Companion to `/update_roadmap`** (per-roadmap maintainer — the fix executor) and `/roadmap_next` (next-pickup scorer). This command answers "**are the roadmaps consistent with each other?**"

**Read-only.** Parses roadmaps, computes findings, prints a tiered report. No file writes, no agent dispatch. Every finding names the fix executor — `/update_roadmap` (Parts / Spawned / deps / count), `/mvp_plan refine` (MVP Pos-refs), or a manual design-doc move + `brainstorm-source` update (placement) — the command never applies fixes itself.

---

## Why this exists

The schema's lazy-resolution policy (`common.md §6.8`) deliberately tolerates stale cross-folder *state* snapshots, and `/update_roadmap` regenerates Mermaid + derived views deterministically for the roadmap it edits. Neither mechanism keeps a **parent's denormalized references to a child** in sync when the child mutates. Empirically (2026-05-21 audit), four drift classes accumulated:

- A sub-roadmap split 8 → 11 Parts; the parent's submap Trigger, Spawned table, and MVP "all N Parts" claims still said 8.
- A sub-roadmap renumbered Pos 3–8 → 4–9; the parent's MVP Checkpoints cited pre-split Pos numbers.
- A cross-folder Dep named a parent Part that was never created (phantom dep) — silently unresolvable.
- A cross-roadmap reference asserted a Part was "named as DEP" when the target's Deps cell was `—`, used a stale `(arch-pending)` annotation, and pointed at a forbidden `roadmap#Part` heading-anchor.

All four are mechanically detectable. This command is the periodic catch.

## When to invoke

- After a sub-roadmap split / renumber / rename / state-batch (the parent likely drifted).
- Before a planning session that spans multiple roadmaps, or before `/roadmap_next` (so its leverage graph isn't built on stale cross-folder edges).
- Periodically as hygiene — e.g., a `/session_end`-conditional sweep when the session touched 2+ roadmaps.

## When to skip

- Single-roadmap session with no cross-folder references touched.
- Immediately after a clean `/roadmap_audit` with no intervening roadmap edits.

---

## Arguments

| Form | Behavior |
|---|---|
| `/roadmap_audit` | Full audit — all 5 checks across every roadmap. |
| `/roadmap_audit <check-id[,check-id...]>` | Run only named checks. IDs: `submap`, `deps`, `anchors`, `mvp`, `state`, `docs`. |
| `/roadmap_audit <roadmap-slug>` | Scope to one roadmap as the *subject* (still reads others as resolution targets). |
| `/roadmap_audit --strict` | Promote advisory state-staleness (Check 5) findings to the same tier as structural findings. |

Unknown check-id or slug → emit usage table and exit.

---

## The six checks

| ID | Check | Detects | Tier |
|---|---|---|---|
| `submap` | **Submap-count sync** | A `submap-pending` Part's Trigger "(N child Parts)" + Spawned-section state breakdown disagree with the child roadmap's actual Part count / state distribution. | structural |
| `deps` | **Cross-folder dep resolution** | A cross-folder Deps entry (`(parent) X`, `(<folder>) X`, or `[[../<folder>/roadmap\|...]] § "X"`) names a Part `X` that doesn't exist on the target roadmap (phantom dep, or a rename the dependent never followed). | structural |
| `anchors` | **Forbidden `#part` anchors** | A cross-roadmap *Part* reference uses a heading anchor — `roadmap#<part>` or `[[../<folder>/roadmap#<part>]]`. Parts are table rows, not headings; the anchor falls through to file-top (`common.md §6.8`). | structural |
| `mvp` | **MVP Pos-reference integrity** | An MVP Checkpoint cites `(<folder>) Pos N (<Part>)` / `<folder> Pos N` where the named Part is no longer at Pos N on the target (split/renumber drift), or an "all N Parts" / "N child Parts" claim disagrees with the child's live count. | structural |
| `state` | **State-snapshot staleness** | A cross-roadmap reference annotates a target Part's state in parens — `(arch-pending)`, `(complete)`, etc. — that disagrees with the target's live State. Also: a "named as DEP" assertion where the target's Deps cell doesn't contain the referrer. | advisory* |
| `docs` | **Submap doc placement** | A sub-roadmap's `brainstorm-source` either (a) points at a file that doesn't exist (dangling pointer), or (b) is a freshly-authored submap doc mis-placed at the parent top level — signature: `../arch.md` / `../ideas.md` (bare-name doc reached via `../`), which should be co-located `./arch.md` / `./ideas.md` per common.md §5.1. A `../arch-<slug>.md` (hyphenated cluster doc) is the legitimate exception, not a finding. | structural |

*Advisory because `common.md §6.8` tolerates stale state snapshots by design (eager mirroring would create consistency bugs). Surfaced anyway because a *false structural claim* ("named as DEP") riding on a stale annotation is worth a human glance. `--strict` promotes these to structural tier.

**Out of scope** (owned elsewhere — don't duplicate):
- Intra-roadmap Source-cell *design-doc* anchor coherence (`[[arch#Section ...]]`) — `/update_roadmap` Step 3 validates these at edit time.
- Trigger content-shape per `common.md §6.10` — `/update_roadmap` Step 3.
- Derived-view freshness within a single roadmap — `/update_roadmap` Step 5 regen + `/roadmap_next`'s "parse the canonical table" discipline.
- Part *scoring* / next-pickup — `/roadmap_next`.

---

## Procedure

### Phase 1 — Discover roadmaps + parse args

1. `Glob` for `BrainstormingDesigns/**/roadmap.md` under the Obsidian vault (`{{VAULT_ROOT}}\DevProjects\{{PROJECT_NAME}}\Claude\BrainstormingDesigns\`).
2. Parse args into `checks` (default: all 5) + optional `subject_slug` + `--strict`. Unknown id/slug → usage + exit.
3. Build the parent↔child map from frontmatter `parent-roadmap` fields (identifies which roadmaps are sub-roadmaps, and each `submap-pending` Part's child target).

### Phase 2 — Parse Parts tables + reference surfaces (bundled)

Per CLAUDE.md §9 — 3+ files for synthesis → route through `mcp__ai-worker__read_files` (offline fallback: a Haiku subagent per `common.md §3`):

```
read_files(
  paths=[<each roadmap.md absolute path>],
  question="For EACH roadmap return one structured record per input path (do not omit any path).
            Extract: (a) every Parts table row {pos, part_name, state, deps_raw, trigger, source};
            (b) the Spawned-sub-brainstorms section text;
            (c) every MVP Checkpoint's Required-Parts + Acceptance lines that name a cross-folder
                Part or Pos (e.g. 'encounter-extraction Pos 6', 'all 8 Parts');
            (d) any 'Cross-roadmap references' / 'Cross-roadmap dependencies' section bullets,
                including parenthetical state annotations like '(arch-pending)';
            (e) the frontmatter 'parent-roadmap' and 'brainstorm-source' fields (verbatim);
            (f) any PROSE sentence — especially in derived-view sections ('Currently ready to
                execute', 'Blocked', 'Status', section intros) — that denormalizes a child
                sub-roadmap's Part COUNT or per-state breakdown (e.g. 'its 10 sub-Parts: 2 complete,
                6 plan-pending...', 'the 11-Part encounter roadmap'). Return each verbatim with its
                heading. This surface is INVISIBLE to (a)-(d) and is the recurring blind spot — a
                submap fix touching only the Trigger + Spawned row leaves these prose copies stale.
            Return JSON-shaped output keyed by roadmap path."
)
```

**Output-shape verification (MANDATORY):** count returned records vs input `paths`. If fewer, re-`Read` the missing roadmaps individually before Phase 3 — a missing roadmap silently drops its cross-folder edges and produces false "phantom dep" / "resolves clean" findings. Mechanical check, not a judgment call (same failure mode documented in `/roadmap_next` Phase 2).

### Phase 3 — Build resolution structures

1. **Part index** — `{(roadmap, part_name) → {state, pos}}` across all roadmaps. The lookup table every check resolves against.
2. **Submap map** — each `submap-pending` Part → its child roadmap + the child's live `{count, state-breakdown}`.
3. **Cross-folder reference list** — every Deps entry, Source cell, MVP reference, and cross-roadmap-section bullet that names another roadmap, parsed to `{referrer-roadmap, referrer-part, target-roadmap, target-part-or-pos, asserted-state?}`.
4. **Submap doc-placement list** — each sub-roadmap (`parent-roadmap` present) → `{brainstorm-source pointer, resolved absolute path}` for the `docs` check.

### Phase 4 — Run checks

For each enabled check, walk the relevant structure and emit findings. Each finding = `{tier, check-id, roadmap, location-hint, observed, expected, fix-executor}`.

- **submap** — for each submap map entry, compare Trigger "(N child Parts)" + Spawned breakdown + any derived-view prose mention (field (f)) vs child live count/breakdown. All denormalized copies must agree; flag each stale location separately so the fix covers them all (the prose copy is the recurring miss).
- **deps** — for each cross-folder Deps reference, look up `(target-roadmap, target-part)` in the Part index; miss → phantom-dep finding.
- **anchors** — regex the raw reference strings for `roadmap#` followed by a Part-name-shaped token (not a design-doc section); hit → forbidden-anchor finding. (Design-doc anchors `[[<doc>#Section ...]]` where `<doc> ≠ roadmap` are exempt.)
- **mvp** — for each MVP cross-folder Pos/count reference, verify the named Part sits at that Pos on the target / the count matches the child live count.
- **state** — for each annotated cross-roadmap reference, compare asserted state vs target live state; also verify any "named as DEP" assertion against the target's actual Deps cell.
- **docs** — for each sub-roadmap (has `parent-roadmap` frontmatter), resolve `brainstorm-source` relative to the sub-roadmap's own folder. If the resolved file doesn't exist → dangling-pointer finding. If `brainstorm-source` is `../arch.md` or `../ideas.md` (a bare-name design doc reached one level up) → mis-placement finding: a freshly-authored submap doc that should be co-located as `./arch.md` / `./ideas.md` in the sub-roadmap folder (per common.md §5.1). `../arch-<slug>.md` (hyphenated cluster doc that legitimately stays in the parent) is exempt — not a finding. Use `Glob`/`Read` to confirm existence; this is the one check that touches the filesystem beyond the bundled Parts-table read.

### Phase 5 — Emit tiered report

```
╔══════════════════════════════════════════════════════╗
║   ROADMAP AUDIT — <DATE>                             ║
╠══════════════════════════════════════════════════════╣
║ Scanned:    <N> roadmaps (<S> sub-roadmaps)          ║
║ Checks:     <enabled list>                           ║
║ Findings:   <X> structural, <Y> advisory             ║
╚══════════════════════════════════════════════════════╝

## Structural findings (fix before they mislead)

### [submap] <referrer-roadmap> — <Part>
   Observed:  Trigger says "8 child Parts"; child has 11 (3 complete, 8 arch-pending)
   Expected:  "11 child Parts (3 complete, 8 arch-pending)" in Trigger + Spawned table + any derived-view prose note
   Fix:       /update_roadmap (resync submap denormalized references)

### [mvp] <roadmap> — MVP-4
   Observed:  cites "Pos 6 (Multi-encounter rooms + procgen CSP)"; that Part is now Pos 7
   Expected:  "Pos 7 ..." (encounter-extraction renumber 2026-05-18)
   Fix:       /mvp_plan refine MVP-4

### [deps] <roadmap> — <Part>
   Observed:  Deps "(parent) Procgen-needs-list sub-Part" — no such Part on <target>
   Expected:  resolve to a real parent Part, or remove the dep (DESIGN DECISION — see note)
   Fix:       /update_roadmap (rewrite dep) — but resolution may need a design call

### [docs] <sub-roadmap> — brainstorm-source
   Observed:  brainstorm-source "../arch.md" — fresh submap doc sits in the PARENT folder
   Expected:  co-located "./arch.md" inside the sub-roadmap folder (common.md §5.1)
   Fix:       move the design doc into the sub-roadmap folder + set brainstorm-source to ./

## Advisory findings (state staleness — §6.8 tolerated)

### [state] <roadmap> — <reference>
   Observed:  annotated "(arch-pending)"; target live State is "submap-pending"
   Fix:       /update_roadmap (refresh annotation) — or accept per lazy-resolution policy

## Clean checks
- [anchors] no forbidden roadmap#part references found
```

If zero findings: print *"All <N> roadmaps consistent across <enabled checks>. No cross-roadmap drift."* and exit.

---

## Constraints

- **Read-only.** No file writes, no worklog adds, no state transitions. Findings name the executor; the user runs it.
- **No agents.** Bounded sequential parse + check; no `Task` dispatch (the bundled `read_files` is the only synthesis call).
- **Source-of-truth: Parts tables.** Resolve against canonical Parts tables, never derived views (which may themselves be stale).
- **Cloud compatible.** No LSP / Godot MCP dependency. `read_files` offline → Haiku-subagent fallback (`common.md §3`).
- **Don't auto-fix design decisions.** Phantom-dep findings (Check `deps`) often can't be resolved mechanically — the fix may require creating a parent Part or folding scope into another (a design call). Surface; never guess the resolution.

---

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Resolve against `## Currently ready to execute` — it's pre-computed" | Derived views drift too. Resolve against canonical Parts tables (same rule as `/roadmap_next`). |
| "A phantom dep is just stale — auto-rewrite it to the nearest real Part" | Phantom-dep resolution is usually a design decision (create the missing parent Part vs fold scope vs drop the dep). Surface it; don't guess. |
| "State annotations are always wrong eventually — skip Check 5" | The annotation alone is §6.8-tolerated, but a *false structural claim* riding on it ("named as DEP" when Deps is `—`) misleads. Keep the check; it's why `state` also verifies the DEP assertion, not just the parenthetical. |
| "Apply the fixes from this command directly" | This command is the *detector*. Fixes route through `/update_roadmap` (Parts/Spawned/derived) and `/mvp_plan refine` (MVP section) so the revision-log + regen discipline runs. |
| "Audit only the roadmap I just edited" | Drift is in the *seam* — the edited child's parent (or a sibling that deps into it) is where the stale reference lives. Default to the full sweep; `<roadmap-slug>` scopes the *subject* but still reads others as targets. |

---

## Cross-references

- [`_brainstorm_shared/common.md`](../skills/_brainstorm_shared/common.md) — roadmap.md schema. §5.1 (submap doc placement — `docs` check), §6.3 (submap-pending), §6.8 (cross-folder dep resolution + forbidden-anchor rule), §6.10 (Trigger content + submap denormalization discipline), §6.11 (MVP refs), §6.12 (sub-roadmap cross-roadmap-deps section) are the surfaces this command checks.
- [`/update_roadmap`](update_roadmap.md) — fix executor for `submap` / `deps` / `anchors` / `state` findings (Parts table + Spawned section + derived views + revision log).
- [`/mvp_plan`](mvp_plan.md) — fix executor for `mvp` findings (`/mvp_plan refine MVP-N`).
- [`/roadmap_next`](roadmap_next.md) — sibling read-only scanner (discovery + bundled-parse skeleton shared with this command); run `/roadmap_audit` first so its leverage graph isn't built on stale cross-folder edges.
- CLAUDE.md §9 *Tool Routing — Pre-Call Litmus* — Phase 2 bundled `read_files` rule + offline fallback.
