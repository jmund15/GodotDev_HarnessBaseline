---
disable-model-invocation: true
---

# `/update_roadmap` Steps 4–5 — Mermaid and derived views

Read at Steps 4 and 5 of [`update_roadmap.md`](../update_roadmap.md). Not auto-loaded.

## Step 4 — Render Mermaid diagram

Generate deterministically from the Parts table:

- **Node ID:** Part name slugified (`Foundation+Refactor` → `Foundation_Refactor`).
- **Node label:** Part name **verbatim, NO `Pos N. ` prefix.** Mermaid's CommonMark parser interprets leading `N. ` (also `- `, `* `, `# `, `> `) as markdown list/heading syntax and emits "Unsupported markdown: list" warnings — one per offending node. Pos is encoded by graph rank, not in the label. If the renderer must surface Pos in-label, use `N: Name` / `[N] Name` / `Pos N — Name` (em-dash-separated) — never `N. Name`. Full constraint: [`mermaid_diagrams` skill](../../skills/mermaid_diagrams/SKILL.md) → *Renderer constraints* + [`common.md`](../../skills/_brainstorm_shared/common.md) §6.4 → *Node label rule*.
- **Class:** one of `plan | arch | idea | rework | workshop | complete | abandoned | user | submap` (rework states map to `rework` class which is dashed-stroke; `user-owned` maps to `user`; `submap-pending` maps to `submap` which is long-dashed teal; `prototype-pending` maps to `workshop` — both terminate in a human verdict, and common.md §6.4's classDef block defines no class of its own for that state).
- **Edges:** `<dep> --> <part>` for each Deps entry where `<dep>` is a local Part.
- **Cross-folder edges:** dashed line with the cross-folder path as edge label.

Replace the existing Mermaid block in roadmap.md (between the standard `## Current state — Mermaid` heading and the next `##`).

Palette, classDef discipline, and renderer constraints: see the `mermaid_diagrams` skill. The classDef block in `common.md §6.4` already encodes the canonical roadmap mapping — emit it verbatim.

| Rationalization | Reality |
|---|---|
| "Don't regen Mermaid; the old one is close enough" | Mermaid drift is silent and accumulates. Always regen — costs are negligible. |

## Step 5 — Compute derived views

Three computed sections rendered below Mermaid:

**Currently ready to execute** — Parts where State ∈ {`idea-pending`, `arch-pending`, `plan-pending`, `idea-rework`, `arch-rework`} AND every Dep has State=`complete` (resolve cross-folder deps lazily per §6.8).

**Blocked / awaiting deps** — Parts where State ∈ {`idea-pending`, `arch-pending`, `plan-pending`, `idea-rework`, `arch-rework`} having ANY Dep with State ≠ `complete`. Show the blocking dep and its current State.

**Ready for you (user-owned)** — Parts where State ∈ {`user-owned`, `prototype-pending`} AND every Dep has State=`complete`. Bullet list with Trigger displayed (the user deliverable; for `prototype-pending`, the runtime question the user answers by playing the prototype build).

Replace existing derived-view sections in roadmap.md.

| Rationalization | Reality |
|---|---|
| "Skip cross-folder Currently-Ready resolution; this folder is local" | The lazy-resolution policy exists for exactly this — run it. If no cross-folder deps exist, it costs nothing. |

## Step 5 — MVP Checkpoints recompute

In-place; only when a `## MVP Checkpoints` section exists. UNLIKE the three views above, this is NOT a wholesale section replacement: the narrative fields are `/mvp_plan`'s and must survive byte-for-byte. Mutate exactly two things per MVP, leaving Goal / Validates / Acceptance / Excluded / Playtest untouched:

1. **Required-Part check-marks** — for each `- [ ]`/`- [x]` line under **Required Parts**, resolve its Part reference by name (same resolution as the Step 3 coherence validator) and set the mark to `[x]` iff that Part's State is `complete`, else `[ ]`. Recompute every run, both directions — never additive. Cross-roadmap Parts (`(parent) <Part>` or `[[../<folder>/roadmap|...]]`) resolve lazily per §6.8; when the target isn't locally reachable, leave that line's mark as-authored and emit `⚠ MVP-N: cross-roadmap Part <name> unresolvable — mark left as-authored.`
2. **Status line** — compute from the Required-Part completion count (X complete of Y):
   - `X < Y` → `🔨 In progress (X/Y parts)`
   - `X = Y` AND current Status is not `✅ Verified` → `🧪 Ready for playtest`
   - current Status is `✅ Verified` AND `X = Y` → preserve `✅ Verified`
   - current Status is `✅ Verified` AND `X < Y` → **downgrade** to the computed `🔨 In progress (X/Y parts)` and record a regression flag for Step 7: `⚠ MVP-N was Verified but Part <name> regressed out of complete — downgraded.`
   - **Playtest-plan guard (only when computed Status is `🧪 Ready for playtest`)** — if the MVP has no `Playtest plan` field (absent or empty, and not the explicit `Automated-only — no manual surface` declaration per common.md §6.11), emit for Step 7: `⚠ MVP-N flipped to 🧪 Ready for playtest but has no Playtest plan — author it via /mvp_plan refine MVP-N before playtesting.` The flip is still applied (Part completion is factual); the warning makes the narrative gap visible at the exact moment the status promises playtestability.
