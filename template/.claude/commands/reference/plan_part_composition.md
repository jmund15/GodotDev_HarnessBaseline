---
disable-model-invocation: true
---

# /plan_part — workflow composition and anti-patterns

Read §Workflow Composition at the Phase 5 hand-off, and §Anti-patterns before skipping or softening any phase. Entrypoint: [`../plan_part.md`](../plan_part.md).

## Workflow Composition

`/plan_part` is a **front-loader**, not a replacement for the drafting workflow. The stage order it hands off into is the drive spine's `## Stages` ([`drive_spine.md`](../../skills/_brainstorm_shared/drive_spine.md)). The briefing surfaces, and the caller proceeds into drafting.

Phase 3 verification and `/explore` discovery are distinct evidence tasks; neither substitutes for the other.

**Failure mode this composition prevents:** a verified brief can still miss a viable owner or an alternative shape, and a discovery survey cannot verify every concrete design claim. Keep both `/explore` and the caller's validation pass downstream of the briefing.

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "Skip Phase 3 verification — design is recent, code can't have drifted" | Drift accrues silently between brainstorm-complete and plan-start, and the verification is the value-add ([`plan_part.md`](../plan_part.md) §Phase 3). A warm chain bounds Phase 3 to the files changed since the grounding base (`plan_part.md` §Procedure); it never skips it. |
| "Treat all drift as macro — safer to kick back than risk planning against stale assumptions" | Defeats the macro/micro split: over-routing to arch-rework wastes a brainstorm session on micro fixes. Classify by the litmus in [`plan_part.md`](../plan_part.md) §Phase 3. |
| "Emit the briefing even on macro drift — let the drafting agent decide" | No. Drafting on invalid premises ships the wrong thing; macro drift is a HARD-STOP with kick-back ([`plan_part.md`](../plan_part.md) §Phase 4). |
| "Skip the API + Test Pin verbatim load — the design doc summary is enough" | Load verbatim, never summarize ([`plan_part.md`](../plan_part.md) §Phase 2). A summary lets the agent re-design a surface it never saw in full. |
| "Surface drift findings as one-line bullets — the drafting agent will figure it out" | Micro-drift bullets MUST include: (a) what drifted, (b) where (file:line), (c) suggested in-plan resolution. Three columns. Anything thinner forces the drafting agent to re-do the verification work. |
| "Resolve macro drift inline by adjusting the briefing" | The briefing is read-only. Macro drift means the design itself needs re-work; that's `/architecture_brainstorm` against the `arch-rework`'d Part, not a briefing edit. |
| "Run /plan_part after drafting has already started — the briefing emits identically" | It emits, but late invocation forfeits the fail-fast win: a macro-drift kick-back then discards drafting work already done. Pre-draft IS the path. |
| "AskUserQuestion menu-pick is enough to settle a design decision" | Menu-pick captures user intent on a starter; the validation deep-dive checks the pick against codebase prior-art and may OVERRIDE the tentative choice. Treat AskUserQuestion as starting input to the validation pass, not as substitute. |
| "The design's Open Questions are just meta-notes — load them for context, no need to surface each" | A scope decision the design parked there is invisible otherwise ([`plan_part.md`](../plan_part.md) §Phase 2 directive (e) and the *Unresolved Scope Notes* briefing slot). Surface every parked scope-note as resolve-or-defer; the design's deferral must not become your omission. |
| "A scope signal crossed a clear threshold — bounce the Part to `arch-rework` to be safe" | No — the scope signal is advisory, per [`plan_part.md`](../plan_part.md) §Phase 3 — Cross-reference against codebase. Auto-bouncing on a proxy mis-fires (it can't tell bounded-but-large from unbounded) and wastes the brainstorm session this command exists to protect. |
| "Scope-inflation needs the full call-graph — dispatch an Explore agent to be thorough" | No — the scope proxy reuses the `findReferences` / `Glob` calls Phase 3 already issues for verification. Widening into open-ended blast-radius discovery is `/explore`'s job (downstream of the briefing); pulling it into `/plan_part` re-introduces the exact Phase-3-vs-`/explore` conflation the §Workflow Composition section forbids. Cheap byproduct proxy only; no new dispatch. |
