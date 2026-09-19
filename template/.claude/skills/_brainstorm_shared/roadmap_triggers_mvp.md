# Roadmap.md — Triggers, MVP Checkpoints, cross-roadmap deps, atlas (§6.10–§6.13)

> Detail body of the brainstorming shared surface, read at the step named in [`common.md`](common.md). Not a skill — no frontmatter, never auto-loaded.
> Section numbers are stable across the split; a § cited here that is not in this file is mapped to its file by the `common.md` index.

---

### §6.10 Trigger content semantics (by State)

The `Trigger` field is required for any Part whose State leaves un-resolved work, an external ripeness condition, or a user-deliverable obligation. Required content varies by State — this table is the single source of truth for every Trigger shape:

| State | Required Trigger content |
|---|---|
| `arch-pending` / `idea-pending` | Inventory + source pointer + out-of-scope. **Canonical format:** `"Produces <output-shape> for <inventory> per <source-section>. Out of scope: <items>."` |
| `workshop-pending` | The user-decision question + a source pointer to the doc section that surfaced it. Inventory / out-of-scope optional but encouraged. |
| `prototype-pending` | The prototype question + the SHAPE of its answer (mirrors `prototypes/<slug>/QUESTION.md`) + a pointer to the `decisions.md` `## Blocked` entry it unblocks. |
| `idea-rework` / `arch-rework` | What changed since the prior phase fired — spec-revealed scope creep, plan-production hard-stop reason, ripening signal that fired, conflicting evidence, etc. The point is to make the "came back" history legible to the next session. |
| `submap-pending` | Relative path to the child sub-roadmap (`<child-folder>/roadmap.md`). That file's existence + Parts table IS the decomposition; no other Trigger field needed. **Canonical format:** `"Decomposed into <child-folder>/roadmap.md (N child Parts)."` |
| `user-owned` | Names the user deliverable concretely (spatial design batch, manual content authoring, taste-driven tuning pass) so the user knows what "complete" looks like. |

**Rationale (`*-pending` case):** Triggers function as the future session's scope briefing — that session's existing-doc check (§1) loads the right context on first call. Inventory + source + out-of-scope is the minimum that prevents silent re-derivation of the design space.

**Rationale (`*-rework` case):** `*-rework` States map to dashed-stroke Mermaid class (signaling "came back" history); the Trigger preserves the WHY across sessions. Without it, the next-session reader sees the dashed stroke but can't reconstruct the gap.

**Rationale + denormalization discipline (`submap-pending` case):** The Trigger is a path because the decomposition IS the artifact — future readers follow the path to find live work. **Denormalize as little child state into the parent as possible.** The bare `(N child Parts)` count is the ONLY child figure permitted in the parent's submap Trigger; the per-state distribution (e.g. `3 complete, 8 arch-pending`) MUST NOT be copied into the Trigger, the §6.6 Spawned-sub-brainstorms entry, or §6.11 MVP claims — it drifts on *every* child Part transition, and the child roadmap is the sole source of truth for child state. **The ban costs the reader nothing now that the atlas exists** — `Roadmap-Atlas.md` (§6.13) renders each parent's subtree roll-up and per-state breakdown live, recomputed on every regen, so the only thing a copied breakdown adds is a surface that can be wrong. Even the bare count drifts when the child splits/adds/removes Parts (rarer, structural); `/update_roadmap` Step 3 emits a parent-staleness warn on those, and `/roadmap_audit submap` is the periodic catch. (The earlier "stale-mirror is structurally prevented" claim was aspirational — denormalizing the breakdown anyway is what rotted it four times across the dungeon + replayability roadmaps.)

**Consumers:**
- `architecture_brainstorm` SKILL Step 5 — authors `*-pending` and `*-rework` Triggers; runs user-owned question for `user-owned` Triggers. Step 8's `/update_roadmap` invocation handles `submap-pending` transitions when spawn-placement is child-subfolder.
- `idea_brainstorm` SKILL Step 5 — authors `*-pending` Triggers; also `workshop-pending` Triggers for `→ workshop` cluster outcomes.
- `/update_roadmap` Step 3 — emits soft warns for malformed Triggers across every shape in the table above, as a safety net for standalone transitions and hand-edits that bypass each SKILL's authoring gate.

### §6.11 MVP Checkpoints (optional, top-level roadmaps ONLY)

For top-level roadmaps whose Parts span enough surface that *operational readiness* (per §6.5 derived views) is hard to distinguish from *playable-milestone progress*, an optional `## MVP Checkpoints` section frames the roadmap as a sequence of playable surfaces — each MVP defined by which Parts MUST complete + what playtest validates the milestone. The authored fields are owned by `/mvp_plan`; `/update_roadmap` Step 5 additionally recomputes two derived elements in-place each run — the per-Required-Part check-marks and the non-terminal Status line — both pure idempotent functions of the Parts table (recomputed both directions, so they cannot drift). It never authors or edits the narrative fields. The terminal `✅ Verified` status is user-set via `/mvp_plan verify` and preserved across runs (downgraded only when a Required Part regresses out of `complete`). `roadmap_atlas.py` additionally consumes this section read-only: its MVP-Demand scoring criterion counts the incomplete MVPs (Status ≠ `✅ Verified`) that list a candidate Part — or a direct dependent of it — among their Required Parts, so the checkbox membership feeds next-pickup ranking, not just display.

**Roadmap-level applicability — top-level only.** MVPs are authored on TOP-LEVEL roadmaps only — those whose frontmatter has no `parent-roadmap` field, OR whose `scope-level` is `whole-game`. **Sub-roadmaps (per §5.1 *deeper scope* outcome, having `parent-roadmap: ../...` frontmatter) do NOT carry their own MVP Checkpoints section.** Parent MVPs reference sub-roadmap Parts directly (`encounter-extraction Pos 1 (Part Name) complete`, `encounter-extraction sub-roadmap fully complete`, etc.) — never sub-roadmap MVPs.

*Rationale:* MVPs frame *player-facing playable surfaces*. The playable surface emerges at the project level where the player actually experiences the game, not at the subsystem / sub-roadmap level (which is impl-validation territory — that work belongs in test plans, acceptance criteria, and integration touch points within each Part, NOT in MVP-shaped milestones). Two MVP sections in the same brainstorm-topic folder is a category confusion AND a drift surface: readers would have to reconcile "subsystem playable surface" vs "project playable surface" framings that purport to be the same vocabulary at different scopes.

*Enforcement:* `/mvp_plan` aborts when invoked on a sub-roadmap (detects `parent-roadmap` frontmatter, redirects user to parent). `architecture_brainstorm` Step 5 *MVP recommendation* sub-step only fires for top-level roadmaps.

**When to author** — roadmaps with **5+ Parts** where Parts alone don't communicate *"what does the player experience after Part N?"*. Skip for short roadmaps (1-4 Parts) where the Parts themselves are the milestones. Common case for the trigger: arch-shaped roadmaps where Parts deliver infrastructure that needs *combination* to be playable.

**Skip litmus** — if every Part already names its playtest moment in its own design-doc section, MVPs are redundant. If Parts cluster into N intuitive playable surfaces (1 surface ≈ 2-4 Parts), MVPs add value as the cross-Part integration narrative.

**Authoring path** — at brainstorm time via `architecture_brainstorm` Step 5's *MVP recommendation* sub-step (surfaces the option; does NOT inline-author), or retroactively via [`/mvp_plan`](../../commands/mvp_plan.md). Both paths route the actual authoring through `/mvp_plan` for voice consistency.

**Placement in roadmap.md** — between §6.5 derived views (after `## Ready for you (user-owned)`) and §6.6 Spawned sub-brainstorms.

**Schema:**

```markdown
## MVP Checkpoints

### MVP-N: <short playable-goal label>

- **Goal** — one-sentence narrative of the playable surface this MVP unlocks
- **Validates** — design commitments this MVP exercises in observable behavior
- **Acceptance** — the **cross-Part integration** rubric this MVP verifies: assertions no single Required Part's completion guarantees (named integration tests, multi-Part behavioral assertions, full-scene checks). Do NOT restate per-Part criteria — a Part can't reach `complete` without its own tests passing the gate, so per-Part acceptance rides on the Required-Part check-mark. These cross-Part checks are confirmed at playtest, not auto-computed. Each criterion verifiable without ambiguity.
- **Required Parts** — a **checkbox list, one Part per line**. `/mvp_plan` authors membership (always emitted unchecked `- [ ]`); `/update_roadmap` Step 5 drives the `[ ]`/`[x]` mark from each Part's State every run (idempotent, both directions). Link shape per line — Intra-roadmap: `- [ ] [[#Parts\|<Part Name>]]` (links to the `## Parts` heading; displayed as the Part name). Cross-roadmap: `- [ ] (parent) <Part Name>` plain text (default), OR `- [ ] [[../<folder>/roadmap\|<folder>]] § "<Part Name>"` when a clickable file link adds value. **Do NOT use `[[#Pos N Part Name]]` or `[[../<folder>/roadmap#Part Name]]`** — Parts are table rows, not headings; either shape silently falls through to file-top per common.md §6.2 / §6.8. **Submap-completion references:** prefer `"<sub-roadmap> fully complete"` / `"all child Parts complete"` over copying a count (`"all N Parts"`) — per §6.10 denormalization discipline, the count drifts when the child splits/adds/removes Parts, and the atlas (§6.13) shows the live figure anyway. Likewise, when an MVP cites a specific cross-roadmap Pos (`"<folder> Pos N (<Part>)"`), that Pos drifts on child renumber — `/roadmap_audit mvp` is the catch.
- **Excluded** — what's deliberately NOT in scope for this MVP (sets boundaries against scope creep)
- **Playtest plan** — **REQUIRED.** Concrete manual verification steps the user runs to confirm the MVP shipped. This is where the **Acceptance** field's cross-Part integration checks are actually verified. **Full content (rubric + operational runbook) is authored in the topic's `playtest.md`** — colocated with `roadmap.md`, one section per MVP (execution artifacts separate from planning docs, per the PRTesting shape); the roadmap field carries a 2–3 line summary + wikilink (`→ [[playtest#MVP-N\|MVP-N playtest]]`, with `playtest.md` section headings authored exactly `## MVP-N`). The runbook must name the actual editor surfaces — scene paths, exact `[ExportToolButton]` labels in order, plugin main-screen tabs, launch path — verified against the codebase; a rubric-only plan is an incomplete field. **The launch step specifically carries a stronger bar**: it must either have been executed end-to-end, or be marked `launch UNVERIFIED` in the plan — naming a real path is necessary and not sufficient; the deliverable is a procedure that demonstrably reaches a playable state. For MVPs whose acceptance is fully automated (every criterion a deterministic test, no human verdict), an explicit `Automated-only — no manual surface` declaration satisfies the requirement; an ABSENT plan never does. Where the playtest surface needs authoring (a stacking-frame scene, a demo scenario), the scene is named as a **Checkpoint artifact** below.
- **Checkpoint artifact** — *optional* named deliverable the MVP's playtest depends on but no single Required Part's completion guarantees (e.g., "a hand-authored innate `FixedTagGrantSpec`" or "a ≥4-NPC stacking frame scene"). If the artifact must be authored (scene, data), one Required Part's Trigger carries the deliverable. The `🧪 Ready for playtest` status presumes the artifact exists.
- **Status** — one of `🔨 In progress (X/Y parts)` · `🧪 Ready for playtest` · `✅ Verified`. `/update_roadmap` Step 5 computes the first two from Required-Part completion (X = complete count, Y = total); `✅ Verified` is set ONLY by `/mvp_plan verify MVP-N` after the user playtests. `/update_roadmap` preserves an existing `✅ Verified` unless a Required Part has regressed out of `complete`, in which case it downgrades (a stale ✅ is a lie) and flags the regression in its batch diff. **`🧪` without a Playtest plan is also a lie** — `/update_roadmap` Step 5 warns when a computed `🧪` MVP has no plan field.
```

**Numbering** — sequential (MVP-1, MVP-2, …). Reflects intended playable-progression order. Two MVPs at the same number is invalid; if two are parallel-safe, rename one to break the tie.

**Auto-computation** — scoped. `/update_roadmap` Step 5 recomputes exactly two derived elements per MVP — the Required-Part check-marks and the non-terminal Status — as pure idempotent functions of the Parts table (the same derived-view discipline as Mermaid / Currently-ready). It does NOT author, validate, or edit the narrative fields (Goal / Validates / Acceptance / Excluded / Playtest). The single piece of *stored* MVP state is `✅ Verified`, owned by the user via `/mvp_plan verify`. Drift is structurally impossible for the computed elements (recomputed both directions every run); the lone stored bit is downgraded automatically on Required-Part regression. Rationale: the milestone *narrative* stays single-owned by `/mvp_plan`; only the mechanical roll-up of Part state crosses over, and only as derived output.

**Revision** — the MVP *narrative* is owned by `/mvp_plan` (refine mode), analogous to how Parts are owned by `/update_roadmap`; `✅ Verified` is set via `/mvp_plan verify MVP-N`. Direct hand-edits work but bypass the revision-log discipline (and any hand-toggled check-mark / Status is overwritten by `/update_roadmap`'s next recompute).

### §6.12 Cross-roadmap dependencies (optional — sub-roadmaps only)

For sub-roadmaps (per §5.1 *deeper scope* outcome), an optional `## Cross-roadmap dependencies` section makes parent-roadmap deps explicit alongside the Parts table. Provides at-a-glance *"what does this sub-roadmap need from above?"* for sub-roadmap readers without forcing them to scan every Part's Deps cell for `(parent)` prefixes.

**Applicability** — sub-roadmaps only. Top-level topic roadmaps have no cross-roadmap deps in the parent direction (their deps go laterally or downward to spawned sub-roadmaps).

**When to author** — any sub-roadmap with 2+ Parts that depend on parent-roadmap Parts. Skip for self-contained sub-roadmaps with no cross-roadmap deps.

**Placement** — between §6.11 MVP Checkpoints and §6.7 Revision Log. Located here so it serves as a *boundaries* view above the historical log.

**Schema:**

```markdown
## Cross-roadmap dependencies

This sub-roadmap depends on parent-roadmap Parts at these edges:

| Sub-roadmap Part | Depends on parent Part | Parent state (as of YYYY-MM-DD) | Notes |
|---|---|---|---|
| Pos N | (parent) <Part name> | <state> | <one-line context — why this dep exists, what it unblocks> |
```

**Mermaid encoding** — parent-roadmap nodes use the `external` classDef (§6.4) + dashed-arrow edges (`-.->` Mermaid syntax). Distinguishes them from local sub-roadmap nodes at a glance.

**Stale-state caveat** — the *Parent state (as of YYYY-MM-DD)* column is a snapshot at sub-roadmap update time, not live data. `/update_roadmap` does not auto-refresh this column (would require eager parent-roadmap reads on every sub-roadmap update, violating the lazy-resolution policy per §6.8). Refresh manually by re-running `/update_roadmap` on the sub-roadmap when parent state shifts materially.

**Composition with sub-roadmap auto-promotion** — when all sub-roadmap Parts reach `complete`, the parent's `submap-pending` Part auto-promotes (per §6.3). At that point this section's content becomes historical — preserve it for audit trail; don't delete.

**Closing paragraph (conventional, not strict schema):** below the table, a one-sentence narrative naming the auto-promotion condition is helpful for the reader. Example: *"When all 8 sub-roadmap Parts reach `complete`, the parent-roadmap composite Part 'LevelDefinition extraction' (currently `submap-pending`) auto-promotes to `complete`. No separate close-out PR needed — sub-roadmap completion is the close-out."*

### §6.13 Roadmap Atlas (generated index)

No roadmap owns the view *across* roadmaps. `.claude/scripts/roadmap_atlas.py` parses every
`BrainstormingDesigns/**/roadmap.md` in both vault trees and renders two vault documents:

- **`Roadmap-Atlas.md`** — portfolio progress with subtree roll-ups, ranked next pickups, the
  human-gated queue, the MVP board, the unscoped surface, and parse-byproduct Health findings.
- **`Roadmap-Atlas-Archive.md`** — completed and abandoned roadmaps, plus every shipped Part.

Both are **generated — never hand-edited**, and live outside the git checkout. Regenerated by
[`/update_roadmap`](../../commands/update_roadmap.md) Step 9, `/session_end`, and
[`/roadmap_atlas`](../../commands/roadmap_atlas.md).

**This is the live home for every cross-roadmap and child-state figure.** A parent roadmap therefore
does not copy child counts or state breakdowns into its own prose (§6.6, §6.10, §6.11) — the reader
gets them recomputed from the child, which is where they are true.
