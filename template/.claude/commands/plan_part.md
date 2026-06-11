---
description: Briefing for a plan-pending Part — load design surface verbatim, verify against codebase, surface macro/micro drift. Invoke BEFORE Plan Mode entry (macro drift kicks back without consuming a session); in-Plan-Mode invocation supported.
---

# /plan_part

Prepares an in-context briefing for the in-Plan-Mode agent working on a `plan-pending` Part. Loads the design-doc surface verbatim (API + Test Pin block, Integration touch points, Source-cell section), cross-references against current codebase state, and classifies any divergence as **macro** (→ kick to `arch-rework`) or **micro** (→ Plan Mode resolves during plan drafting).

**Companion to `/plan_check`:** this command is *pre-draft* (briefs the agent before plan composition); `/plan_check` is *post-draft* (audits a written plan). Together they bracket Plan Mode.

**Plan Mode contract:** Plan Mode is a Claude Code built-in. This command is **read-only** — surfaces a structured briefing into the assistant message; the agent (whether pre- or in-Plan-Mode) consumes it as front-loaded context. Primary invocation: run BEFORE entering Plan Mode so macro-drift kick-backs don't burn a plan-mode session. In-Plan-Mode invocation is supported (briefing emits into the active session; agent continues with standard Plan Mode Phase 1–3). The briefing is **front-loaded context for Plan Mode's standard workflow**, NOT a replacement for it — see *Plan Mode Workflow Composition* below. No file writes, no side effects.

---

## When to invoke

User-typed when scoping to a `plan-pending` roadmap Part. Two valid timings:

**Primary — before Plan Mode entry.** Run `/plan_part <part>` first, review the briefing, THEN enter Plan Mode if drift verdict is clean (or micro-only). Macro drift kicks back to `arch-rework` without consuming a plan-mode session — the structural win that makes this the default.

**Secondary — already in Plan Mode.** Allowed when the user entered Plan Mode for unrelated reasons and now wants to scope to a Part. Briefing emits into the active session; agent continues with standard Plan Mode Phase 1–3 (see *Plan Mode Workflow Composition* below).

Auto-trigger hooks should NOT fire this — it's an explicit user action that scopes the session.

Required preconditions (HARD-STOP if any fail):
- Named Part exists on the resolved roadmap.
- Part State is `plan-pending`. Other States redirect:

| State | Redirect |
|---|---|
| `arch-pending` / `arch-rework` | `/architecture_brainstorm` |
| `idea-pending` / `idea-rework` | `/idea_brainstorm` |
| `workshop-pending` | User decision, then re-route |
| `user-owned` | Not an agent-executable Part; surface the user deliverable from Trigger |
| `submap-pending` | Redirect to child sub-roadmap; pick a Part there |
| `complete` / `abandoned` | Refuse — no plan needed |

## When to skip

- **`/plan_check` already ran successfully on a drafted plan for this Part** — drift already surfaced inline at draft time.
- **Part scope is genuinely scope-1 / scope-2** (per worklog_reference) — no roadmap Part shape; just plan inline.
- **User explicitly wants to redesign** — that's an `arch-rework` transition, not a plan session.

---

## Plan Mode Workflow Composition

`/plan_part` is a **front-loader**, not a replacement for Plan Mode's standard Phase 1–3 workflow. The phases compose:

| Workflow seam | Owned by | Purpose |
|---|---|---|
| /plan_part Phase 1–2 | this command | Resolve Part + load design surface verbatim |
| /plan_part Phase 3 | this command | Verify design's existing claims against current code (mechanical; no agents) |
| /plan_part Phase 4–5 | this command | Drift verdict + emit briefing |
| **— hand-off seam —** | | Briefing surfaces; agent enters Plan Mode if invoked outside |
| Plan Mode Phase 1 (Explore) | Plan Mode built-in | Prior-art discovery for ALTERNATIVE shapes the briefing didn't enumerate (dispatch Explore agents in parallel) |
| Plan Mode Phase 2 (Plan) | Plan Mode built-in | Validate tentative design decisions against codebase; propose alternatives when AskUserQuestion surfaces options (dispatch Plan agents in parallel) |
| Plan Mode Phase 3 (Review) | Plan Mode built-in | Read critical files; reconcile findings with user intent |
| Plan Mode Phase 4 (Final Plan) | Plan Mode built-in | Compose plan to plan file |
| Plan Mode Phase 5 (ExitPlanMode) | Plan Mode built-in | Request approval |

**Discipline:** Phase 3 verification is "what does the code currently say about the design's claims" — mechanical, bounded. Plan Mode Phase 1 Explore is "what alternative shapes / prior art exist" — open-ended discovery. The two are NOT redundant; skipping either loses value the other can't recover.

**Failure mode this composition exists to prevent:** agent reads /plan_part's "No agents — sequential read + LSP + grep" rule (Phase 3 scope) and over-applies it to the whole plan session, skipping Plan Mode's Phase 1 Explore + Phase 2 Plan agents. Result: tentative design decisions ship to plan file without codebase-grounded validation; deep-dive findings surface only after user pushback. Empirically observed 2026-05-19 (P2 RunController plan session) — 2/2 design decisions overridden once Plan agents were finally dispatched.

---

## Arguments

- `/plan_part <part-name>` — target roadmap = nearest ancestor `roadmap.md` from CWD (same convention as `/update_roadmap`).
- `/plan_part <part-name> <roadmap-path>` — explicit roadmap path when CWD ambiguity exists or briefing a Part from another folder.
- `/plan_part` (no args) — print usage + the *When to invoke* table; exit without briefing.

**Part-name resolution:** exact match on the `Part` column of the resolved roadmap's Parts table. If `<part-name>` doesn't resolve, list candidate Part names and exit. Don't fuzzy-match — Part names are stable identifiers (per `/update_roadmap rename` discipline).

---

## Procedure

### Phase 1 — Resolve the Part

1. Locate target `roadmap.md` (CWD ancestor or explicit arg).
2. Parse Parts table; find row matching `<part-name>` exactly.
3. Verify State is `plan-pending`. If not → emit the redirect from the *When to invoke* table and HARD-STOP.
4. Verify every Dep on this Part has State `complete`. If any incomplete Dep → emit `⚠ Part is blocked by unfinished deps: <list>. Plan can proceed but the impl will block on these.` (non-blocking — Plan Mode might legitimately draft against not-yet-shipped deps if the user is sequencing work).
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

| Macro drift (→ arch-rework, HARD-STOP) | Micro drift (→ surface as briefing item for Plan Mode to resolve) | Glossary note (→ briefing footnote, NOT drift) |
|---|---|---|
| Named base class / interface no longer exists | Named file moved to a sibling folder; type intact | Informal naming in design pseudocode where the actual codebase entity differs in name but not shape (e.g., "Wizard.tscn" → real entity is `[Export] PackedScene _playerPrefab`) |
| Extended-family inventory collapsed below 2 subclasses (extend-not-parallel premise broken) | Named method gained/lost a parameter; intent preserved | Sibling-pattern reference where the design says "analogous to X" but X doesn't share the same contract (shape analogy only, not contract conformity) |
| New-file path collides with non-trivial existing content (parallel work happened) | Test fixture base class was renamed but extension surface is the same | Design-doc shorthand for a multi-step process the codebase formalizes via discrete API (e.g., "LoadAsync" → `RequestLoad` / `GetLoadStatus` / `CompleteLoad`) |
| Named subsystem in Integration touch points was removed / merged into another | Adjacent file added/removed in a touch-point subsystem (additive context) | Design-pseudocode method names that the executor will translate to canonical API at write-time |
| Base class became `sealed`, or `abstract` → concrete, or constructor reshape that invalidates the extension | New `BBDataSig` key exists adjacent to the planned one (additive) | |
| Jmodot ↔ PP boundary the design assumed was re-drawn | Comment / whitespace / formatting drift in linked design-doc section | |
| Source-cell wikilink anchor no longer resolves in the design doc | An extra subclass exists in the family beyond the design's enumeration | |

**Rule of thumb (litmus when in doubt):**
- *"Could the in-Plan-Mode agent address this in plan-drafting without changing the design's load-bearing claims?"* Yes → micro. No → macro.
- *"Is the divergence purely a naming/shorthand issue with the executor writing the canonical name at code-time?"* Yes → glossary note, NOT drift. Glossary notes go in the briefing's footer ("Design-doc glossary translations") rather than the drift count, to avoid diluting the actionable micro-drift signal.

**Drift count discipline:** the briefing's `Drift: macro=N | micro=M` header counts ONLY drift items, not glossary notes. A briefing with 4 micro + 2 glossary reads as `macro=0 | micro=4`. Glossary notes are listed below the verification block under a `─── Design-Doc Glossary ───` section so Plan Mode has the translations without inflating the action count.

**Scope-inflation advisory (distinct from drift).** While running the verification above, capture cheap size proxies for any named *modify* / *migrate* / *refactor* target — reuse the SAME `findReferences` / `documentSymbol` / `Glob` calls already issued; do NOT dispatch agents or widen the search (open-ended blast-radius discovery is Plan Mode Phase 1 Explore, not this command). Two proxies: (a) `findReferences` count + distinct-file count for a type the Part migrates/refactors; (b) current LOC for a file the design treats as a light edit. When a proxy materially exceeds what the design's prose implies (e.g. a "migrate existing X" one-liner against a type with 20+ call sites across 6+ files), record a **scope signal**. This catches *post-authoring scope inflation* — a Part bounded at arch Step 5 that grew via later code drift — which neither arch Step 5 (too early) nor Plan Mode's file-list gate (catches *unbounded*, not *bounded-but-large*) covers. The signal is **advisory, NOT drift**: it does not gate, does not kick to `arch-rework`, and is NOT counted in the `macro=N | micro=M` header. The proxy count is evidence for a split *recommendation* the user weighs — never a verdict (a 30-reference type may be a mechanical rename; a 3-reference one a deep rework). The authoritative bound check remains Plan Mode's file-list gate.

### Phase 4 — Drift verdict

**Any macro drift → HARD-STOP**:

1. Surface the macro findings with file paths / line numbers.
2. Propose the kick-back: `/update_roadmap transition <part-name> to arch-rework` with a Trigger naming the drifted assumptions (e.g., `"Base class <X> retired since design — extension path invalidated; re-evaluate inheritance vs composition."`).
3. Exit without emitting the briefing. The user runs the transition; later, a fresh `/architecture_brainstorm` session against the now-`arch-rework` Part re-resolves the design.

**Zero macro drift → proceed to Phase 5**. Micro drift carries forward as briefing content for the agent to resolve.

### Phase 5 — Emit briefing

Surface a structured block in the assistant message. The in-Plan-Mode agent consumes it as self-briefing before composing the plan.

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
 The briefing still emits and Plan Mode still proceeds — this is a recommendation, not a stop.
 Omit this section entirely if no signals.>

─── Out of Scope This Session (from design) ───
<bullet list of explicit exclusions>

─── Unresolved Scope Notes (resolve or consciously defer BEFORE planning) ───
<Open-Questions items + stale/deprecated/dead-code/debt/audit-for-removal notes from
 (e) above that bear on this Part. Each: the note + a resolve-or-defer prompt. The design
 parking a scope decision here does NOT make it resolved — Plan Mode either resolves it
 (commit the scope into the plan) or the user consciously defers it. An unsurfaced note here
 is the canonical "shipped a half-retired surface" failure. Omit this section if empty.>

─── Design-Doc Glossary (informational; NOT drift) ───
<bullet list of name/shorthand translations — design term → canonical codebase entity.
 Omit this section entirely if zero glossary notes were collected.>

─── Gates & Contract (respect these; rest is Plan Mode's call) ───
- File-list-bounded gate (per arch_brainstorm Step 5 handoff requirement) —
  Plan Mode's enumerated file list MUST be bounded; "...and possibly others"
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

─── Next Steps (Plan Mode Workflow) ───
1. Enter Plan Mode if not already (skip if /plan_part was invoked in-session).
2. **Phase 1 Explore** — dispatch Explore agent(s) in parallel for prior-art discovery
   on any NEW types the design introduces. Briefing's API + Test Pin block enumerates
   the new types — search for similar precedents (`*Config.cs`, factory defaults,
   static-seam patterns) across PP + Jmodot BEFORE proposing the new type. Cite found
   precedents in plan, or override the design's new-type proposal if a precedent fits.
3. **Phase 2 Plan** — dispatch Plan agent(s) in parallel to validate any
   AskUserQuestion-resolved decisions. Required when the question presented design
   alternatives (DI shape, wiring strategy, API decomposition); agent reports may
   OVERRIDE the tentative pick (treat menu-pick as Phase 0 input, not as a settled
   decision).
4. **Phase 3 Review** — read critical files identified by agents; reconcile findings.
5. **Phase 4 Final Plan** — compose plan to plan file (header line required, per
   Gates & Contract above).
6. **Phase 5 ExitPlanMode** — request approval.
═══════════════════════════════════════════════════════════════
```

After emitting, /plan_part is done. The agent (in or pre Plan Mode) reads the briefing as front-loaded context, then continues with **standard Plan Mode Phase 1–3 workflow** (Explore agents for prior-art, Plan agents for tentative-decision validation, review pass). The Planning Phase Checklist (Memory + Skill search per CLAUDE.md) runs alongside Plan Mode Phase 1. User reviews the composed plan; `/plan_check` optionally audits before code starts.

---

## Constraints

- **Read-only.** No file writes. The briefing surfaces inline in the assistant message.
- **Plan Mode NOT required.** Primary invocation is BEFORE Plan Mode entry (so macro-drift kick-back doesn't waste a session); in-Plan-Mode invocation is the secondary path. The previous "Plan Mode required" rule was reversed after empirical observation — macro drift inside Plan Mode burns the session before the kick-back can route.
- **No agents — SCOPED TO PHASES 1–4 ONLY.** /plan_part's own verification work is sequential read + LSP + grep (no `Task` dispatch; bounded, no parallelism benefit). This does NOT apply to Plan Mode's Phase 1 Explore + Phase 2 Plan agent dispatches downstream of the briefing — those ARE in scope and ARE required when tentative design decisions emerge (see *Plan Mode Workflow Composition*).
- **No worklog adds.** Drift findings route to arch-rework (macro) or the briefing (micro); they don't become worklog items.
- **Cloud compatible.** LSP fallback to `Grep("class\\s+<Type>")` per CLAUDE.md §9 verified-unique-name carve-out when LSP unavailable.

---

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Skip Phase 3 verification — design is recent, code can't have drifted" | Drift accrues silently between brainstorm-complete and plan-start. The verification IS the value-add; without it, this command is just a fancier `cat` of the design doc. |
| "Treat all drift as macro — safer to kick back than risk planning against stale assumptions" | Defeats the macro/micro split. Plan Mode IS responsible for micro refinement and verification (per user direction); over-routing to arch-rework wastes a brainstorm session on micro fixes. Apply the litmus: load-bearing-claim-invalidated → macro; signature-fuzziness → micro. |
| "Emit the briefing even on macro drift — let Plan Mode decide" | No. Macro drift means the design's premises don't hold. Plan Mode operating on invalid premises produces plans that ship the wrong thing. HARD-STOP and kick back. |
| "Skip the API + Test Pin verbatim load — the design doc summary is enough" | The verbatim load is the structural defense against the second failure mode this command exists to solve (agent re-designing because it didn't load the full surface). Always load verbatim; never summarize. |
| "Surface drift findings as one-line bullets — Plan Mode will figure it out" | Micro-drift bullets MUST include: (a) what drifted, (b) where (file:line), (c) suggested in-plan resolution. Three columns. Anything thinner forces Plan Mode to re-do the verification work. |
| "Resolve macro drift inline by adjusting the briefing" | The briefing is read-only. Macro drift means the design itself needs re-work; that's `/architecture_brainstorm` against the `arch-rework`'d Part, not a briefing edit. |
| "Run /plan_part inside Plan Mode for the macro-drift fail-fast benefit" | Inverted as of this refinement — outside Plan Mode IS the primary path precisely because macro-drift kick-back from inside Plan Mode wastes the session. In-Plan-Mode invocation is the secondary path (covers the "already entered" case); the briefing emits identically. |
| "Briefing-loaded context is enough — skip Plan Mode Phase 1 Explore + Phase 2 Plan agents" | The briefing verifies the design's *existing* claims; it does NOT discover alternative shapes or validate tentative design picks against prior-art. Plan Mode Phase 1–2 agents are downstream of /plan_part and ARE required, especially when AskUserQuestion surfaced design alternatives. The two workflow surfaces serve different purposes; skipping one loses value the other can't recover. See *Plan Mode Workflow Composition*. |
| "AskUserQuestion menu-pick is enough to settle a design decision" | Menu-pick captures user intent on a starter; Plan-agent deep-dive validates the pick against codebase prior-art and may OVERRIDE the tentative choice. Empirically observed 2026-05-19: 2/2 design decisions overridden after deep-dive in P2 RunController plan session (RunControllerConfig Resource → static-seam pattern; inline-RequestTransition → helper-at-two-commit-sites). Treat AskUserQuestion as Phase 0 input to Plan-agent dispatch, not as substitute. |
| "The design's Open Questions are just meta-notes — load them for context, no need to surface each" | Phase 2 reads the `## Open Questions` section but the extraction directive (e) + the *Unresolved Scope Notes* briefing slot exist precisely because a scope decision the design *parked* there (not resolved) is invisible otherwise. Empirically observed 2026-05-19 (P2): the PvP-retirement purge was parked in the design's Open Questions; `/plan_part` loaded the section but had no slot to surface it, so the purge silently dropped from the briefing and was only caught later by `/plan_check`. Surface every parked scope-note as resolve-or-defer; don't let the design's deferral become your omission. |
| "A scope signal crossed a clear threshold — bounce the Part to `arch-rework` to be safe" | No — scope signals are advisory by construction. Proxy counts (call sites, LOC) are evidence, not verdicts: a 30-reference type can be a mechanical rename, a 3-reference one a deep rework. Auto-bouncing on a proxy mis-fires (it can't tell bounded-but-large from unbounded) and wastes the brainstorm session this command exists to protect. Surface the split *recommendation*; let the user decide. The authoritative bound check stays Plan Mode's file-list gate, not a `/plan_part` proxy. |
| "Scope-inflation needs the full call-graph — dispatch an Explore agent to be thorough" | No — the scope proxy reuses the `findReferences` / `Glob` calls Phase 3 already issues for verification. Widening into open-ended blast-radius discovery is Plan Mode Phase 1 Explore's job (downstream of the briefing); pulling it into `/plan_part` re-introduces the exact Phase-3-vs-Plan-Mode-Phase-1 conflation the *Plan Mode Workflow Composition* section forbids. Cheap byproduct proxy only; no new dispatch. |

---

## Cross-references

- [`_brainstorm_shared/common.md §6`](../skills/_brainstorm_shared/common.md) — roadmap.md schema (Parts table, State vocab §6.3, Source-cell anchor rules §6.2)
- [`architecture_brainstorm/SKILL.md`](../skills/architecture_brainstorm/SKILL.md) — Step 5 readiness gate, API + Test Pin sub-procedure, Plan Mode handoff requirement (this command operationalizes that prose)
- [`/update_roadmap`](update_roadmap.md) — companion executor; invoked via `/update_roadmap transition <part> to arch-rework` when macro drift fires
- [`/plan_check`](plan_check.md) — post-draft audit (Memory gotchas + abstraction discovery); composes with this command (pre-draft setup)
- [`project_subsystems` skill](../skills/project_subsystems/SKILL.md) — Integration touch points cross-reference (≤2 subsystem boundaries per arch Step 5)
- CLAUDE.md *Planning Phase Checklist* — the in-Plan-Mode agent runs this after consuming the briefing
- CLAUDE.md §9 *Tool Routing — Pre-Call Litmus* — Phase 2 bundled `read_files`, Phase 3 per-claim tool selection
- `feedback_invoke_named_skill_not_manual_equivalent.md` — once this command exists, agents must invoke it rather than manually performing equivalent steps
