---
disable-model-invocation: true
---

# `/update_roadmap` Step 3 — validate the edited Parts table

Read at Step 3 of [`update_roadmap.md`](../update_roadmap.md). Not auto-loaded. Every warning below is non-blocking unless marked otherwise.

## Structural checks

- [ ] Every Deps entry resolves to an existing Part name (or a cross-folder `path#part-name`).
- [ ] Every `Pos` value is either an integer or `—`. Integers form a contiguous set — no gaps (`1, 2, 2, 3, 4` is fine; `1, 2, 4` is not). Duplicates (ties) are allowed.
- [ ] No Part has State=`complete` with un-completed Deps (signal of a state-mismatch — flag for user review).
- [ ] No backward `*-pending` transitions — `plan-pending` → `arch-pending` (not `arch-rework`), or `arch-pending` → `idea-pending` (not `idea-rework`). Emits `⚠ Backward to *-pending — did you mean *-rework? *-pending means 'never had this phase'; *-rework means 'had this phase, redo'.`
- [ ] No compound-`+` Part names — soft warn. Emits `⚠ Compound name — split-candidate signal. Verify this is one cohesive unit, not two stapled together.`

| Rationalization | Reality |
|---|---|
| "Demote `plan-pending` → `arch-pending` to signal 'this needs more design'" | `arch-pending` means *"never had arch brainstorm"*. If arch happened (even thinly), the honest move is `arch-rework`. The distinction is load-bearing: `*-rework` maps to dashed-stroke Mermaid class, signaling "came back" history; `*-pending` renders as solid, erasing it. |

## Trigger content

Per [`common.md §6.10`](../../skills/_brainstorm_shared/common.md) — every Part with State ∈ {`*-pending`, `*-rework`, `user-owned`} carries a Trigger whose content shape conforms to §6.10's sub-table. Per-shape soft-warns:

- `arch-pending` / `idea-pending`: `⚠ Trigger missing required field per common.md §6.10 — must include inventory + source pointer + out-of-scope.`
- `workshop-pending`: `⚠ workshop-pending requires a Trigger naming the user decision + source pointer (per common.md §6.10).`
- `prototype-pending`: `⚠ prototype-pending requires a Trigger naming the runtime question AND the shape of its answer ("a number" / "one of three enum values" / "yes/no"), plus the prototypes/<slug> path (per the prototype skill). A question with no stateable answer shape is not yet scoped — sharpen it or use arch-pending.`
- `idea-rework` / `arch-rework`: `⚠ *-rework requires a Trigger explaining what changed since the prior phase (per common.md §6.10).`
- `submap-pending`: `⚠ submap-pending requires a Trigger naming the child sub-roadmap path (per common.md §6.10). Canonical format: "Decomposed into <child-folder>/roadmap.md (N child Parts)."`
- `user-owned`: `⚠ user-owned requires a Trigger naming the user deliverable (per common.md §6.10).`

## Batch-shape checks

- [ ] **Sibling-arch-pending batch** — if this batch adds 2+ NEW Parts at State=`arch-pending`, emit `⚠ 2+ arch-pending Parts in one batch — apply Arch-pending consolidation litmus (architecture_brainstorm SKILL.md Step 5): do their open questions roll up to one session? If yes, consolidate into one Part with per-fork inventory in its Trigger.`
- [ ] **Intra-batch speculative dep** — if a new Part's Deps include another NEW Part in this same batch with State ∈ {`arch-pending`, `idea-pending`, `workshop-pending`}, emit `⚠ This Part depends on a *-pending sibling being added in the same batch — apply Downstream-of-fork guard (architecture_brainstorm SKILL.md Step 5). If this Part's scope would change based on the upstream session's decision, remove it and push inventory into the upstream Trigger instead.`

## Wikilink coherence

- [ ] **MVP Required-Parts wikilink coherence** — if a `## MVP Checkpoints` section exists, every Required-Parts checkbox line (intra-roadmap `[[#Parts|<Part Name>]]` or cross-roadmap `(parent) <Part Name>` / `[[../<folder>/roadmap|<folder>]] § "<Part Name>"`) must resolve **by Part name** to a current Parts table row (resolve cross-roadmap deps lazily per §6.8). Stale references (Part renamed/retired/split without MVP-section follow-through, or hand-edits that drifted) emit `⚠ MVP-N Required Parts references missing Part <name> — likely a rename/split/retire that bypassed MVP-section coordination. Run /mvp_plan refine MVP-N to repoint.` (`rename` op auto-rewrites so this fires only when rewriting was bypassed or after split/retire/hand-edit). This same name→row resolution feeds the Step 5 check-mark recompute.
- [ ] **Source-column wikilink anchor coherence** — for every `[[<doc>#<anchor>\|...]]` in the Source column, resolve the target doc (lazy — only when the target is local or reachable), then verify `<anchor>` matches a literal heading line in that doc. Obsidian resolves anchors by exact heading-text match — no kebab-case slugging, no whitespace normalization. Soft-warn `⚠ Source wikilink anchor not found in <doc>: <anchor>. Obsidian requires VERBATIM heading text — preserve the "Section N — " prefix and the " — " em-dash if present in the heading. Open the target doc, copy the heading line, paste verbatim.` Misses are the silent fall-through-to-file-top failure mode that this validator exists to catch (per common.md §6.2 verbatim rule). Skip the check when the target doc is non-local (cross-repo, external URL).
- [ ] **Deps-column cross-folder Part-dep shape** — for every Deps cell containing `[[<other>/roadmap#<part>...]]`, soft-warn `⚠ Cross-folder Part-dep wikilink #<part> cannot resolve — Parts live in table rows, not headings. Use the canonical shape per common.md §6.8: '(parent) <Part Name>' (short form, default) OR '[[../<folder>/roadmap\|<folder>]] § "<Part Name>"' (with display annotation when a clickable file link is wanted). The '#<part-name>' anchor will always fall through to file-top.` The same shape rule applies to Source cells that reference a Part on another roadmap (rather than a section on a design doc).

## Cross-roadmap staleness

- [ ] **Sub-roadmap shape change → parent staleness** — if the target roadmap has `parent-roadmap` frontmatter (it's a sub-roadmap) AND this batch changes the Part *count* (add / split / remove) or *Pos numbering*, emit `⚠ Sub-roadmap shape changed — the parent roadmap's submap reference (bare "(N child Parts)" count) + any MVP Pos-references to this sub-roadmap may now be stale. Run /roadmap_audit submap (and /roadmap_audit mvp) to verify; fix the parent via /update_roadmap (count) + /mvp_plan refine (MVP Pos-refs).` This **does NOT auto-edit the parent** — eager cross-roadmap mirroring is rejected by common.md §6.8 (lazy-resolution). It is a nudge only; the precise check + fix route lives in `/roadmap_audit` + the parent's own executors. Pure state transitions (no count/Pos change) don't fire it — they're already covered by §6.10's no-denormalize-child-state rule (the parent carries no child state to go stale).

## Disposition of warnings

If any validator fails, surface the issue in the diff preview with a `⚠` marker; user confirms whether to apply anyway or revise.

## Fix-recipes

Shown alongside the warning:

| Warning | Fix-recipe |
|---|---|
| Any `Trigger ... per common.md §6.10` warning | Look up the State's required Trigger content shape in [common.md §6.10](../../skills/_brainstorm_shared/common.md) and supply it. Examples: `*-rework` → "spec revealed scope creep" / "plan_part macro-drift hard-stop"; `workshop-pending` → the user-decision question + source link; `user-owned` → a concrete user deliverable like "design 5 floor scenes". |
| `complete with un-completed deps` | Check whether deps actually completed, or whether this Part was prematurely marked complete. |
| `Compound name — split-candidate signal` | Recheck whether the Part is one cohesive planning session or two stapled together; if the latter, split via `/update_roadmap split <part> into <a>, <b>`. |
| `Backward to *-pending` | Confirm intent: was the Part previously at the destination phase (correct: use `*-rework`) or genuinely never authored at this phase (rare; usually a typo)? |
| `MVP-N Required Parts references missing Part` | Run `/mvp_plan refine MVP-N` to repoint to the current Part name (or remove the reference if the underlying Part was retired). For splits, decide per-MVP which child Part(s) should replace the original reference. |
| `Source wikilink anchor not found in <doc>` | Open the target doc, copy the heading line VERBATIM (including the `Section N — ` prefix for top-level `## Section N — Title` headings and the ` — ` em-dash inside `### N.M — Title` sub-headings). Paste into the wikilink: `[[<doc>#<exact-heading>\|<short display>]]`. Escape the alias pipe as `\|` inside the table cell. If the Part's commitment legitimately spans 2+ design-doc sections, emit 2+ wikilinks separated by ` + ` — never fabricate a joined `#<a> and <b>` anchor that doesn't exist as a real heading. |
| `Cross-folder Part-dep wikilink cannot resolve` | Rewrite to the canonical shape per common.md §6.8: prefer `(parent) <Part Name>` in Deps cells (short, scans well, integrates with Mermaid external-node rendering); use `[[../<folder>/roadmap\|<folder>]] § "<Part Name>"` when a clickable file link adds value in a Source cell. Parts are table rows, never headings — `#<part-name>` anchors always fall through to file-top. |
| `Sub-roadmap shape changed — parent may be stale` | After this batch lands, run `/roadmap_audit submap` (count) + `/roadmap_audit mvp` (Pos-refs). Fix the parent's bare `(N child Parts)` count via `/update_roadmap` on the parent; fix MVP Pos-references via `/mvp_plan refine MVP-N`. Do NOT denormalize the per-state breakdown back into the parent (common.md §6.10) — only the bare count is permitted to live there. |
