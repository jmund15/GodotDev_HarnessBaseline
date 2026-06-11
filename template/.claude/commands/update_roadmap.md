---
description: Update topic-folder roadmap.md — Parts table, mermaid regen, derived views, revision log. Brainstorm skills invoke; standalone for Part transitions.
---

# /update_roadmap

Single executor for `roadmap.md` edits. Source of truth for the roadmap schema: [`_brainstorm_shared/common.md §6`](../skills/_brainstorm_shared/common.md).

**Mode: batch-propose.** All proposed edits assembled into one diff; single user approval applies everything (Parts table + Mermaid + derived views + Spawned sub-brainstorms + MVP check-mark/Status recompute + Revision Log). Iterative per-edit confirmation is NOT this command's job.

**Invocation contexts:**
- **From `/architecture_brainstorm`** (final step): just-saved arch design doc + the Parts the design produced.
- **From `/idea_brainstorm`** (final step): just-saved idea-bank doc + Per-Cluster Routing → Part state mapping.
- **Standalone (user-driven):** ad-hoc Part transitions (`mark complete <part>`, `transition <part> to <state>`, `rename <old> <new>`, `split <part> into <new-1>, <new-2>`, `retire <part>`).

---

## Arguments

- **From a brainstorm skill (no explicit args):** the calling skill supplies context (saved doc path + Parts list). Locate the topic-folder `roadmap.md` from the saved doc's parent directory.
- **Standalone (verb-form args):** `/update_roadmap <verb> <part-name> [args]` — see the *Standalone operations* table below. Target roadmap = current working directory's `roadmap.md` (or nearest ancestor folder containing one).
- **No argument and no calling-skill context:** print usage (this Arguments section + the Standalone operations table) and exit without edits.
- **Malformed `<part-name>`** (doesn't resolve in the target roadmap): list candidate Parts and exit without edits. Don't auto-propose creating a new Part — Part creation routes through the brainstorm-skill invocation, not standalone.

---

## Procedure

### Step 1 — Locate `roadmap.md`

Search the current topic folder (parent of just-saved design doc, OR current working dir for standalone).

- **Found:** load it. Proceed to Step 2.
- **Not found:** propose creating one from the schema in `common.md §6`. Default frontmatter from the calling skill's frontmatter (or user-provided for standalone). Initial Parts populated from the calling brainstorm's outputs. Proceed to Step 2 after confirmation.

**Child sub-roadmap creation path** (when invoked from `architecture_brainstorm` Step 8 *Multi-roadmap case* invocation 2 for a child-subfolder spawn): the file path is `<parent-folder>/<child-slug>/roadmap.md` (NEW file). Frontmatter populated per common.md §6.1 + sub-roadmap conventions:

| Field | Source |
|---|---|
| `created` | today |
| `topic` | child topic slug (from brainstorm invocation) |
| `scope-level` | inherited from parent-roadmap frontmatter |
| `parent-roadmap` | `../<parent-folder>/roadmap.md` (relative path) |
| `status` | `active` |
| `last_revised` | today |
| `parent-composite-part` *(optional)* | `"<parent-Part name>" (submap-pending on parent)` — human-legible linkage |
| `brainstorm-source` *(optional)* | **Default `./arch.md` / `./ideas.md`** — the submap's own design doc is co-located in THIS child subfolder (the deeper-scope fresh-brainstorm case per common.md §5.1; the brainstorm Step 6/5 saves it here). Use `../arch-<slug>.md` ONLY when the submap decomposes a pre-existing parent-folder cluster doc that legitimately stays in the parent (e.g. `arch-encounter-definition.md`). Prefer co-location; a `../` pointer with a freshly-authored submap doc is the mis-save signal. |

Initial Parts populated from architecture_brainstorm Step 5 output. Cross-roadmap deps preserved with `(parent) <Part>` prefix or `[[../<parent>/roadmap#Part]]` wikilink (per common.md §6.8 lazy-resolution policy).

If 2+ Parts have cross-roadmap deps, ALSO prompt the user to author the `## Cross-roadmap dependencies` section (common.md §6.12) in this same batch — it's optional schema but high-value for sub-roadmap readers. Default: include the section; user can decline.

**Forbidden alternative paths.** Never create child roadmap.md via `write_doc` / `write_code` / direct `Write`. Single-executor pattern: every roadmap.md at every depth routes through this command (enforced by [`architecture_brainstorm/SKILL.md`](../skills/architecture_brainstorm/SKILL.md) Step 6 *Roadmap.md is NOT saved here* guardrail + symmetric [`idea_brainstorm/SKILL.md`](../skills/idea_brainstorm/SKILL.md) Step 5 guardrail). Step 6 of `architecture_brainstorm` saves the design doc only; this command creates the sub-roadmap.

### Step 2 — Resolve proposed edits from inputs

Translate inputs into concrete edit operations on the Parts table.

**From an idea brainstorm** — each cluster's Per-Cluster Routing maps to a Part:

| Cluster Routing | Resulting Part State |
|---|---|
| `→ /architecture_brainstorm` | `arch-pending` |
| `→ /idea_brainstorm rerun` | `idea-rework` (existing Part) or `idea-pending` (new sub-topic) |
| `→ workshop` | `workshop-pending` |

Cluster Open Questions / Findings populate the Trigger and Source fields. Pos is `—` (un-sequenced) unless the cluster's Routing timing modifier names a position (e.g., `(now — dependency root)` → propose Pos 1).

**From an arch brainstorm** — each Part the design authored maps to a Parts table row:

| Arch-brainstorm output | Resulting Part State |
|---|---|
| Part passing the readiness gate (per arch SKILL Step 5) | `plan-pending` |
| Part with open arch design questions (architectural fork) | `arch-pending` |
| Part needing creative ideation (creative fork) | `idea-pending` |
| Part awaiting user decision | `workshop-pending` |
| Part marked user-owned (per arch Step 5 user-owned ask) | `user-owned` |

Each Part inherits Deps from the design doc's enumerated dependencies and gets a Source link to the design section.

**Existing Part decomposed via child-subfolder spawn** (per arch SKILL Step 8 spawn-placement extension + [common.md §5.1](../skills/_brainstorm_shared/common.md)) — transitions an existing Part to `submap-pending`. Distinct from the table above: this is a transition on a pre-existing Part, NOT a new Part. The Part's name, Deps, Source, and dependent edges are preserved; only State + Trigger change. Trigger format: `"Decomposed into <child-folder>/roadmap.md (N child Parts)."` per common.md §6.10. The child sub-roadmap is also added to the parent's `Spawned sub-brainstorms` section in the same batch.

**Re-authoring paths** (for `/update_roadmap split` outcomes + re-decomposition triggered by arch Step 5 hard-stop remediation):

| Pre-existing State + remediation | Children's State |
|---|---|
| `plan-pending` Part split with known lines | Each child gated per arch Step 5; typically `plan-pending` |
| Part kicked to `arch-rework` with split charter | Children start `plan-pending` after the arch-rework session lands |

**Standalone (user-driven):** the named transition applies directly.

### Step 3 — Validate the edited Parts table

Before rendering, check for structural issues:

- [ ] Every Deps entry resolves to an existing Part name (or a cross-folder `path#part-name`).
- [ ] Every `Pos` value is either an integer or `—`. Integers form a contiguous range (`1, 2, 2, 3, 4`) — gaps signal an authoring mistake.
- [ ] No Part has State=`complete` with un-completed Deps (signal of a state-mismatch — flag for user review).
- [ ] No backward `*-pending` transitions — `plan-pending` → `arch-pending` (not `arch-rework`), or `arch-pending` → `idea-pending` (not `idea-rework`). Emits `⚠ Backward to *-pending — did you mean *-rework? *-pending means 'never had this phase'; *-rework means 'had this phase, redo'.`
- [ ] No compound-`+` Part names — soft warn. Emits `⚠ Compound name — split-candidate signal. Verify this is one cohesive unit, not two stapled together.` (non-blocking).
- [ ] **Trigger content** per [`common.md §6.10`](../skills/_brainstorm_shared/common.md) — every Part with State ∈ {`*-pending`, `*-rework`, `user-owned`} carries a Trigger whose content shape conforms to §6.10's sub-table. Per-shape soft-warns (all non-blocking):
  - `arch-pending` / `idea-pending`: `⚠ Trigger missing required field per common.md §6.10 — must include inventory + source pointer + out-of-scope.`
  - `workshop-pending`: `⚠ workshop-pending requires a Trigger naming the user decision + source pointer (per common.md §6.10).`
  - `idea-rework` / `arch-rework`: `⚠ *-rework requires a Trigger explaining what changed since the prior phase (per common.md §6.10).`
  - `submap-pending`: `⚠ submap-pending requires a Trigger naming the child sub-roadmap path (per common.md §6.10). Canonical format: "Decomposed into <child-folder>/roadmap.md (N child Parts)."`
  - `user-owned`: `⚠ user-owned requires a Trigger naming the user deliverable (per common.md §6.10).`
- [ ] **Sibling-arch-pending batch** — if this batch adds 2+ NEW Parts at State=`arch-pending`, emit `⚠ 2+ arch-pending Parts in one batch — apply Arch-pending consolidation litmus (architecture_brainstorm SKILL.md Step 5): do their open questions roll up to one session? If yes, consolidate into one Part with per-fork inventory in its Trigger.` (non-blocking).
- [ ] **Intra-batch speculative dep** — if a new Part's Deps include another NEW Part in this same batch with State ∈ {`arch-pending`, `idea-pending`, `workshop-pending`}, emit `⚠ This Part depends on a *-pending sibling being added in the same batch — apply Downstream-of-fork guard (architecture_brainstorm SKILL.md Step 5). If this Part's scope would change based on the upstream session's decision, remove it and push inventory into the upstream Trigger instead.` (non-blocking).
- [ ] **MVP Required-Parts wikilink coherence** — if a `## MVP Checkpoints` section exists, every Required-Parts checkbox line (intra-roadmap `[[#Parts|<Part Name>]]` or cross-roadmap `(parent) <Part Name>` / `[[../<folder>/roadmap|<folder>]] § "<Part Name>"`) must resolve **by Part name** to a current Parts table row (resolve cross-roadmap deps lazily per §6.8). Stale references (Part renamed/retired/split without MVP-section follow-through, or hand-edits that drifted) emit `⚠ MVP-N Required Parts references missing Part <name> — likely a rename/split/retire that bypassed MVP-section coordination. Run /mvp_plan refine MVP-N to repoint.` (non-blocking; `rename` op auto-rewrites so this fires only when rewriting was bypassed or after split/retire/hand-edit). This same name→row resolution feeds the Step 5 check-mark recompute.
- [ ] **Source-column wikilink anchor coherence** — for every `[[<doc>#<anchor>\|...]]` in the Source column, resolve the target doc (lazy — only when the target is local or reachable), then verify `<anchor>` matches a literal heading line in that doc. Obsidian resolves anchors by exact heading-text match — no kebab-case slugging, no whitespace normalization. Soft-warn `⚠ Source wikilink anchor not found in <doc>: <anchor>. Obsidian requires VERBATIM heading text — preserve the "Section N — " prefix and the " — " em-dash if present in the heading. Open the target doc, copy the heading line, paste verbatim.` Misses are the silent fall-through-to-file-top failure mode that this validator exists to catch (per common.md §6.2 verbatim rule). Non-blocking. Skip the check when the target doc is non-local (cross-repo, external URL).
- [ ] **Deps-column cross-folder Part-dep shape** — for every Deps cell containing `[[<other>/roadmap#<part>...]]`, soft-warn `⚠ Cross-folder Part-dep wikilink #<part> cannot resolve — Parts live in table rows, not headings. Use the canonical shape per common.md §6.8: '(parent) <Part Name>' (short form, default) OR '[[../<folder>/roadmap\|<folder>]] § "<Part Name>"' (with display annotation when a clickable file link is wanted). The '#<part-name>' anchor will always fall through to file-top.` Non-blocking. The same shape rule applies to Source cells that reference a Part on another roadmap (rather than a section on a design doc).
- [ ] **Sub-roadmap shape change → parent staleness** — if the target roadmap has `parent-roadmap` frontmatter (it's a sub-roadmap) AND this batch changes the Part *count* (add / split / remove) or *Pos numbering*, emit `⚠ Sub-roadmap shape changed — the parent roadmap's submap reference (bare "(N child Parts)" count) + any MVP Pos-references to this sub-roadmap may now be stale. Run /roadmap_audit submap (and /roadmap_audit mvp) to verify; fix the parent via /update_roadmap (count) + /mvp_plan refine (MVP Pos-refs).` Non-blocking, and **does NOT auto-edit the parent** — eager cross-roadmap mirroring is rejected by common.md §6.8 (lazy-resolution). This is a nudge only; the precise check + fix route lives in `/roadmap_audit` + the parent's own executors. Pure state transitions (no count/Pos change) don't fire it — they're already covered by §6.10's no-denormalize-child-state rule (the parent carries no child state to go stale).

If any validator fails, surface the issue in the diff preview with a `⚠` marker; user confirms whether to apply anyway or revise.

**Fix-recipes for common `⚠` markers** (shown alongside the warning):

| Warning | Fix-recipe |
|---|---|
| Any `Trigger ... per common.md §6.10` warning | Look up the State's required Trigger content shape in [common.md §6.10](../skills/_brainstorm_shared/common.md) and supply it. Examples: `*-rework` → "spec revealed scope creep" / "Plan Mode hard-stop"; `workshop-pending` → the user-decision question + source link; `user-owned` → a concrete user deliverable like "design 5 floor scenes". |
| `complete with un-completed deps` | Check whether deps actually completed, or whether this Part was prematurely marked complete. |
| `Compound name — split-candidate signal` | Recheck whether the Part is one cohesive Plan Mode session or two stapled together; if the latter, split via `/update_roadmap split <part> into <a>, <b>`. |
| `Backward to *-pending` | Confirm intent: was the Part previously at the destination phase (correct: use `*-rework`) or genuinely never authored at this phase (rare; usually a typo)? |
| `MVP-N Required Parts references missing Part` | Run `/mvp_plan refine MVP-N` to repoint to the current Part name (or remove the reference if the underlying Part was retired). For splits, decide per-MVP which child Part(s) should replace the original reference. |
| `Source wikilink anchor not found in <doc>` | Open the target doc, copy the heading line VERBATIM (including the `Section N — ` prefix for top-level `## Section N — Title` headings and the ` — ` em-dash inside `### N.M — Title` sub-headings). Paste into the wikilink: `[[<doc>#<exact-heading>\|<short display>]]`. Escape the alias pipe as `\|` inside the table cell. If the Part's commitment legitimately spans 2+ design-doc sections, emit 2+ wikilinks separated by ` + ` — never fabricate a joined `#<a> and <b>` anchor that doesn't exist as a real heading. |
| `Cross-folder Part-dep wikilink cannot resolve` | Rewrite to the canonical shape per common.md §6.8: prefer `(parent) <Part Name>` in Deps cells (short, scans well, integrates with Mermaid external-node rendering); use `[[../<folder>/roadmap\|<folder>]] § "<Part Name>"` when a clickable file link adds value in a Source cell. Parts are table rows, never headings — `#<part-name>` anchors always fall through to file-top. |
| `Sub-roadmap shape changed — parent may be stale` | After this batch lands, run `/roadmap_audit submap` (count) + `/roadmap_audit mvp` (Pos-refs). Fix the parent's bare `(N child Parts)` count via `/update_roadmap` on the parent; fix MVP Pos-references via `/mvp_plan refine MVP-N`. Do NOT denormalize the per-state breakdown back into the parent (common.md §6.10) — only the bare count is permitted to live there. |

### Step 4 — Render Mermaid diagram

Generate deterministically from the Parts table:

- **Node ID:** Part name slugified (`Foundation+Refactor` → `Foundation_Refactor`).
- **Node label:** Part name **verbatim, NO `Pos N. ` prefix.** Mermaid's CommonMark parser interprets leading `N. ` (also `- `, `* `, `# `, `> `) as markdown list/heading syntax and emits "Unsupported markdown: list" warnings — one per offending node. Pos is encoded by graph rank, not in the label. If the renderer must surface Pos in-label, use `N: Name` / `[N] Name` / `Pos N — Name` (em-dash-separated) — never `N. Name`. Full constraint: [`mermaid_diagrams` skill](../skills/mermaid_diagrams/SKILL.md) → *Renderer constraints* + [`common.md §6.4`](../skills/_brainstorm_shared/common.md) → *Node label rule*.
- **Class:** one of `plan | arch | idea | rework | workshop | complete | abandoned | user | submap` (rework states map to `rework` class which is dashed-stroke; `user-owned` maps to `user`; `submap-pending` maps to `submap` which is long-dashed teal).
- **Edges:** `<dep> --> <part>` for each Deps entry where `<dep>` is a local Part.
- **Cross-folder edges:** dashed line with the cross-folder path as edge label.

Replace the existing Mermaid block in roadmap.md (between the standard `## Current state — Mermaid` heading and the next `##`).

Palette, classDef discipline, and renderer constraints: see the `mermaid_diagrams` skill. The classDef block in `common.md §6.4` already encodes the canonical roadmap mapping — emit it verbatim.

### Step 5 — Compute derived views

Three computed sections rendered below Mermaid:

**Currently ready to execute** — Parts where State ∈ {`idea-pending`, `arch-pending`, `plan-pending`, `idea-rework`, `arch-rework`} AND every Dep has State=`complete` (resolve cross-folder deps lazily per §6.8).

**Blocked / awaiting deps** — Parts where State ∈ {`idea-pending`, `arch-pending`, `plan-pending`, `idea-rework`, `arch-rework`} having ANY Dep with State ≠ `complete`. Show the blocking dep and its current State.

**Ready for you (user-owned)** — Parts where State=`user-owned` AND every Dep has State=`complete`. Bullet list with Trigger (the user deliverable) displayed.

Replace existing derived-view sections in roadmap.md.

**MVP Checkpoints recompute (in-place; only when a `## MVP Checkpoints` section exists)** — UNLIKE the three views above, this is NOT a wholesale section replacement: the narrative fields are `/mvp_plan`'s and must survive byte-for-byte. Mutate exactly two things per MVP, leaving Goal / Validates / Acceptance / Excluded / Playtest untouched:

1. **Required-Part check-marks** — for each `- [ ]`/`- [x]` line under **Required Parts**, resolve its Part reference by name (same resolution as the Step 3 coherence validator) and set the mark to `[x]` iff that Part's State is `complete`, else `[ ]`. Recompute every run, both directions — never additive. Cross-roadmap Parts (`(parent) <Part>` or `[[../<folder>/roadmap|...]]`) resolve lazily per §6.8; when the target isn't locally reachable, leave that line's mark as-authored and emit `⚠ MVP-N: cross-roadmap Part <name> unresolvable — mark left as-authored.`
2. **Status line** — compute from the Required-Part completion count (X complete of Y):
   - `X < Y` → `🔨 In progress (X/Y parts)`
   - `X = Y` AND current Status is not `✅ Verified` → `🧪 Ready for playtest`
   - current Status is `✅ Verified` AND `X = Y` → preserve `✅ Verified`
   - current Status is `✅ Verified` AND `X < Y` → **downgrade** to the computed `🔨 In progress (X/Y parts)` and record a regression flag for Step 7: `⚠ MVP-N was Verified but Part <name> regressed out of complete — downgraded.`

### Step 6 — Compose revision log entry

**One line per transition — one statement, ≤25 words. A hard cap, not a soft target.** A second sentence, or a third `;`-joined clause, means you're writing a postmortem — not the *navigational audit trail* this is. Record WHAT transitioned + the single WHY a reader needs to reconstruct current state; everything else lives where it's authoritative — git, commit message, plan/arch doc, Parts table, derived views.

**Keep (load-bearing):** the transition itself (Part + state change, or rename/split/spawn/retire) plus AT MOST ONE qualifier — a commit ref OR a ≤6-word why, never both, never a what-shipped manifest. Cross-roadmap/text-only edits: the Part + the one value that changed.

**Cut (derivable elsewhere — never in the log):** test/gate counts (`Logic X / Integration Y`), file-by-file shipped manifests, plan divergences/deviations, `session_audit` findings, auto-memory pins, "Mermaid reclassed `:::complete`" (mechanical — always happens), unblock-cascade narration ("now Currently-ready" — the derived views already show readiness), multi-sentence design rationale (belongs in the arch doc).

**Format:** `- YYYY-MM-DD — <Part>: <old-state> → <new-state> (<commit-ref OR ≤6-word why>).` — the parenthetical is ONE slot; pick the ref or the why, not both.

**Idiomatic forms:**
- `- 2026-05-13 — Initial roadmap from arch.md. 7 Parts sequenced (Pos 1–7), 12 un-sequenced.`
- `- 2026-05-15 — <Part>: <old-state> → <new-state> (<reason>).`
- `- 2026-05-15 — <Part>: plan-pending → complete (<commit-ref>).`
- `- 2026-05-15 — Spawned `<child-doc>` from Part <name> (<same-folder | child-subfolder | sibling-folder> per §5.1).`
- `- 2026-05-15 — Renamed Part `<old>` → `<new>` (N Deps + M MVP wikilinks rewritten).`
- `- 2026-05-15 — Split Part `<old>` into `<new-1>`, `<new-2>`. Deps redistributed.`

Append to the `## Revision Log` section.

### Step 7 — Present batch diff

Show the user the proposed diff covering:
1. Frontmatter changes (only `last_revised` typically)
2. Parts table updates (additions, transitions, splits, renames)
3. Mermaid block regeneration
4. Currently-Ready / Blocked view refresh
5. Spawned sub-brainstorms section updates (if new spawn this session)
6. MVP recompute (if a `## MVP Checkpoints` section exists): changed check-marks + Status transitions. **Surface a playtest prompt** for any MVP that flipped to `🧪 Ready for playtest` this run — name it, and name any remaining `user-owned` Required Part the user must finish first (e.g. *"MVP-2 ready for playtest, except user-owned Part: Room scene art"*). Surface any `✅ Verified`→downgrade regression flag.
7. New Revision Log line(s)
8. Validator warnings from Step 3 (if any)

**Single approval applies all.** User can redirect ("revise Mermaid", "split this transition into two log lines", "skip the spawn entry — it's the same folder") — adjust diff, re-present.

### Step 8 — Apply

If the batch diff (Step 7) is empty — proposed edits reduce to no changes — report *"No roadmap changes needed"* and exit without prompting for approval.

Otherwise: `Edit` (or `Write` if creating) `roadmap.md` with the approved diff. Bump `last_revised: YYYY-MM-DD` in frontmatter to today.

---

## Standalone operations (user-driven)

| Command | Effect |
|---|---|
| `/update_roadmap mark complete <part>` | State → `complete`; revision log entry. |
| `/update_roadmap retire <part>` | State → `abandoned` with user-supplied reason in revision log. |
| `/update_roadmap rename <old> <new>` | Rewrite Part cell + every Deps cell + every `## MVP Checkpoints` Required-Parts wikilink referencing `<old>`. Revision log records count (Deps + MVP wikilinks separately). |
| `/update_roadmap split <part> into <new-1>, <new-2>` | Replace Part with N new Parts. Prompt for Pos values + Deps redistribution. **If any `## MVP Checkpoints` Required-Parts line references the split Part, prompt per-MVP for which child(ren) inherit the reference** and rewrite in the same batch — 1→N is ambiguous so it can't auto-rewrite the way `rename` does, but it must not silently orphan the MVP ref (the Step 3 coherence validator is only the reactive backstop). |
| `/update_roadmap transition <part> to <state>` | Direct State change. Prompt for reason → revision log. |
| `/update_roadmap promote <part> to <pos>` | Move from un-sequenced to sequenced at given Pos. |
| `/update_roadmap demote <part>` | Move from sequenced to un-sequenced (Pos → `—`). |

All standalone ops route through Steps 3-8 (validate, regen, present diff, apply).

### Standalone error handling

| Error condition | Behavior |
|---|---|
| Verb not recognized (e.g., `/update_roadmap whatevs Foo`) | List the valid verbs from the operations table above; exit without edits. |
| `<part-name>` doesn't resolve in the target roadmap | List candidate Parts; exit without edits. Don't auto-propose creating a new Part — Part creation routes through brainstorm-skill invocation. |
| `transition <part> to <state>` with invalid `<state>` | List the 10 valid States from [common.md §6.3](../skills/_brainstorm_shared/common.md); exit without edits. |
| `rename <old> <new>` where `<new>` collides with an existing Part name | Refuse — silent rename would corrupt Deps references on the collision target. Exit with the conflict named. User resolves by picking a non-colliding name or `retire`-ing the existing Part first. |
| `split <part> into <new-1>` (only one child supplied) | Refuse — split produces 2+ children by definition. (Use `rename` for 1→1.) Exit. |
| `split <part> into ...` where any new name collides with an existing Part | Refuse — same name-collision rationale as `rename`. Exit with the conflict named. |
| Target roadmap doesn't exist (no `roadmap.md` in CWD or any ancestor) | Per Step 1, propose creating one from `common.md §6` schema (user confirmation required) before applying the standalone op. |

---

## MCP-offline policy

Non-event — `roadmap.md` is a vault file edited with native `Read`/`Edit`/`Write`. Obsidian MCP being offline does not block `/update_roadmap`. See `_brainstorm_shared/common.md §3`.

---

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Skip the revision log this time — nothing important changed" | Revision log is the audit trail. Every State transition deserves a line; the reader uses it to reconstruct WHY the roadmap is in its current state. |
| "Don't regen Mermaid; the old one is close enough" | Mermaid drift is silent and accumulates. Always regen — costs are negligible. |
| "Skip cross-folder Currently-Ready resolution; this folder is local" | The lazy-resolution policy exists for exactly this — run it. If no cross-folder deps exist, it costs nothing. |
| "Apply edits without showing the diff — user trusts the command" | Batch mode = single approval, NOT zero approval. The diff is the user's checkpoint against silent drift. |
| "Auto-promote freshly-arch'd Parts to Pos values inferred from Deps" | Pos assignment is a user judgment call about priority/parallelism. Default to un-sequenced (`—`) on new Parts; user explicitly promotes via `promote` op or by editing during the batch-diff review. |
| "Combine multiple Part transitions into one revision log line" | One transition = one line. Aggregating loses the timestamped audit trail. Exception: a single brainstorm session legitimately produces N transitions; those can share a date but each gets its own line. |
| "This completion was a big effort — the log should capture all of it (tests shipped, files touched, audit findings, deviations)" | The log is a navigational trail, not a postmortem. Cap each entry at one ≤25-word statement (Step 6): the transition + one qualifier (commit ref OR ≤6-word why, not both). Ship-detail is derivable from git, the commit message, and the plan/arch doc — duplicating it here is the exact bloat that makes logs eat half the doc. |
| "Demote `plan-pending` → `arch-pending` to signal 'this needs more design'" | `arch-pending` means *"never had arch brainstorm"*. If arch happened (even thinly), the honest move is `arch-rework`. The distinction is load-bearing: `*-rework` maps to dashed-stroke Mermaid class, signaling "came back" history; `*-pending` renders as solid, erasing it. |

---

## Cross-references

- [`_brainstorm_shared/common.md §6`](../skills/_brainstorm_shared/common.md) — roadmap.md schema (source of truth). §6.10 is canonical for Step 3 Trigger validators; §6.4 + §6.5 specify the heading anchors Step 4 / Step 5 replace; §6.8 governs cross-folder dep resolution.
- [`_brainstorm_shared/common.md §5.1`](../skills/_brainstorm_shared/common.md) — spawn-placement convention (informs new-folder vs same-folder decisions in Step 7's Spawned sub-brainstorms updates)
- [`_brainstorm_shared/common.md §3`](../skills/_brainstorm_shared/common.md) — MCP-offline policy
- [`_brainstorm_shared/common.md §1.2`](../skills/_brainstorm_shared/common.md) — stale-roadmap remediation (relevant when invoked against a roadmap whose Parts predate the calling skill's current state — the caller re-runs arch Step 5 before handing the Part list here)
- [`architecture_brainstorm/SKILL.md`](../skills/architecture_brainstorm/SKILL.md) — invokes this command as its final step
- [`idea_brainstorm/SKILL.md`](../skills/idea_brainstorm/SKILL.md) — invokes this command as its final step
- `obsidian_conventions` skill — Obsidian wikilink/anchor conventions for `Source`-column links
