---
description: One-shot drive of a plan-pending Part to shipped code — plan_part brief → in-session plan → plan_check convergence → execute → regression_gate → commits → roadmap complete. No Plan Mode stop.
---

# /part_drive — One-Shot Part Drive (Brief → Plan → Execute → Ship)

Fuses [`/plan_drive`](plan_drive.md)'s plan-production loop with [`/part_execute`](part_execute.md)'s execution grind into a single autonomous session, **without the Plan Mode / `ExitPlanMode` stop**. The invocation itself is the execution directive (`feedback_honor_execution_directive`): the user authorizes brief-through-commit in one go, and the halt valves below are the only legitimate pauses.

Use `/plan_drive` instead when the user wants to review the plan before code is written. Use `/part_execute` when an approved plan file already exists. `/part_drive` is for Parts the user trusts end-to-end — typically retirement/refactor Parts with a tight design surface, or Parts whose design doc already resolved the taste questions.

## The flow

1. **Brief** — run [`/plan_part <part>`](plan_part.md) logic: load the design surface **verbatim** (section-bounded reads), verify against the codebase, classify drift. **Macro drift → halt (a).** Distinguish what the design section assigns to THIS Part vs items already shipped by prior Parts or explicitly deferred to later ones — the roadmap's sibling rows + the design's Parts-decomposition section are the boundary authority. **Roadmap reads are grep-anchored:** the Part row, its sibling rows, and the *Currently ready* section carry the boundary signal; the Mermaid block + classDefs are pure redundancy with the table — never load them.
2. **Plan in-session (no Plan Mode)** — explore prior art, resolve analytical questions from code (never guesses), and write the plan file to `.claude/plans/<slug>.md` with the `**Roadmap:** <path> — Part **<ID>**` header (drives `/session_end` drift detection). The plan file is still mandatory: it is the audit surface for `/plan_check`, the parity ledger for deletions, and the Decision-record home. Include the *Closing steps (Definition of Done)* section: gate → commits (submodule order) → `/update_roadmap` flip.
3. **Audit & converge** — run [`/plan_check @<plan-file>`](plan_check.md) with its **conditional lens composition** (pure-retirement plans drop `plc-pattern-fit`) and **path-passed CONTEXT** (Agent-tool subagents read the plan file; no verbatim paste). Fold findings per `/plan_drive`'s convergence rules (round cap 2, intent-preserving auto-apply only, liveness precondition). **Three part_drive-specific deltas:**
   - **Taste forks HALT.** There is no `ExitPlanMode` to batch them into. A load-bearing taste fork (per the appetite invariant) → **halt (f)** immediately; do not parameterize around it.
   - **Round-2 re-dispatch is conditional.** If every folded revision was itself derived from a first-party code read (you verified the mechanism in source before editing the plan), the round-2 re-audit adds no evidence — skip it and record the basis. Re-dispatch only for revisions resting on agent testimony or untested inference, as ONE focused verifier under the evidence-quoting rule.
   - **Verify refuting agent claims first-party.** An agent that *refutes* the plan on empirical grounds ("this code already exists / is already refactored / file missing") gets a direct Grep/Read verification BEFORE its finding counts (`feedback_verify_explore_agent_empirical_claims` — an agent fabricated exactly this claim on the maiden run). Fabricated refutation = discard the finding, record it, continue; do NOT re-dispatch to the same agent.
4. **Execute** — `/part_execute` stance on the converged plan: TDD per slice (strict in Logic; RED proven before GREEN for any behavior change — a one-line wiring change still gets its RED), deterministic-Gameplay via ISceneRunner, subjective flagged for playtest (**halt (c)**). **For retirement/refactor Parts:** stand up a *transient parity capture* before surgery (self-baselining scratch test against the CURRENT code: write fixed-seed golden outputs if absent, else compare; delete post-surgery) — the empirical half of `feedback_refactor_parity_audit`, and the only instrument that catches silent drift when the old oracles are themselves being deleted. Post-deletion: retired-name grep sweep including doc comments (`gotcha_errorsonly_build_hides_cref_drift`). **Edit strategy:** under ~30% file churn use targeted `Edit`s, above it a full `Write`; run file-wide regex passes (PowerShell `-replace`) BEFORE targeted `Edit`s on the same file — a regex pass invalidates `Edit`'s read state and forces a re-read.
5. **Gate** — full [`/regression_gate`](regression_gate.md), single-flight. Failure that isn't a trivial in-scope fix → **halt (d)**. A baseline *drop* that is exactly accounted by the Part's test arithmetic (N retired + M added) may be applied with prominent disclosure in the report + commit message; any unaccounted drop → **halt (d)**.
6. **Commits** — categorical split, submodule first (`jmodot_submodule.md` rules: verify `origin/master` containment, message via temp-file `-F`, pointer bump shows `160000`). **Concurrent-session index hygiene** (`gotcha_concurrent_session_shared_index_collision`): enumerate `git status --short` and treat any staged entry you didn't stage as foreign — unstage it, stage YOUR files by explicit path, and verify the committed file list afterward (a foreign staged entry silently rides into `git commit` even without `-a`; this fired on the maiden run). Foreign worktree WIP (modified/untracked files from a parallel session) is never swept in. Do not push.
7. **Close out** — [`/update_roadmap`](update_roadmap.md) Part → `complete` (applied directly under the one-shot directive; diff surfaced in the final report). Offer `/pr_ready` if the Part rides toward a PR; on main-direct workflows the gate + commits close it.

## Halt valves (union of the parents' sets)

| Valve | Trigger | From |
|---|---|---|
| **(a) Factual-basis drift** | `/plan_part` macro drift; a plan-referenced file/type missing at execution; an agent's load-bearing empirical claim failing first-party verification *in the plan's favor is fine — failing against the codebase halts* | plan_drive (a) / part_execute (a) |
| **(b) Unbounded scope** | File list can't be bounded at plan time, or execution discovers the Part is materially larger than planned | plan_drive (b) |
| **(c) Subjective gameplay** | Behavior that can't be test-gated — implement mechanism, flag for playtest, continue; halt only if the Part's DoD *is* the subjective behavior | part_execute (c) |
| **(d) Gate / audit failure** | `/regression_gate` failure beyond trivial in-scope fix; unaccounted baseline drop; `/plan_check` agent INCONCLUSIVE twice (after the one re-run) | part_execute (d) / plan_drive (d, e) |
| **(f) Load-bearing taste fork** | Appetite-dependent question whose answer changes the plan's shape — no batching point exists, so it halts immediately | plan_drive (f), hardened |

## Anti-patterns

| Rationalization | Reality |
|---|---|
| "No Plan Mode means no plan file." | The plan file is load-bearing: plan_check audits it, session_end drift-detects on its header, the parity ledger lives in it. Write it. |
| "The audit agent says the work is already done / the premise is wrong — adjust course." | Verify first-party first. Agents refute confidently and wrongly; one fabricated an 'already-refactored' file read on the maiden run. Grep beats testimony. |
| "Index has a staged file I didn't touch — commit anyway, it's probably mine." | It's the concurrent-session collision. Unstage it, stage by explicit path, verify the committed list. |
| "It's a deletion Part — nothing to TDD." | The retirement's behavior changes (collapsed loops, pref flips) still get RED→GREEN, and the parity capture pins everything that must NOT change. |
| "This taste question is small — pick the sensible default to keep the one-shot moving." | One-shot mode removes the batching point, not the appetite invariant. Taste halts. |
