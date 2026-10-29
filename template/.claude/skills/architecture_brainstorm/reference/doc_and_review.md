# Doc and review: Steps 6–8

Read at [`SKILL.md`](../SKILL.md) Step 6, once the owner has approved the Parts list.

## Step 6: Save the design doc

**[Shared]** Path tiebreaker + folder-per-topic + frontmatter conventions — see [`_brainstorm_shared/common.md` §5](../../_brainstorm_shared/common.md). Spawn-placement decisions for any child docs — see [`§5.1`](../../_brainstorm_shared/common.md) (applied at Step 1).

**Architecture-doc-specific conventions:**
- Folder + filename per `common.md §5`. **The save path is fixed by the Step 1 spawn-placement decision (§5.1) — resolve it there, then map to a path here. Each placement is a distinct case; do NOT default to the parent folder:**
  - **Direct-arch invocation** (no upstream cluster routing, fresh topic): `YYYY-MM-DD-<kebab-case-topic>/arch.md`
  - **Cluster-scoped** (downstream of an `ideas.md` Per-Cluster Routing): `<parent-topic-folder>/arch-<cluster-slug>.md` (same parent folder)
  - **Same-folder sibling / follow-up** (§5.1 *sub-component* — criterion 1 or 3 fails): `<parent-topic-folder>/arch-<slug>.md` or `<parent-topic-folder>/arch-<slug>-followup-N.md`
  - **Deeper-scope submap** (§5.1 *child subfolder* — own brainstorm, parent-confined audience): **`<parent-topic-folder>/<sub-slug>/arch.md` — inside a NEW child subfolder, NOT the parent folder.** ⚠ This is the case that gets mis-saved to the parent top level. The design doc AND the child `roadmap.md` (the latter created by `/update_roadmap` in Step 8's multi-roadmap case) BOTH live in `<parent-topic-folder>/<sub-slug>/`. Create the subfolder if it doesn't exist.
  - **Cross-cutting sibling folder** (§5.1 all 3 criteria met): `YYYY-MM-DD-<sibling-slug>/arch.md` — a NEW top-level topic folder at the same depth as the parent.
- Frontmatter: `phase: architecture_brainstorm`
- Status taxonomy:
  - **`active-brainstorming`** — in progress
  - **`brainstorming-complete`** — all design sections finalized; Parts authored and ready for `/update_roadmap`. Whether the resulting roadmap Parts are all `plan-pending` or mix `plan-pending` with `arch-pending` / `idea-pending` is recorded in `roadmap.md`, NOT in this doc's status — separation of concerns.

**Roadmap.md is NOT saved here — load-bearing guardrail.** Step 6 saves the DESIGN DOC only — `arch-<slug>.md` / `arch.md` / `arch-<slug>-followup-N.md` per the filename table above. The topic-folder `roadmap.md` (top-level OR child sub-roadmap from a *deeper-scope* spawn per [`common.md §5.1`](../../_brainstorm_shared/common.md)) is owned by `/update_roadmap` in Step 8 — DO NOT create roadmap.md via `write_doc` / `write_code` / direct `Write` here, in Step 7, or anywhere except as Step 8's invocation output. Bypassing the executor silently drops Trigger validators ([`common.md §6.10`](../../_brainstorm_shared/common.md)), derived-view recomputation ([§6.5](../../_brainstorm_shared/common.md)), Mermaid deterministic regen ([§6.4](../../_brainstorm_shared/common.md)), and the revision-log discipline ([§6.7](../../_brainstorm_shared/common.md)). Single-executor pattern applies recursively: every roadmap.md at every depth routes through `/update_roadmap`. The mistake-mode this guardrail prevents: a worker-prose `write_doc` call producing a sub-roadmap inline during this Step instead of waiting for Step 8's two-invocation sequence (parent transition + child creation).

**Worklog discipline (two-category integrity):** the brainstorm doc IS the design deliverable. Two homes for things surfaced in this brainstorm:

- **Architectural commitment** → a Part on `roadmap.md` (any State per §6.3 — `plan-pending`, `arch-pending`, `idea-pending`, `workshop-pending`). Owned by this brainstorm-topic-folder's roadmap.
- **Tactical deferral / out-of-scope punt** → worklog (e.g., "lift mechanic — revisit post-MVP" with no design implications).

Don't include a "worklog items to add" section listing architecture work surfaced inside the brainstorm — that's category confusion. Architecture commitments live on the roadmap; worklog is for tactical noise.

### Design doc structure

- An `## At a glance` section directly under the H1, for the owner who reads only that: the problem, the chosen approach, and the Parts it yields with each one's State. It states no fact the body below does not, and it is rewritten whenever a later section changes one of those three.
- Sections for each architectural commitment, including the test plan and (for `plan-pending` Parts) the Integration touch points enumeration from [`part_authoring.md`](part_authoring.md).
- An *Open Questions* section at the end for genuinely-unanswered items raised during Socratic phase (Step 2) — these are NOT roadmap Parts; they're meta-questions about the design itself. **Open Questions is for design *meta*-questions, NOT a parking lot for deferred scope decisions that belong to a named Part.** If a note bears on a specific Part's scope (what it adds, migrates, or *deletes/retires*), it belongs in that Part's source section as committed scope OR as an explicit worklog deferral — not parked here. Litmus: *"Does resolving this change what a named Part builds or removes?"* Yes → it's Part scope, commit it; No (it's a design-shape question with no Part owner) → Open Questions. Parking Part-scoped decisions here defeats the No-orphans check below and the decision silently drops at the `/plan_part` handoff (the design surface tool reads Open Questions but a parked-not-resolved decision reads as ambient context, not an action).
- The roadmap is NOT embedded in this doc — it lives in `roadmap.md` in the same folder, owned by `/update_roadmap`.

## Step 7: Spec self-review

Before declaring the design complete, scan the doc for:

- [ ] **Placeholder scan** — no `TBD`, `TODO`, `<fill in>`, `???` markers. Either resolve or escalate to the user as an open question.
- [ ] **Contradiction check** — sections shouldn't claim opposing things (e.g., *"stamina is a resource"* in §2, *"stamina is a cooldown"* in §5).
- [ ] **Ambiguity check** — every concrete claim has a concrete file path or class name. *"It uses the existing event system"* is ambiguous; *"It emits `CapacityChanged` via `EventBus.Publish` (see `src/Events/EventBus.cs:42`)"* is concrete.
- [ ] **Scope check** — the design doc matches the topic. If discussion drifted, either expand scope (and the topic) or trim the off-topic sections.
- [ ] **Parts authored** — Step 5 produced a list of Parts, each with State (per common.md §6.3 vocabulary), Deps, Source link back into this doc, and (for `plan-pending` Parts) Integration touch points enumeration. At least one Part is required; zero-Part outcome = doc terminates without bridging to either implementation or further design work.
- [ ] **No-orphans check** — every architectural claim in the design body maps to exactly one of: {a Part on the roadmap, an explicit worklog deferral, abandoned with stated reason}. No design claim with no path forward. If a section says *"the system will use X"* but X is neither committed in a Part nor explicitly punted to worklog nor abandoned, that's an orphan — promote it to a Part or punt it explicitly. Orphans are the failure mode that produces *"we wrote a design doc and then nothing happened"* months later. **Open Questions and Memory-Anchor footnotes do NOT count as a "path forward" for a Part-scoped scope decision** — a deletion, migration, or retirement that bears on a named Part but lives only in Open Questions or a cited memory is an orphan.
- [ ] **Seam-ownership check** — for every item the design explicitly scopes OUT to a neighboring or future brainstorm, name the interface **contract** at that boundary and assign an **owner**: a Part on this roadmap, an existing Part on a cross-referenced roadmap (cite it), or an explicit worklog deferral. A claim that defers work to *"their own Parts"* / *"a future session"* is an orphan **unless that Part demonstrably exists**. Clean scoping *creates* seams; seams need owners too.
- [ ] **No-speculative-downstream check** — every Part depends only on (a) Parts in this roadmap at `plan-pending` or stronger, OR (b) already-shipped infra. No Part may depend on `*-pending` output (that belongs to the future session and authors its own Parts). Litmus per [`part_authoring.md`](part_authoring.md) *Downstream-of-fork guard*: *"If this Part's upstream session decided differently, would this Part change shape?"* Yes = speculative; remove.
- [ ] **Readiness-gate audit** — for each Part with State=`plan-pending`, verify the 5 criteria of the *Part-readiness gate* ([`part_authoring.md`](part_authoring.md)) actually hold. If any fails, demote to `arch-pending` with the open piece named in Trigger.
- [ ] **Plan-pending cohesion audit** — for any run of 3+ sequential `plan-pending` Parts, the *Plan-pending cohesion litmus* ([`part_authoring.md`](part_authoring.md)) ran: thin + tightly-coupled adjacent Parts were merged, or each kept-split Part states its keep-separate reason. Catches type-surface-layer over-split (clean dependency ordering ≠ right Part sizing).
- [ ] **Integration touch-points audit** — for each `plan-pending` Part, verify the touch-points list ≤2 distinct subsystem boundaries (per `project_subsystems`). ≥3 boundaries = mandatory split or additional Socratic pass before invoking `/update_roadmap`.

**Mode A adversarial pass (drafted design).** For architecturally-loaded designs, run [`/architecture_brainstorm_redteam`](../../../commands/architecture_brainstorm_redteam.md) in its standalone **Mode A** before the user-review gate (its `--auto` mode for topics you trust the agents to harden). It red-teams the *drafted* design for design holes and **classifies findings design-hole (fold back into the doc) vs taste-fork (surface to the user as Socratic seeds) — it never decides taste.** This is the full-doc counterpart to the per-step **Mode B** hooks ([`red_team.md`](red_team.md)) that fire during Steps 2/4/5 when the skill is invoked with `--red_team`. Both AUGMENT the Socratic flow; neither replaces Steps 2/5.

**[Shared] Rationale spot-check** — see [`_brainstorm_shared/common.md` §2](../../_brainstorm_shared/common.md).

**[Shared] User review gate** — see [`_brainstorm_shared/common.md` §4](../../_brainstorm_shared/common.md).

## Step 8: Invoke `/update_roadmap`

After the design doc is saved and the user has approved both doc and Parts list, invoke `/update_roadmap` with:
- The saved design doc path (so the command knows which topic folder owns the roadmap)
- The Parts list from Step 5 (mapped per §6.3 vocabulary)
- Any spawn-placement decision from Step 1 (same-folder / child-subfolder / sibling-folder) for the `Spawned sub-brainstorms` section.

**Multi-roadmap case (`child-subfolder` spawn that decomposes an existing parent Part)** — invoke `/update_roadmap` **TWICE in sequence**. Single-roadmap batch-diff semantics stay intact; user approves each batch separately.

| Invocation | Target roadmap | What it does |
|---|---|---|
| **1 — Parent** | `<parent-folder>/roadmap.md` | Transition parent Part to `submap-pending` with Trigger=`"Decomposed into <child-folder>/roadmap.md (N child Parts)."` per common.md §6.10. Add `Spawned sub-brainstorms` entry pointing to the child roadmap. Append revision log entry. Parent Part's Deps + Source + dependent edges PRESERVED (only State + Trigger change). |
| **2 — Child** | `<parent-folder>/<child-slug>/roadmap.md` (NEW file) | Create child sub-roadmap from common.md §6.1 schema with `parent-roadmap: ../<parent-folder>/roadmap.md`, `scope-level` inherited from parent, fresh `created: <today>` / `last_revised: <today>`. Populate Parts table from this brainstorm's Step 5 output. Cross-roadmap deps preserved with `(parent) <Part>` prefix or `[[../<parent>/roadmap#Part]]` wikilink. If 2+ cross-roadmap deps exist, also author `## Cross-roadmap dependencies` (§6.12). |

**Do NOT inline-author the child sub-roadmap via `write_doc` / `Write`** — per CLAUDE.md NEVER rule + Step 6 guardrail above, the child roadmap.md routes through `/update_roadmap` exactly like the parent. Invocation 2 IS the canonical authoring path.

`/update_roadmap` is the single executor for roadmap.md edits. It runs in batch-propose mode — assembles the full diff (Parts table + Mermaid + derived views + revision log entry), shows it, applies on single approval. Do NOT hand-edit `roadmap.md` from within this skill; the command owns that surface.

If `/update_roadmap` reports no parent `roadmap.md` exists, it'll propose creating one — confirm and proceed. The initial creation pulls frontmatter from the design doc's frontmatter (`topic`, `scope-level`, `scope` → `project-game` / `jmodot-framework`).

## Plan-production handoff requirement

When a `plan-pending` Part enters plan production, the FIRST action MUST be to enumerate the full set of files to be modified. If the file list cannot be bounded ("...and possibly some others"), drafting HARD-STOPS with feedback: *"Part scope is unbounded — split or kick back to arch brainstorm with the discovered cross-cutting."*

This is the failsafe for criterion 1 (files-nameable) of the *Part-readiness gate* ([`part_authoring.md`](part_authoring.md)) drifting between brainstorm time and plan time. Arch brainstorm names files; plan production verifies. Drift = scope-creep alarm. Don't proceed past the unbounded signal.

**Recommended entry recipe:** invoke [`/plan_part <part-name>`](../../../commands/plan_part.md) at the top of the planning session. The command loads the design surface verbatim (Source-cell section + API + Test Pin + Integration touch points), cross-references the design's named files/types/families against current codebase state, and classifies any divergence as **macro** (HARD-STOP, kicks back to `arch-rework`) or **micro** (surfaced in the briefing to resolve during drafting). The briefing it emits IS the in-context handoff packet — `/plan_part` operationalizes this requirement so the file-list-bounded gate and the "design is approved, don't redesign" contract are enforced rather than aspirational.

**Autonomous alternative — `/part_drive --plan-only`.** To drive a `plan-pending` Part to an approval-ready plan *unattended*, invoke [`/part_drive <part-name> --plan-only`](../../../commands/part_drive.md) instead of planning by hand — it wraps `/plan_part` + [`/explore`](../../../commands/explore.md) + drafting + `/plan_check` into one brief→explore→draft→audit→converge loop, halting only at the user's approval of the plan file (or a halt valve). Use `/plan_part` directly for hands-on planning; `/part_drive --plan-only` when you want the loop run for you. (This is the forward seam of the pipeline: `/architecture_brainstorm` → `/part_drive --plan-only` → approval → `/part_execute`.)

**Hard-stop remediation.** When plan production hard-stops a `plan-pending` Part, route per the table:

| Situation | Move |
|---|---|
| Split lines known | `/update_roadmap split <part> into <a>, <b>` — children inherit gate-resolved States |
| Split lines unknown (or also under-specified) | `arch-rework`, Trigger names the split charter ("identify split boundaries") |
| Under-specified only (size fine) | `arch-rework`, Trigger names the missing piece |
| Inherently user-domain | `user-owned`, Trigger names the user deliverable |
