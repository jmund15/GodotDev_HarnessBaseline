---
description: Retroactively author MVP Checkpoints section on an existing topic-folder roadmap.md. Socratic per-MVP. Recommended when roadmap has 5+ Parts and no MVP section.
---

# /mvp_plan

Authors the optional `## MVP Checkpoints` section on a topic-folder `roadmap.md` — milestone-narrative layer above the Parts table. Source of truth for the MVP schema: [`_brainstorm_shared/common.md §6.11`](../skills/_brainstorm_shared/common.md).

Existing MVPs are NOT overwritten by default — the command appends new MVPs or refines existing ones interactively. To rewrite the whole section, pass `--rewrite`.

**Sister surface, mostly-disjoint:** `/update_roadmap` owns Parts + Mermaid + derived views + revision log, and additionally *recomputes* two derived elements inside the MVP section each run — the Required-Part check-marks and the non-terminal Status line (pure functions of Part state). `/mvp_plan` owns the MVP *narrative* (Goal / Validates / Acceptance / Excluded / Playtest / Required-Part membership) and sets the terminal `✅ Verified` status via `verify` mode. Both share the same revision log; both validate against the same `common.md §6` schema.

**When to invoke:**
- Recommended automatically by `/architecture_brainstorm` Step 5 *MVP recommendation* sub-step when the design produces ≥5 Parts AND the target roadmap has no MVP section.
- Standalone after any brainstorm to add, refine, or rewrite MVPs.

---

## Arguments

- `/mvp_plan` (no args) — target = `roadmap.md` in current working directory (or nearest ancestor folder containing one).
- `/mvp_plan <path-to-roadmap.md>` — explicit target.
- `/mvp_plan --rewrite` — discard existing MVP section, author fresh. Confirms before discarding.
- `/mvp_plan refine MVP-N` — re-author a single existing MVP.
- `/mvp_plan verify MVP-N` — mark MVP-N's Status `✅ Verified` after a successful playtest. Sole writer of the terminal status. Requires all Required Parts `complete` (refuses otherwise, naming the incomplete Parts). See *Verify mode* below.

If target roadmap doesn't exist → print *"No roadmap.md at <path> or ancestors. Author Parts first via a brainstorm skill + /update_roadmap."* and exit.

**Edge-case argument handling:**

| Condition | Behavior |
|---|---|
| `--rewrite` against a roadmap with no existing `## MVP Checkpoints` section | Warn *"No existing MVP section to rewrite — behaving as fresh authoring."* and continue to Step 1. Skips the destructive-confirm prompt (nothing to discard). |
| `refine MVP-N` where `N` doesn't resolve to an existing MVP | List existing MVP numbers + labels (e.g., *"MVP-1: Combat parity, MVP-2: One playable room"*) and exit without edits. Don't auto-create — `refine` operates on existing MVPs only. Use default `/mvp_plan` (append mode) to add new MVPs. |
| `refine MVP-N` against a roadmap with no MVP section at all | Print *"No MVP section exists; refine has nothing to operate on. Run `/mvp_plan` (no args) to author the initial section."* and exit. |
| `verify MVP-N` where `N` doesn't resolve to an existing MVP | List existing MVP numbers + labels and exit without edits (same as `refine` miss). |
| `verify MVP-N` where not all Required Parts are `complete` | Refuse: *"MVP-N has incomplete Required Parts: <list>. Verify only after all Required Parts complete and you've playtested."* Exit without edits. |
| `<path-to-roadmap.md>` resolves to a file that exists but isn't a roadmap (no `## Parts` table, or frontmatter lacks `topic`) | Print *"`<path>` doesn't look like a roadmap.md (no Parts table or topic frontmatter). Aborting."* and exit. |

---

## Procedure

### Step 1 — Locate roadmap + load Parts

Find target `roadmap.md` per Arguments. Read frontmatter + Parts table. Extract Part names, States, Deps, Source links, design-doc paths.

**Sub-roadmap guard (FIRST check — runs before any other validation):** if the target roadmap's frontmatter has a `parent-roadmap` field (indicating it's a sub-roadmap per common.md §5.1 *deeper-scope* outcome), abort:

> *"MVP Checkpoints are top-level-roadmap-only per common.md §6.11. This roadmap is a sub-roadmap of `<parent-roadmap path from frontmatter>`. Sub-roadmaps don't carry their own MVPs — the playable-milestone narrative belongs to the parent (the project-level surface is where the player actually experiences the game; sub-roadmaps are impl-decomposition only). Run `/mvp_plan` on the parent roadmap instead, or use parent MVPs to reference this sub-roadmap's Parts directly."*

Exit without edits. The redirect-to-parent message is mandatory — don't silently no-op.

**Empty-Parts guard:** if no Parts exist → abort: *"Roadmap has no Parts. MVPs frame Parts as playable surfaces; author Parts first via /update_roadmap or a brainstorm skill."*

**Part-count litmus** (matches `common.md §6.11`'s *Skip litmus*):
- 1-4 Parts → warn: *"Roadmap has N Parts. MVPs are usually redundant at this scale — the Parts themselves are the milestones. Continue anyway?"*
- 5+ Parts → proceed.

**Existing MVP section detection** — if `## MVP Checkpoints` header found AND no `--rewrite` / `refine` arg → switch to **append mode**: show existing MVPs, ask user which to keep / add to / replace. `--rewrite` skips append-mode entirely but still requires the destructive-confirm before discarding the existing section (per Arguments — the *"Confirms before discarding"* clause).

### Step 2 — Bundle design-doc context (one read_files call)

For each Part, the Source-column wikilink points to a design-doc section. Bundle those design-doc paths into one `mcp__ai-worker__read_files` call asking for:
- Per-Part playtest moment (if named in the doc body)
- Per-Part design commitments (validates targets)
- Per-Part test plan (acceptance criteria candidates)
- Per-Part scene/feature deliverable (goal candidates)

This is the **only** synthesis-shaped read in this command (per CLAUDE.md §9). All subsequent reasoning runs in Claude.

If a Part's design doc is missing or its section header doesn't resolve → flag the Part as *"design-source missing; user supplies MVP fields manually"* and continue.

### Step 3 — Propose MVP count + grouping

Suggest an initial MVP count based on Part count + dep graph topology:

| Part count | Suggested MVP count |
|---|---|
| 5-8 | 2-3 |
| 9-15 | 4-6 |
| 16+ | 6-8 (or recommend sub-roadmap decomposition first if the roadmap is genuinely too large) |

Propose initial Part-to-MVP grouping based on dep graph: Parts that unblock a playable surface together cluster into one MVP. Show the proposal as a table:

```
MVP-1 (proposed): "Combat parity through encounter abstraction"
  Required Parts: Pos 1, (parent) Trait-Weight Resource Family
MVP-2 (proposed): "One playable room with combat encounter"
  Required Parts: Pos 1, Pos 2
...
```

Ask user to confirm the grouping or override (split, merge, reorder, rename). Each MVP represents a *playable surface*, not an arbitrary Parts chunk — the user is the authority on what counts as playable.

### Step 4 — Author each MVP Socratically (one at a time)

For each MVP-N in approved order, walk the 6 fields per the `common.md §6.11` schema. One question per turn; multiple-choice preferred when the design doc offers candidates.

1. **Goal** — propose the one-sentence playable-surface narrative based on Step 2's extracted goal candidates. *"MVP-N target: <proposed>. Approve / refine?"*

2. **Required Parts** — propose Parts from Step 3's grouping, authored as a **checkbox list, always emitted unchecked** (`- [ ] [[#Parts\|<Part Name>]]`); `/update_roadmap` drives the marks thereafter. Cross-roadmap deps: `- [ ] (parent) <Part Name>` plain text (default) OR `- [ ] [[../<folder>/roadmap\|<folder>]] § "<Part Name>"` when a clickable link adds value — never the `#Part Name` anchor form (Parts are rows, not headings; §6.11). Confirm or adjust.

3. **Excluded** — propose what the MVP scopes out. Pull candidates from (a) Parts assigned to later MVPs, (b) abandoned Parts, (c) explicit design-doc Open Questions. Frames scope-creep boundaries. *"MVP-N excludes: <candidates>. Add / remove?"*

4. **Acceptance** — propose the **cross-Part integration** checks this MVP verifies — assertions no single Required Part's completion guarantees (e.g. *"enemy spawns AND trail renders AND room loads together in one scene"*). Do NOT restate per-Part criteria: a Part can't reach `complete` without its own tests passing the gate, so per-Part acceptance rides on the Required-Part check-mark (§6.11). Each criterion verifiable without ambiguity (named integration test, multi-Part behavioral assertion, full-scene check). Vague checks (*"feels responsive"*) belong in Playtest plan. These cross-Part checks are confirmed at playtest, not auto-computed.

5. **Playtest plan** — propose concrete manual verification steps the user runs after the MVP ships. This is where *"feels responsive"* belongs — subjective rubric. Pull candidates from each Part's design-doc playtest moment.

6. **Validates** — propose design commitments being exercised. Pull from each Required Part's design-doc commitments. This field is the *"why this MVP earns its slot"* — connects the milestone narrative back to architectural commitments.

After the 6 fields, **initialize Status** to `🔨 In progress (0/Y parts)` (Y = Required-Parts count) — non-Socratic, no user input; `/update_roadmap` recomputes it on its next run. Never author `🧪`/`✅` at creation.

Get user approval per MVP before moving to the next. Save approved MVPs to scratch as you go (in-context only; not a file ledger — this command is single-session).

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

Single approval applies all. `Edit` (or `Write` if creating) `roadmap.md`. Bump `last_revised: YYYY-MM-DD` in frontmatter.

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
| "Every Part deserves its own MVP" | An MVP is a *playable surface*, not a Part. Multiple Parts collapse into one MVP when they're individually invisible to the player; that's the point. 1 MVP per Part defeats the milestone-narrative purpose — at that scale, the Parts table IS the milestones. |
| "MVP-N should include every Part it transitively depends on" | MVPs are not transitive closure of deps. They're playable-milestone snapshots. A Part may be Required for MVP-3 even if MVP-2 also Required it (both MVPs validate against that Part). |
| "Skip Acceptance — the test suite covers it" | The test suite is what each Part's design enumerates; Acceptance is what the *MVP* verifies (often cross-Part integration the per-Part tests don't cover). Different surface. |
| "Inline-author MVPs during /architecture_brainstorm Step 5 instead of routing through /mvp_plan" | The brainstorm SKILL recommends; it doesn't author. Inline MVP authoring inflates Step 5 cognitive load when the brainstorm is the wrong surface for milestone-narrative thinking. The cognitive-mode shift (per-Part Socratic → cross-Part playable-surface narrative) is real; honor it by using the separate command. |
| "Hand-edit MVPs directly in roadmap.md" | Like Parts, the MVP narrative is an authoring surface owned by this command. Direct edits work but bypass the revision-log discipline + the §6.11 schema validator. Prefer `/mvp_plan refine MVP-N` for material changes. (Hand-toggling the Required-Part check-marks or Status is futile — `/update_roadmap` recomputes them from Part state next run.) |
| "Auto-fire /mvp_plan from /update_roadmap when Parts hit 5+" | Routing-confusion failure mode. `/update_roadmap` owns Parts; `/mvp_plan` owns the MVP *narrative*. `/update_roadmap` may *recompute* the derived check-marks + Status inside the MVP section (pure Part-state roll-up), but it never *authors* an MVP — and it must not auto-*fire* `/mvp_plan`. The authoring recommendation surfaces from the brainstorm SKILL (Step 5 *MVP recommendation*) at the natural moment, not from inside `/update_roadmap`. |
| "Drive sub-roadmap creation from /mvp_plan when an MVP gets too big" | Out of scope. Sub-roadmap creation is a brainstorm-skill spawn-placement decision (`common.md §5.1`), driven by the *deeper scope* criterion. If an MVP's Required Parts span too much surface, that's a signal to invoke `/architecture_brainstorm` for the over-scoped Part, not to author the decomposition here. |

---

## MCP-offline policy

Non-event — `roadmap.md` is a vault file edited with native `Read`/`Edit`. See [`_brainstorm_shared/common.md §3`](../skills/_brainstorm_shared/common.md). The Step 2 design-doc bundling uses `mcp__ai-worker__read_files`, which is independent of Obsidian MCP.

---

## Cross-references

- [`_brainstorm_shared/common.md §6.11`](../skills/_brainstorm_shared/common.md) — MVP Checkpoints schema (source of truth)
- [`_brainstorm_shared/common.md §6.5`](../skills/_brainstorm_shared/common.md) — derived view section placement (MVPs sit between §6.5 and §6.6)
- [`_brainstorm_shared/common.md §6`](../skills/_brainstorm_shared/common.md) — full roadmap.md schema
- [`architecture_brainstorm/SKILL.md`](../skills/architecture_brainstorm/SKILL.md) Step 5 *MVP recommendation* — surfaces this command when Parts ≥ 5 AND no existing MVP section
- [`/update_roadmap`](update_roadmap.md) — sibling executor for Parts + Mermaid + derived views; *recomputes* the Required-Part check-marks + Status inside the MVP section each run, but never authors the narrative fields
- `obsidian_conventions` skill — wikilink and heading-anchor conventions for `Required Parts` links
