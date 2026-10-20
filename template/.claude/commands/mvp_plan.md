---
description: Add, refine or verify MVP checkpoints above Parts when Parts hide usable milestones
---

# /mvp_plan

Authors the optional `## MVP Checkpoints` section on a topic-folder `roadmap.md` — milestone-narrative layer above the Parts table. Source of truth for the MVP schema: [`_brainstorm_shared/roadmap_triggers_mvp.md §6.11`](../skills/_brainstorm_shared/roadmap_triggers_mvp.md).

Existing MVPs are not overwritten — see *Step 1* for append-mode detection and the `--rewrite` exit.

**Sister surface, mostly-disjoint:** `/update_roadmap` owns Parts + Mermaid + derived views + revision log, and recomputes the MVP section's derived elements each run (`skills/_brainstorm_shared/roadmap_triggers_mvp.md` §6.11 *Auto-computation*). `/mvp_plan` owns the MVP *narrative* (Goal / Validates / Acceptance / Excluded / Playtest / Required-Part membership) and sets the terminal `✅ Verified` status via `verify` mode. Both share the same revision log; both validate against the same `common.md` §6 schema.

**When to invoke:**
- Recommend per the authoring litmus in `skills/_brainstorm_shared/roadmap_triggers_mvp.md` §6.11's *When to author*.
- Standalone after any brainstorm to add, refine, or rewrite MVPs.

---

## Arguments

- `/mvp_plan` (no args) — target = `roadmap.md` in current working directory (or nearest ancestor folder containing one).
- `/mvp_plan <path-to-roadmap.md>` — explicit target.
- `/mvp_plan --rewrite` — discard existing MVP section, author fresh. Confirms before discarding.
- `/mvp_plan refine MVP-N` — re-author a single existing MVP.
- `/mvp_plan verify MVP-N` — mark MVP-N's Status `✅ Verified` after a successful playtest. Sole writer of the terminal status. See *Verify mode* below.

If target roadmap doesn't exist → print *"No roadmap.md at <path> or ancestors. Author Parts first via a brainstorm skill + /update_roadmap."* and exit.

**Edge-case argument handling:**

| Condition | Behavior |
|---|---|
| `--rewrite` against a roadmap with no existing `## MVP Checkpoints` section | Warn *"No existing MVP section to rewrite — behaving as fresh authoring."* and continue to Step 1. Skips the destructive-confirm prompt (nothing to discard). |
| `refine MVP-N` where `N` doesn't resolve to an existing MVP | List existing MVP numbers + labels (e.g., *"MVP-1: Combat parity, MVP-2: One playable room"*) and exit without edits. Don't auto-create — `refine` operates on existing MVPs only. Use default `/mvp_plan` (append mode) to add new MVPs. |
| `refine MVP-N` against a roadmap with no MVP section at all | Print *"No MVP section exists; refine has nothing to operate on. Run `/mvp_plan` (no args) to author the initial section."* and exit. |
| `verify MVP-N` where `N` doesn't resolve to an existing MVP | List existing MVP numbers + labels and exit without edits (same as `refine` miss). |
| `<path-to-roadmap.md>` resolves to a file that exists but isn't a roadmap (no `## Parts` table, or frontmatter lacks `topic`) | Print *"`<path>` doesn't look like a roadmap.md (no Parts table or topic frontmatter). Aborting."* and exit. |

---

## Procedure

### Step 1 — Locate roadmap + load Parts

Find the target `roadmap.md` per Arguments and load its Parts.

**Sub-roadmap guard (FIRST check — runs before any other validation):** if the target roadmap's frontmatter has a `parent-roadmap` field (indicating it's a sub-roadmap per common.md §5.1 *deeper-scope* outcome), abort:

> *"MVP Checkpoints are top-level-roadmap-only per roadmap_triggers_mvp.md §6.11. This roadmap is a sub-roadmap of `<parent-roadmap path from frontmatter>`. Run `/mvp_plan` on the parent roadmap."*

Exit without edits.

**Empty-Parts guard:** if no Parts exist → abort: *"Roadmap has no Parts. MVPs frame Parts as playable surfaces; author Parts first via /update_roadmap or a brainstorm skill."*

**Playable-milestone litmus** (`skills/_brainstorm_shared/roadmap_triggers_mvp.md` §6.11 *When to author*): proceed only when the owner wants an additional milestone layer; do not add a numeric warning or confirmation solely because the roadmap is short.

**Existing MVP section detection** — if `## MVP Checkpoints` header found AND no `--rewrite` / `refine` arg → switch to **append mode**: existing MVPs are not overwritten by default, so show them and ask the user which to keep / add to / replace. `--rewrite` skips append-mode entirely.

### Step 2 — Bundle design-doc context (one read_files call — it replaces the per-Part reads)

For each Part, the Source-column wikilink points to a design-doc section. Bundle those design-doc paths into one `mcp__ai-worker__read_files` call (offline fallback: the scout tier per `doc_production.md §3`) asking for:
- Per-Part playtest moment (if named in the doc body)
- Per-Part design commitments (validates targets)
- Per-Part test plan (acceptance criteria candidates)
- Per-Part scene/feature deliverable (goal candidates)

This is the **only** synthesis-shaped read in this command (per CLAUDE.core.md §9). All subsequent reasoning runs in Claude.

If a Part's design doc is missing or its section header doesn't resolve → flag the Part as *"design-source missing; user supplies MVP fields manually"* and continue.

### Step 3 — Propose MVP count + grouping

Group Parts by distinct playable integration surface and dependency graph. Propose one MVP per surface that needs a cross-Part acceptance or playtest; do not derive an MVP count from Parts count.

Show the proposed grouping as a table:

```
MVP-1 (proposed): "Combat parity through encounter abstraction"
  Required Parts: Pos 1, (parent) Trait-Weight Resource Family
MVP-2 (proposed): "One playable room with combat encounter"
  Required Parts: Pos 1, Pos 2
...
```

Ask user to confirm the grouping or override (split, merge, reorder, rename). Each MVP represents a *playable surface*, not an arbitrary Parts chunk — the user is the authority on what counts as playable.

### Step 4 — Author each MVP Socratically (one at a time)

For each MVP-N in approved order, author the seven `roadmap_triggers_mvp.md §6.11` fields and verify each before applying the approved MVP. Resolve owner-dependent fields in coherent owner-batched packets (`CLAUDE.core.md` §Preferences), deferring a field only when it depends on a prior answer. §6.11 owns each field's shape, including Required Parts' checkbox and link forms, Acceptance as cross-Part checks only, and the Playtest plan's two branches (a manual surface, or the explicit `Automated-only — no manual surface` declaration). Done when every field is present and Playtest plan is one of those two branches.

Candidate sources: Goal from Step 2's extracted goal candidates; Required Parts from Step 3's grouping; Excluded from Parts assigned to later MVPs, abandoned Parts and design-doc Open Questions; Validates from each Required Part's design-doc commitments; Playtest plan from each Part's design-doc playtest moment.

- **Playtest plan.** The full content is authored in the topic's `playtest.md` (create it here if absent). Verify each runbook step against the codebase's actual editor surface: exact `[ExportToolButton]` labels in order, plugin main-screen tabs, scene paths, launch path. A rubric-only plan is incomplete.
- **Checkpoint artifact** (optional; ask when the Playtest plan names a surface that must be authored). Name the deliverable and which Required Part's Trigger carries it, so the plan's scene path exists by Status-flip time. Default deliverable: the scenario the project's playtest-artifact command emits for `--part <Required Part>`, where it ships one.

After the seven fields, **initialize Status** to `🔨 In progress (0/Y parts)` (Y = Required-Parts count) — non-Socratic, no user input; `/update_roadmap` recomputes it on its next run. Never author `🧪`/`✅` at creation.

### Step 5 — Render, present diff, apply

Compose the full `## MVP Checkpoints` section per the §6.11 schema. Placement: between `## Ready for you (user-owned)` (last §6.5 derived view) and `## Spawned sub-brainstorms` (§6.6). If the existing roadmap has a non-canonical ordering (e.g., MVP section already between Mermaid and Blocked, per the encounter-extraction example), preserve the existing position — don't relocate; this command is appending content, not restructuring.

Show the user the full proposed section + the revision log entry:

```markdown
## Revision Log
...
- YYYY-MM-DD — Authored N MVP Checkpoints via /mvp_plan.
```

For refine mode: revision log line names the affected MVP(s):
```markdown
- YYYY-MM-DD — Refined MVP-N (<what changed>) via /mvp_plan.
```

Display the final diff and apply the already approved MVPs. Ask again only if rendering adds a taste-bearing choice not covered by the accepted field batches. `Edit` (or `Write` if creating) `roadmap.md`; bump `last_revised: YYYY-MM-DD` in frontmatter.

### Step 6 — Cross-link recommendation (advisory)

After applying, suggest the user link each MVP from the parent design doc's Open Questions section (or wherever milestone-narrative context lives). Do NOT auto-edit the design doc — recommend. Example surfacing:

> *"MVP Checkpoints applied. Consider linking from `<design-doc>#Open Questions` so future readers find the milestone narrative from the design doc. Skip if the design doc already names playtest moments inline per-Part."*

---

## Verify mode — `/mvp_plan verify MVP-N`

Sets MVP-N's Status to `✅ Verified` after the user confirms a successful playtest. This is the **only** writer of the terminal status; `/update_roadmap` preserves it across runs and downgrades it only if a Required Part later regresses out of `complete`.

1. Locate the roadmap (per Step 1) and resolve MVP-N. Miss → list existing MVP numbers + labels, exit (per Arguments edge-case table).
2. Check every Required Part is `complete` (resolve cross-roadmap deps lazily per §6.8). Any incomplete → refuse: *"MVP-N has incomplete Required Parts: <list>. Verify only after all Required Parts complete and you've playtested."* Exit.
3. Confirm the user actually playtested: *"Mark MVP-N ✅ Verified? This asserts you playtested the milestone and it works."*
4. On approval, set the Status line to `✅ Verified`, append a revision-log line `- YYYY-MM-DD — MVP-N verified (playtest passed) via /mvp_plan verify.`, and bump `last_revised`. Touch nothing else.

---

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Every Part deserves its own MVP" | An MVP is a *playable surface*, not a Part, and what counts as playable is the owner's call (`skills/_brainstorm_shared/roadmap_triggers_mvp.md` §6.11). One MVP per Part defeats the milestone-narrative purpose — at that scale, the Parts table IS the milestones. |
| "MVP-N should include every Part it transitively depends on" | MVPs are not transitive closure of deps. They're playable-milestone snapshots. A Part may be Required for MVP-3 even if MVP-2 also Required it (both MVPs validate against that Part). |
| "Skip Acceptance — the test suite covers it" | The test suite is what each Part's design enumerates; Acceptance is what the *MVP* verifies (often cross-Part integration the per-Part tests don't cover). Different surface. |
| "Inline-author MVPs during `/architecture_brainstorm` Part authoring instead of routing through `/mvp_plan`" | The `architecture_brainstorm` skill surfaces the recommendation; it doesn't author. The MVP narrative is owned by `/mvp_plan` (`skills/_brainstorm_shared/roadmap_triggers_mvp.md` §6.11). |
| "Hand-edit MVPs directly in roadmap.md" | The MVP narrative is owned by this command (`skills/_brainstorm_shared/roadmap_triggers_mvp.md` §6.11). Prefer `/mvp_plan refine MVP-N` for material changes. |
| "Auto-fire /mvp_plan from /update_roadmap when Parts hide a playable milestone" | Routing-confusion failure mode. `/update_roadmap` must not auto-*fire* `/mvp_plan` (`skills/_brainstorm_shared/roadmap_triggers_mvp.md` §6.11). |
| "Drive sub-roadmap creation from /mvp_plan when an MVP gets too big" | Out of scope. Sub-roadmap creation is a brainstorm-skill spawn-placement decision (`common.md §5.1`), driven by the *deeper scope* criterion. If an MVP's Required Parts span too much surface, that's a signal to invoke `/architecture_brainstorm` for the over-scoped Part, not to author the decomposition here. |

---

## Cross-references

- [`_brainstorm_shared/roadmap_triggers_mvp.md §6.11`](../skills/_brainstorm_shared/roadmap_triggers_mvp.md) — MVP Checkpoints schema (source of truth)
- [`_brainstorm_shared/common.md §6.5`](../skills/_brainstorm_shared/common.md) — derived view section placement (MVPs sit between §6.5 and §6.6)
- [`_brainstorm_shared/common.md §6`](../skills/_brainstorm_shared/common.md) — full roadmap.md schema
- The `architecture_brainstorm` skill's Part-authoring MVP recommendation surfaces this command
- [`/update_roadmap`](update_roadmap.md) — sibling executor for Parts + Mermaid + derived views
- `obsidian_conventions` skill — wikilink and heading-anchor conventions for `Required Parts` links
