---
description: Brief a plan-pending Part before drafting; /plan_check audits drafts
---

# /plan_part

Prepares an in-context briefing for the agent drafting a plan for a `plan-pending` Part. Loads the design-doc surface verbatim (API + Test Pin block, Integration touch points, Source-cell section), cross-references against current codebase state, and classifies any divergence as **macro** (→ kick to `arch-rework`) or **micro** (→ resolved during plan drafting).

**Companion to `/plan_check`:** this command is *pre-draft* (briefs the agent before plan composition); `/plan_check` is *post-draft* (audits a written plan). Together they bracket the drafting step.

**Contract:** **read-only** — surfaces a structured briefing into the assistant message, which the drafting agent consumes as front-loaded context. Run it BEFORE drafting starts so a macro-drift kick-back costs no drafted plan. The briefing is **front-loaded context for the drafting workflow**, NOT a replacement for it — see `reference/plan_part_composition.md` §Workflow Composition. No file writes, no side effects.

---

## When to invoke

User-typed when scoping to a `plan-pending` roadmap Part.

**Timing — before drafting.** Run `/plan_part <part>` first, review the briefing, THEN proceed into the drafting workflow if the drift verdict is clean (or micro-only). Macro drift kicks back to `arch-rework` with no drafted plan wasted — the structural win. The briefing emits into the active session; drafting continues with the standard workflow (`reference/plan_part_composition.md` §Workflow Composition).

Auto-trigger hooks should NOT fire this — it's an explicit user action that scopes the session.

Required preconditions (HARD-STOP if any fail):
- Named Part exists on the resolved roadmap.
- Part State is `plan-pending`. Other States redirect:

| State | Redirect |
|---|---|
| `arch-pending` / `arch-rework` | `/architecture_brainstorm` |
| `idea-pending` / `idea-rework` | `/idea_brainstorm` |
| `workshop-pending` | User decision, then re-route |
| `prototype-pending` | Run the prototype skill, where installed, on the Trigger's question — `QUESTION.md`, branch, variants, verdict; the answer banks in `decisions.md` `## Decided`, then re-route via `/update_roadmap` |
| `user-owned` | Not an agent-executable Part; surface the user deliverable from Trigger |
| `submap-pending` | Redirect to child sub-roadmap; pick a Part there |
| `complete` / `abandoned` | Refuse — no plan needed |

## When to skip

- **`/plan_check` already ran successfully on a drafted plan for this Part** — drift already surfaced inline at draft time.
- **User explicitly wants to redesign** — that's an `arch-rework` transition, not a plan session.

Otherwise the Part's triage decides whether grounding is admitted (`_brainstorm_shared/execution_depth.md` §Admission table).

---

## Arguments

- `/plan_part <part-name>` — target roadmap = nearest ancestor `roadmap.md` from CWD (same convention as `/update_roadmap`).
- `/plan_part <part-name> <roadmap-path>` — explicit roadmap path when CWD ambiguity exists or briefing a Part from another folder.
- `/plan_part` (no args) — print usage + the *When to invoke* table; exit without briefing.

**Part-name resolution:** exact match on the `Part` column of the resolved roadmap's Parts table. If `<part-name>` doesn't resolve, list candidate Part names and exit. Don't fuzzy-match — Part names are stable identifiers (per `/update_roadmap rename` discipline).

---

## Procedure

Five phases, run in order. Before skipping or softening any phase, read [`reference/plan_part_composition.md`](reference/plan_part_composition.md) §Anti-patterns.

**Warm chain.** When the `/design_drive` session that recorded the grounding base runs this Part, Phase 3 checks only the delta. Read the base from that drive's task record: `task_record.py show <drive task>`, the decision whose text starts `design-lock base`. List the changed files, working tree included: `git diff --name-only --no-renames <base>` plus `git ls-files --others --exclude-standard`. Phase 2 still loads the Source-cell sections verbatim. Phase 3 verifies only the claims that name a changed file or a type declared in one. Phase 4 rules on that subset, and a subset with no changed file is clean. Any other session, or a chain with no recorded base, runs every phase in full.

### Phase 1 — Resolve the Part

1. Locate target `roadmap.md` (CWD ancestor or explicit arg).
2. Parse Parts table; find row matching `<part-name>` exactly.
3. Verify State is `plan-pending`. If not → emit the redirect from the *When to invoke* table and HARD-STOP.
4. Verify every Dep on this Part has State `complete`. If any incomplete Dep → emit `⚠ Part is blocked by unfinished deps: <list>. Plan can proceed but the impl will block on these.` (non-blocking — the plan may legitimately be drafted against not-yet-shipped deps if the user is sequencing work).
5. Extract from the Part row: `Source` wikilink(s), `Deps`, `Pos`.

### Phase 2 — Load design surface verbatim

**Single bundled call** (per CLAUDE.md §9 — synthesis-shaped multi-file read):

```
mcp__ai-worker__read_files(
  paths=[
    <each Source-cell wikilink resolved to a file path + anchor>,
    <design doc's "## Open Questions" section, if present>,
  ],
  question="Extract verbatim: (a) the Source-anchored section text, (b) the API + Test Pin
            block for Part '<part-name>' (named files, class signatures with fields/methods,
            [TestCase] method names), (c) the Integration touch points enumeration listing
            cross-subsystem call sites, (d) any Out-of-scope items declared for this Part,
            (e) any '## Open Questions' items OR design-surface notes flagged stale /
            deprecated / dead-code / debt-marked / 'audit for removal' that bear on THIS
            Part's scope (these are the most-likely-to-silently-drop items — surface them all)."
)
```

**Anchor-resolution rule:** Source-cell anchors are VERBATIM heading text (per common.md §6.2). If the anchor doesn't resolve in the target doc → that's **macro drift** (the design's section was renamed or moved without rewriting the Source link). Classify per Phase 3.

**Multi-anchor Sources:** if `Source` contains 2+ wikilinks separated by ` + ` (per common.md §6.2), read all referenced sections — the Part legitimately spans them.

### Phase 3 — Cross-reference against codebase

For every concrete claim in the loaded design surface, verify against current code. **Use the right tool per claim shape** (per CLAUDE.md §9):

| Design claim | Verification |
|---|---|
| `extends <Base>` / `implements <Iface>` | LSP `findReferences` on `<Base>`/`<Iface>` → confirm class exists AND has 2+ subclasses/impls if design called it a family extension |
| New file path `Foo/Bar/Baz.cs` | `Glob("Foo/Bar/Baz.cs")` → confirm no existing file at that path |
| Named existing file `Foo/Bar/Existing.cs` | `Glob` → confirm still exists at named path |
| Named method `ExistingClass.MethodName(...)` | LSP `documentSymbol` on the file → confirm method exists with assumed signature |
| **`<Type>.Instance.<Member>(...)` autoload call** | Enumerate EVERY `*.Instance` call in design pseudocode (don't skip any). For each `<Type>`: `Grep` `project.godot` `[autoload]` block for `<Type>=`. **If absent, rule out the scene-nested-singleton pattern BEFORE declaring drift** — a singleton's `.Instance` is frequently set by a node *inside* an autoload scene (e.g. `InputProfileDatabase` is a child of `global.tscn`, itself the `Global` autoload), so it never appears as a top-level `[autoload]` line yet is populated at boot. Two checks: (a) grep the autoload `.tscn` scenes (esp. `global.tscn`) for a node instancing `<Type>`, AND (b) grep for `Instance = this` in `<Type>`'s `_EnterTree`/`_Ready` and confirm that node is reachable from an autoload. Only if BOTH miss is it genuine autoload-missing drift (executor hits `JmoLogger.Error` at runtime = test failure) → resolve at plan-time per `feedback_resolve_questions_in_plan_not_execution`: add `<Type>` to `[autoload]` (publisher-before-consumer order per `gotcha_autoload_to_autoload_subscription_order`) OR pivot to a non-autoload access pattern. Do NOT defer to write-time as a "verify at code time" caveat, and do NOT add a redundant top-level autoload for a type already wired via a scene child. |
| Integration touch point in subsystem `<X>` | Read `project_subsystems` registry entry for `<X>` → confirm subsystem still exists and the named seam is reachable |
| New `BBDataSig.<Key>` | `Grep("public static readonly StringName <Key>")` on Jmodot's BBDataSig file → confirm key doesn't already exist (collision) AND that the namespace expects it |
| Test-suite path `Tests/Logic/<X>Test.cs` | `Glob` → confirm test folder exists (creation of the file itself is plan-session work) |

**Drift classification — three tiers:**

| Macro drift (→ arch-rework, HARD-STOP) | Micro drift (→ surface as briefing item for the drafting agent to resolve) | Glossary note (→ briefing footnote, NOT drift) |
|---|---|---|
| Named base class / interface no longer exists | Named file moved to a sibling folder; type intact | Informal naming in design pseudocode where the actual codebase entity differs in name but not shape (e.g., "Player.tscn" → real entity is `[Export] PackedScene _playerPrefab`) |
| Extended-family inventory collapsed below 2 subclasses (extend-not-parallel premise broken) | Named method gained/lost a parameter; intent preserved | Sibling-pattern reference where the design says "analogous to X" but X doesn't share the same contract (shape analogy only, not contract conformity) |
| New-file path collides with non-trivial existing content (parallel work happened) | Test fixture base class was renamed but extension surface is the same | Design-doc shorthand for a multi-step process the codebase formalizes via discrete API (e.g., "LoadAsync" → `RequestLoad` / `GetLoadStatus` / `CompleteLoad`) |
| Named subsystem in Integration touch points was removed / merged into another | Adjacent file added/removed in a touch-point subsystem (additive context) | Design-pseudocode method names that the executor will translate to canonical API at write-time |
| Base class became `sealed`, or `abstract` → concrete, or constructor reshape that invalidates the extension | New `BBDataSig` key exists adjacent to the planned one (additive) | |
| Jmodot ↔ {{PROJECT_NAME}} boundary the design assumed was re-drawn | Comment / whitespace / formatting drift in linked design-doc section | |
| Source-cell wikilink anchor no longer resolves in the design doc | An extra subclass exists in the family beyond the design's enumeration | |

**Rule of thumb (litmus when in doubt):**
- *"Could the drafting agent address this in plan-drafting without changing the design's load-bearing claims?"* Yes → micro. No → macro.
- *"Is the divergence purely a naming/shorthand issue with the executor writing the canonical name at code-time?"* Yes → glossary note, NOT drift. Glossary notes go in the briefing's footer ("Design-doc glossary translations") rather than the drift count, to avoid diluting the actionable micro-drift signal.

**Drift count discipline:** the briefing's `Drift: macro=N | micro=M` header counts ONLY drift items, not glossary notes. A briefing with 4 micro + 2 glossary reads as `macro=0 | micro=4`. Glossary notes are listed below the verification block under a `─── Design-Doc Glossary ───` section so the drafting agent has the translations without inflating the action count.

**Scope-inflation advisory (distinct from drift).** While running the verification above, capture cheap size proxies for any named *modify* / *migrate* / *refactor* target — reuse the SAME `findReferences` / `documentSymbol` / `Glob` calls already issued; do NOT dispatch agents or widen the search (open-ended blast-radius discovery belongs to [`/explore`](explore.md), not this command). Two proxies: (a) `findReferences` count + distinct-file count for a type the Part migrates/refactors; (b) current LOC for a file the design treats as a light edit. When a proxy materially exceeds what the design's prose implies (e.g. a "migrate existing X" one-liner against a type with 20+ call sites across 6+ files), record a **scope signal**. This catches *post-authoring scope inflation* — a Part bounded at arch Step 5 that grew via later code drift — which neither arch Step 5 (too early) nor the plan's file-list gate (catches *unbounded*, not *bounded-but-large*) covers. The signal is **advisory, NOT drift**: it does not gate, does not kick to `arch-rework`, and is NOT counted in the `macro=N | micro=M` header. The proxy count is evidence for a split *recommendation* the user weighs — never a verdict (a 30-reference type may be a mechanical rename; a 3-reference one a deep rework). The authoritative bound check remains the plan's file-list bound gate (`/part_drive` valve (b)).

### Phase 4 — Drift verdict

**Any macro drift → HARD-STOP**:

1. Surface the macro findings with file paths / line numbers.
2. Propose the kick-back: `/update_roadmap transition <part-name> to arch-rework` with a Trigger naming the drifted assumptions (e.g., `"Base class <X> retired since design — extension path invalidated; re-evaluate inheritance vs composition."`).
3. Exit without emitting the briefing. The user runs the transition; later, a fresh `/architecture_brainstorm` session against the now-`arch-rework` Part re-resolves the design.

**Zero macro drift → proceed to Phase 5**. Micro drift carries forward as briefing content for the agent to resolve.

### Phase 5 — Emit briefing

Surface a structured block in the assistant message. The drafting agent consumes it as self-briefing before composing the plan.

```
═══════════════════════════════════════════════════════════════
PLAN BRIEFING — <Part Name>
═══════════════════════════════════════════════════════════════
Roadmap:      <roadmap.md relative path>
Pos:          <N> | Deps: <comma list, all complete unless flagged>
State:        plan-pending
Drift:        macro=0 | micro=<count>
Scope:        <clean | <N> advisory signal(s) — see Scope Signals below>

─── Design Surface (verbatim) ───
<verbatim section text from Source-cell anchor(s)>

─── API + Test Pin (verbatim from design doc) ───
<class signatures for all new types; [TestCase] method names>

─── Integration Touch Points ───
<bulleted <subsystem-id>: <description> list — ≤2 boundaries per arch Step 5>

─── Codebase Verification ───
✓ <claim>: <verification result>
✓ <claim>: <verification result>
⚠ MICRO: <drift description> — <where surfaced> — <suggested resolution in plan>
⚠ MICRO: <drift description> — <where surfaced> — <suggested resolution in plan>

─── Scope Signals (advisory — NOT drift, does NOT gate) ───
<For each modify/migrate target inflated past its design-implied size: the proxy evidence
 (findReferences count + distinct-file count, or current LOC) vs. the design's implied size,
 then a split recommendation with the concrete command, e.g.:
 ⚠ SCOPE: <Part> migrates <Type> — findReferences = N call sites across M files vs. the
   design's "<one-line claim>". Exceeds the bounded estimate; recommend split before drafting:
   /update_roadmap split <Part> into <a>, <b>.
 The briefing still emits and drafting still proceeds — this is a recommendation, not a stop.
 Omit this section entirely if no signals.>

─── Out of Scope This Session (from design) ───
<bullet list of explicit exclusions>

─── Unresolved Scope Notes (resolve or consciously defer BEFORE planning) ───
<Open-Questions items + stale/deprecated/dead-code/debt/audit-for-removal notes from
 (e) above that bear on this Part. Each: the note + a resolve-or-defer prompt. The design
 parking a scope decision here does NOT make it resolved — the drafting agent either resolves
 it (commit the scope into the plan) or the user consciously defers it. An unsurfaced note here
 is the canonical "shipped a half-retired surface" failure. Omit this section if empty.>

─── Design-Doc Glossary (informational; NOT drift) ───
<bullet list of name/shorthand translations — design term → canonical codebase entity.
 Omit this section entirely if zero glossary notes were collected.>

─── Gates & Contract (respect these; rest is the drafting agent's call) ───
- File-list-bounded gate (per arch_brainstorm Step 5 handoff requirement) —
  the plan's enumerated file list MUST be bounded; "...and possibly others"
  is the HARD-STOP signal to kick back to arch-rework.
- Design is approved — DO NOT redesign. If design re-evaluation surfaces
  during plan composition, STOP and signal arch-rework instead of working
  around it in the plan.
- Micro drift findings above are yours to resolve during plan drafting.
  Macro drift would have already kicked back before this briefing emitted.
- Plan file MUST start with header line: `**Roadmap:** <path> — Part **<ID>**`
  (drives `/session_end` Phase 4.5 drift check; missing header = plan is invisible
  to drift detection and Part transition will require manual `/update_roadmap`).

─── Cross-references ───
- Design doc: <full path with anchor>
- Roadmap Part row: <roadmap path>#parts (Part: <Part Name>)
- Memory + Skills: agent runs CLAUDE.md Planning Phase Checklist before plan compose

─── Next Steps (Drafting Workflow) ───
1. **Explore** — dispatch /explore for prior-art discovery on any NEW types the design
   introduces. Briefing's API + Test Pin block enumerates the new types — search for
   similar precedents (`*Config.cs`, factory defaults, static-seam patterns) across
   {{PROJECT_NAME}} + Jmodot BEFORE proposing the new type. Cite found precedents in plan, or
   override the design's new-type proposal if a precedent fits.
2. **Validate** — validate any AskUserQuestion-resolved decisions against the codebase.
   Required when the question presented design alternatives (DI shape, wiring strategy,
   API decomposition); the validation may OVERRIDE the tentative pick (treat menu-pick
   as starting input, not as a settled decision).
3. **Review** — read the critical files surfaced by explore/validation; reconcile findings.
4. **Draft** — compose plan to plan file (header line required, per Gates & Contract above).
5. **Approval** — present the plan file for the user's explicit go-ahead.
═══════════════════════════════════════════════════════════════
```

After emitting, /plan_part is done. The drafting agent reads the briefing as front-loaded context, then continues with the **standard drafting workflow** (`/explore` for prior-art, a validation pass for tentative decisions, a review pass). The Planning Phase Checklist (Memory + Skill search per CLAUDE.md) runs alongside `/explore`. User reviews the composed plan; `/plan_check` optionally audits before code starts.

---

## Constraints

- **Read-only.** No file writes. The briefing surfaces inline in the assistant message.
- **Pre-draft by construction.** Run before plan drafting begins, so a macro-drift kick-back costs no drafted plan.
- **No agents — SCOPED TO PHASES 1–4 ONLY.** /plan_part's own verification work is sequential read + LSP + grep (no `Task` dispatch; bounded, no parallelism benefit). This does NOT apply to `/explore` and the caller's validation pass downstream of the briefing — those ARE in scope and ARE required when tentative design decisions emerge (see `reference/plan_part_composition.md` §Workflow Composition).
- **No worklog adds.** Drift findings route to arch-rework (macro) or the briefing (micro); they don't become worklog items.
- **Cloud compatible.** LSP fallback to `Grep("class\\s+<Type>")` per CLAUDE.md §9 verified-unique-name carve-out when LSP unavailable.

---

## Cross-references

- [`reference/plan_part_composition.md`](reference/plan_part_composition.md) — §Workflow Composition (read at the Phase 5 hand-off) and §Anti-patterns (read before skipping or softening any phase)

- [`_brainstorm_shared/common.md §6`](../skills/_brainstorm_shared/common.md) — roadmap.md schema (Parts table, State vocab §6.3, Source-cell anchor rules §6.2)
- [`architecture_brainstorm/SKILL.md`](../skills/architecture_brainstorm/SKILL.md) — Step 5 readiness gate, API + Test Pin sub-procedure, *Plan-production handoff requirement* (this command operationalizes that prose)
- [`/update_roadmap`](update_roadmap.md) — companion executor; invoked via `/update_roadmap transition <part> to arch-rework` when macro drift fires
- [`/plan_check`](plan_check.md) — post-draft audit (Memory gotchas + abstraction discovery); composes with this command (pre-draft setup)
- [`project_subsystems` skill](../skills/project_subsystems/SKILL.md) — Integration touch points cross-reference (≤2 subsystem boundaries per arch Step 5)
- CLAUDE.md *Planning Phase Checklist* — the drafting agent runs this after consuming the briefing
- CLAUDE.md §9 *Tool Routing — Pre-Call Litmus* — Phase 2 bundled `read_files`, Phase 3 per-claim tool selection