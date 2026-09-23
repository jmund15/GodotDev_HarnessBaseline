---
disable-model-invocation: true
---

# /plan_part — workflow composition and anti-patterns

Read §Workflow Composition at the Phase 5 hand-off, and §Anti-patterns before skipping or softening any phase. Entrypoint: [`../plan_part.md`](../plan_part.md).

## Workflow Composition

`/plan_part` is a **front-loader**, not a replacement for the drafting workflow. The seams compose:

| Workflow seam | Owned by | Purpose |
|---|---|---|
| /plan_part Phase 1–2 | this command | Resolve Part + load design surface verbatim |
| /plan_part Phase 3 | this command | Verify design's existing claims against current code (mechanical; no agents) |
| /plan_part Phase 4–5 | this command | Drift verdict + emit briefing |
| **— hand-off seam —** | | Briefing surfaces; the caller proceeds into drafting |
| Explore | [`/explore`](../explore.md) | Prior-art discovery for ALTERNATIVE shapes the briefing didn't enumerate |
| Validate | the drafting flow (e.g. [`/part_drive`](../part_drive.md) step 2) | Validate tentative design decisions against codebase; propose alternatives when AskUserQuestion surfaces options |
| Review | the drafting flow | Read critical files; reconcile findings with user intent |
| Draft | the drafting flow | Compose plan to plan file |
| Approval | the user | Explicit go-ahead on the finished plan file |

**Discipline:** Phase 3 verification is "what does the code currently say about the design's claims" — mechanical, bounded. `/explore` is "what alternative shapes / prior art exist" — open-ended discovery. The two are NOT redundant; skipping either loses value the other can't recover.

**Failure mode this composition exists to prevent:** agent reads /plan_part's "No agents — sequential read + LSP + grep" rule (Phase 3 scope) and over-applies it to the whole plan session, skipping `/explore` and the validation pass. Result: tentative design decisions ship to plan file without codebase-grounded validation; deep-dive findings surface only after user pushback.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Skip Phase 3 verification — design is recent, code can't have drifted" | Drift accrues silently between brainstorm-complete and plan-start. The verification IS the value-add; without it, this command is just a fancier `cat` of the design doc. |
| "Treat all drift as macro — safer to kick back than risk planning against stale assumptions" | Defeats the macro/micro split. Plan drafting IS responsible for micro refinement and verification (per user direction); over-routing to arch-rework wastes a brainstorm session on micro fixes. Apply the litmus: load-bearing-claim-invalidated → macro; signature-fuzziness → micro. |
| "Emit the briefing even on macro drift — let the drafting agent decide" | No. Macro drift means the design's premises don't hold. Drafting on invalid premises produces plans that ship the wrong thing. HARD-STOP and kick back. |
| "Skip the API + Test Pin verbatim load — the design doc summary is enough" | The verbatim load is the structural defense against the second failure mode this command exists to solve (agent re-designing because it didn't load the full surface). Always load verbatim; never summarize. |
| "Surface drift findings as one-line bullets — the drafting agent will figure it out" | Micro-drift bullets MUST include: (a) what drifted, (b) where (file:line), (c) suggested in-plan resolution. Three columns. Anything thinner forces the drafting agent to re-do the verification work. |
| "Resolve macro drift inline by adjusting the briefing" | The briefing is read-only. Macro drift means the design itself needs re-work; that's `/architecture_brainstorm` against the `arch-rework`'d Part, not a briefing edit. |
| "Run /plan_part after drafting has already started — the briefing emits identically" | It emits, but late invocation forfeits the fail-fast win: a macro-drift kick-back then discards drafting work already done. Pre-draft IS the path. |
| "Briefing-loaded context is enough — skip `/explore` and the validation pass" | §Workflow Composition's failure-mode paragraph above is this exact case; both surfaces are required. |
| "AskUserQuestion menu-pick is enough to settle a design decision" | Menu-pick captures user intent on a starter; the validation deep-dive checks the pick against codebase prior-art and may OVERRIDE the tentative choice. Treat AskUserQuestion as starting input to the validation pass, not as substitute. |
| "The design's Open Questions are just meta-notes — load them for context, no need to surface each" | Phase 2 reads the `## Open Questions` section but the extraction directive (e) + the *Unresolved Scope Notes* briefing slot exist precisely because a scope decision the design *parked* there (not resolved) is invisible otherwise. Example: the PvP-retirement purge was parked in the design's Open Questions; `/plan_part` loaded the section but had no slot to surface it, so the purge silently dropped from the briefing and was only caught later by `/plan_check`. Surface every parked scope-note as resolve-or-defer; don't let the design's deferral become your omission. |
| "A scope signal crossed a clear threshold — bounce the Part to `arch-rework` to be safe" | No — scope signals are advisory by construction. Proxy counts (call sites, LOC) are evidence, not verdicts: a 30-reference type can be a mechanical rename, a 3-reference one a deep rework. Auto-bouncing on a proxy mis-fires (it can't tell bounded-but-large from unbounded) and wastes the brainstorm session this command exists to protect. Surface the split *recommendation*; let the user decide. The authoritative bound check stays the plan's file-list bound gate, not a `/plan_part` proxy. |
| "Scope-inflation needs the full call-graph — dispatch an Explore agent to be thorough" | No — the scope proxy reuses the `findReferences` / `Glob` calls Phase 3 already issues for verification. Widening into open-ended blast-radius discovery is `/explore`'s job (downstream of the briefing); pulling it into `/plan_part` re-introduces the exact Phase-3-vs-`/explore` conflation the §Workflow Composition section forbids. Cheap byproduct proxy only; no new dispatch. |
