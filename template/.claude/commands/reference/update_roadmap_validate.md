---
disable-model-invocation: true
---

# `/update_roadmap` Step 3 — validate the edited Parts table

Read at Step 3 of [`update_roadmap.md`](../update_roadmap.md). Not auto-loaded. Every warning below is non-blocking unless marked otherwise. The generator's single-roadmap mode (Step 3) runs the Structural and Wikilink checks and returns each warning with its id; you run Trigger content, Batch-shape and Cross-roadmap staleness. Without a generator, run every check by hand.

## Structural checks

- `deps-resolve` — every Deps entry resolves to an existing Part name (or a cross-folder `path#part-name`).
- `pos-contiguous` — every `Pos` is an integer or `—`, and the integers form a contiguous set with no gaps; ties are allowed.
- `complete-open-deps` — no `complete` Part has an un-completed Dep (a state mismatch; flag it for user review).
- `backward-pending` — no Part moves back to an earlier `*-pending` state (`plan-pending` → `arch-pending`, or `arch-pending` → `idea-pending`); the move was to be `*-rework`. Compared with the file on disk.
- `compound-name` — no `+` in a Part name the batch adds or renames; a soft warn that the Part may be two units stapled together.

| Rationalization | Reality |
|---|---|
| "Demote `plan-pending` → `arch-pending` to signal 'this needs more design'" | `arch-pending` means *"never had arch brainstorm"*. If arch happened (even thinly), the honest move is `arch-rework`. The distinction is load-bearing: `*-rework` maps to dashed-stroke Mermaid class, signaling "came back" history; `*-pending` renders as solid, erasing it. |

## Trigger content

Per [`roadmap_triggers_mvp.md §6.10`](../../skills/_brainstorm_shared/roadmap_triggers_mvp.md) — every Part with State ∈ {`*-pending`, `*-rework`, `user-owned`} carries a Trigger whose content shape conforms to §6.10's sub-table. Emit one soft-warn per non-conforming Part: `⚠ <Part> (<state>): Trigger does not meet the §6.10 row for <state>.` The content-shape judgment against that row is yours. A Part this batch moves to `complete` must carry §6.10's `complete` row, and that check blocks: drop the transition from the batch, show `⚠ BLOCKING <Part>: no close record, stays <state>` in the Step 7 diff, and apply the rest. The owner's own words ordering the flip satisfy it. A `Walked:` path must exist, open with `run: source=` and end with `complete: true`; `Walked: none` is accepted only when the plan's Root acceptance table holds no rendered-scenario cell other than `none`.

## Batch-shape checks

- [ ] **Sibling-arch-pending batch** — if this batch adds more than one NEW Part at State=`arch-pending`, emit `⚠ Several arch-pending Parts in one batch — apply the Arch-pending consolidation litmus ([`architecture_brainstorm/reference/part_authoring.md`](../../skills/architecture_brainstorm/reference/part_authoring.md) §Arch-pending consolidation litmus): do their open questions roll up to one session? If yes, consolidate into one Part with per-fork inventory in its Trigger.`
- [ ] **Intra-batch speculative dep** — if a new Part's Deps include another NEW Part in this same batch with State ∈ {`arch-pending`, `idea-pending`, `workshop-pending`}, emit `⚠ This Part depends on a *-pending sibling being added in the same batch — apply the Downstream-of-fork guard ([`architecture_brainstorm/reference/part_authoring.md`](../../skills/architecture_brainstorm/reference/part_authoring.md) §Downstream-of-fork guard). If this Part's scope would change based on the upstream session's decision, remove it and push inventory into the upstream Trigger instead.`

## Wikilink coherence

- `mvp-unresolvable` — when a `## MVP Checkpoints` section exists, every Required-Parts line resolves by Part name to a current Parts table row, cross-roadmap ones per §6.8. `rename` rewrites these, so it fires after a split, a retire or a hand-edit.
- `source-anchor` — every `[[<doc>#<anchor>\|...]]` in a Source cell matches a literal heading line in `<doc>` (`obsidian_conventions` skill, *Wikilinks & Heading Anchors*). A target the generator cannot read is reported `unchecked`.
- `deps-anchor` — no Deps or Source cell uses a `[[<other>/roadmap#<part>...]]` Part anchor (`roadmap_schema.md` §6.8).

## Parse health

- `parts-unrecovered` — the generator recovered no Part from `## Parts`: the table has no header carrying `Part` and `State` columns, or no row survives.
- `state-unrecognized` — a Parts row's State cell is outside the state vocabulary. The warning says whether the generator counted the Part under the cell's leading state or dropped it from every count and view.

## Cross-roadmap staleness

- [ ] **Sub-roadmap shape change → parent staleness** — if the target roadmap has `parent-roadmap` frontmatter (it's a sub-roadmap) AND this batch changes the Part *count* (add / split / remove) or *Pos numbering*, emit `⚠ Sub-roadmap shape changed — the parent roadmap's submap reference (bare "(N child Parts)" count) + any MVP Pos-references to this sub-roadmap may now be stale. Check the parent's submap count and MVP Pos-refs against the child; fix the parent via /update_roadmap (count) + /mvp_plan refine (MVP Pos-refs).` This **does NOT auto-edit the parent** — eager cross-roadmap mirroring is rejected by roadmap_schema.md §6.8 (lazy-resolution). It is a nudge only; the precise check lives in the project's cross-roadmap audit, where it keeps one, and the fix in the parent's own executors. Pure state transitions (no count/Pos change) don't fire it — they're already covered by §6.10's no-denormalize-child-state rule (the parent carries no child state to go stale).

## Disposition of warnings

If any validator fails, surface the issue in the diff preview with a `⚠` marker, fix it, and re-validate. Ask the user only when the fix is a design decision.

## Fix-recipes

Shown alongside the warning:

| Warning | Fix-recipe |
|---|---|
| Any `Trigger ... per roadmap_triggers_mvp.md §6.10` warning | Look up the State's required Trigger content shape in [roadmap_triggers_mvp.md §6.10](../../skills/_brainstorm_shared/roadmap_triggers_mvp.md) and supply it. Examples: `*-rework` → "spec revealed scope creep" / "plan_part macro-drift hard-stop"; `workshop-pending` → the user-decision question + source link; `user-owned` → a concrete user deliverable like "design 5 floor scenes". |
| `complete-open-deps` | Check whether deps actually completed, or whether this Part was prematurely marked complete. |
| `compound-name` | Recheck whether the Part is one cohesive planning session or two stapled together; if the latter, split via `/update_roadmap split <part> into <a>, <b>`. |
| `backward-pending` | Confirm intent: was the Part previously at the destination phase (correct: use `*-rework`) or genuinely never authored at this phase (rare; usually a typo)? |
| `mvp-unresolvable` | Run `/mvp_plan refine MVP-N` to repoint to the current Part name (or remove the reference if the underlying Part was retired). For splits, decide per-MVP which child Part(s) should replace the original reference. |
| `parts-unrecovered` | Restore the Parts table header (`Part` and `State` columns) or its rows; every derived view and count is empty until it parses. |
| `state-unrecognized` | Rewrite the State cell to one vocabulary state (`roadmap_schema.md` §6.3) and move any annotation into the Trigger; a dropped Part returns to every count and view when its State parses. |
| `source-anchor` | Open the target doc, copy the heading line VERBATIM (including the `Section N — ` prefix for top-level `## Section N — Title` headings and the ` — ` em-dash inside `### N.M — Title` sub-headings). Paste into the wikilink: `[[<doc>#<exact-heading>\|<short display>]]`. Escape the alias pipe as `\|` inside the table cell. If the Part's commitment legitimately spans 2+ design-doc sections, emit 2+ wikilinks separated by ` + ` — never fabricate a joined `#<a> and <b>` anchor that doesn't exist as a real heading. |
| `deps-anchor` | Rewrite to the canonical shape per roadmap_schema.md §6.8: `(parent) <Part Name>` in Deps cells, a file wikilink plus prose Part name in Source cells. |
| `Sub-roadmap shape changed — parent may be stale` | After this batch lands, fix the parent's bare `(N child Parts)` count via `/update_roadmap` on the parent and its MVP Pos-references via `/mvp_plan refine MVP-N`. Do NOT denormalize the per-state breakdown back into the parent (roadmap_triggers_mvp.md §6.10). |
