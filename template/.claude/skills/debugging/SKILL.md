---
name: Debugging Methodology
description: >-
  Auto-load BEFORE proposing fixes when investigating bugs, regressions, or flaky
  behavior. Triggers: "why is X failing", "investigate the bug", "flaky", "intermittent",
  "crash", "orphan", "regression", "something broken", "trace why", "performance
  regression". SKIP when the root cause is already identified — go straight to the fix.
---

# Debugging Methodology

> **Scope:** Unlike the project's test-first rule (CLAUDE.md §Development Philosophy: Hybrid TDD, which governs *test-driven development*), this debugging procedure applies to **any bug in any domain** — Logic, Gameplay, framework, build, runtime. The discipline is the same: investigate before fixing.

## The Iron Law

```
NO FIXES WITHOUT ROOT-CAUSE INVESTIGATION FIRST
```

If you find yourself proposing a fix before completing Phase 1 — **stop and investigate**. Confidence is not evidence. The user has not asked you to be fast; they have asked you to be correct.

---

## Phase 1 — Build a feedback loop

**This is the skill. Everything downstream consumes the loop.** If you have a fast, deterministic, agent-runnable pass/fail signal for the bug, you will find the cause — bisection, hypothesis-testing, and instrumentation all just consume that signal. If you don't have one, no amount of staring at code will save you.

Spend disproportionate effort here. **Be aggressive. Be creative. Refuse to give up.**

### Memory seed first

Before designing the loop, search auto-memory for the high-leverage gotcha buckets: object lifecycle and disposal, pooled-object reset ordering, and orphaned processes or silently skipped tests. `reference/memory_domains.md` supplies the project's seed terms.

These accumulate the most "this bit me before" knowledge. Beyond them, search the **specific** task domain. Recall is semantic-search over `.claude/auto-memory/` — query with an NL paraphrase of the symptom, and search facets separately rather than one mega-query (CLAUDE.md §2 *Memory*).

### Loop options (ranked roughly by leverage)

1. **Failing test at the right seam.** Pick the suite that matches the bug class: pure logic, cross-system integration seam, or player/user-observable end-to-end behavior. The project's testing skill, where it ships one, names the suites and their classification rules.
2. **Harness on a minimal fixture** (a fixture scene, app instance or service stub), simulating input and asserting the observable outcome. Prefer one that runs headless.
3. **Run the real app** for manual repro. Pattern: trigger → poll the runtime output → assert. The engine's path-scoped rules name the run and output tools.
4. **Replay a captured runtime log.** Real session, deterministic offline. `environment_bootstrap` owns the log locations.
5. **Throwaway fixture** exercising the bug code path with one function call. Useful when the production scene is too tangled to isolate the symptom.
6. **`git bisect run <the project's single-test command>`** if the bug appeared between two known-good states. Automate the verdict so bisection runs unattended. **Verify the good-end FIRST** (run the failing test at the supposed-good commit) before automating a wide window — a bisect presupposes a green→red transition exists. If both endpoints fail with an *identical* failure set, there is no transition: reclassify as "never-green-but-masked" (e.g. a test red since its introduction commit, hidden by a stale baseline) and skip the bisect. Note checking out an old commit also reverts submodules and the `.claude/` harness — `git submodule update --init` per step, restore on exit.
7. **Differential loop** — run the same input through old-version vs new-version (or two configs) and diff outputs. Best for "regression appeared between commit X and Y."
8. **Property / seeded-RNG fuzz loop** — run N random seeded inputs and look for the failure mode. The right tool for "it sometimes returns wrong output."
9. **HITL manual test** — last resort. Ask the user to reproduce while you read logs after. If you go here, capture log files, screen recordings with timestamps, or any artifact that turns the manual repro into a replayable one.

### Cloud vs. local — pick the right loop for the environment

- **Local sessions:** running the real app is fastest for visual bugs.
- **Cloud sessions** (`CLAUDE_CODE_REMOTE=true`): interactive run tools are usually unavailable. Force the loop to headless tests or to log-replay; the project's cloud rule names what works there.

### Iterate on the loop itself

Treat the loop as a product. Once you have *a* loop, ask:

- **Faster?** Cache setup, skip unrelated init, narrow the test scope.
- **Sharper?** Assert on the specific symptom, not "didn't crash."
- **More deterministic?** Pin time, seed the RNG, isolate shared state, freeze inputs.

A 30-second flaky loop is barely better than no loop. A 2-second deterministic loop is a debugging superpower.

### Non-deterministic bugs

The goal is not a clean repro but a **higher reproduction rate**. Loop the trigger 100×, parallelise across batched test runs, narrow timing windows, inject `await`s. A 50%-flake bug is debuggable; 1% is not — keep raising the rate until it's debuggable.

### When you genuinely cannot build a loop

Stop and say so explicitly. List what you tried. Ask the user for: (a) access to the environment that reproduces it, (b) a captured artifact (runtime log dump, screen recording with timestamps, seeded test scenario), or (c) permission to add temporary log instrumentation to production code paths. Do **not** proceed to hypothesise without a loop.

Do not proceed to Phase 2 until you have a loop you believe in.

---

## Phase 2 — Reproduce + Investigate

Run the loop. Watch the bug appear. Then gather evidence around it before forming any hypotheses.

### Confirm the reproduction

- [ ] The loop produces the failure mode the **user** described — not a different failure that happens to be nearby. Wrong bug = wrong fix.
- [ ] The failure is reproducible across multiple runs (or, for non-deterministic bugs, reproducible at a high enough rate to debug against).
- [ ] You captured the exact symptom — exact logged error message, exact orphan count, exact assertion failure, exact frame timing — so later phases can verify the fix actually addresses *this*.

Do not proceed until you reproduce the bug.

### Read errors carefully

*Why:* The error message is the most direct evidence available. Skipping it loses information that costs minutes to recover.

- Stack traces: read from inner-most frame outward.
- Logged errors: treat as first-class evidence, and check whether the project's test runner fails on them.
- Build errors: cite the exact `file:line:error-code` in your reasoning. Don't paraphrase.

### Reproduce consistently

*Why:* A bug that won't reproduce is a bug you cannot verify-fix. Five minutes on reproduction before thirty minutes on investigation is leverage.

**Rule:** If reproduction is intermittent, that's the bug — race conditions, init-order issues, frame-timing. Don't "fix" the symptom; investigate the determinism gap.

### Check recent changes

**Rule:** Run `git log --oneline -10` and `git diff HEAD~1` before forming hypotheses.

*Why:* Most "mysterious" bugs are caused by the most recent change. Skipping this step costs more time than running it.

### Gather evidence in multi-component systems

> *"Logs are Truth: you can't see runtime."* — CLAUDE.md Core Principles

**Rule:** Instrument boundaries between components, run the failing scenario, then read the logs.

**The Instrument → Run → Verify loop:**

1. **Instrument:** Place an Info-level log at every boundary the data crosses, through the project's logger. Log STATE CHANGES, not state.
2. **Run:** run the app (automated) or ask the user (manual repro with concrete steps — *don't* tell the user to test while you spin up the app; they're often working on other tasks).
3. **Verify:** read the runtime output while running, OR read the post-run log.

**Verify authored configuration first.** Before instrumenting, read the entity's authored configuration (scene, config file, container registration) to confirm which components/strategies are actually wired. Don't assume an entity uses a particular strategy — a mismatch between code-expected wiring and scene-actual wiring is itself the bug a surprising fraction of the time.

**Boundary chain:** write the flow's chain before instrumenting (e.g. `Input → Controller → DomainCore → Behavior → Output`). Each arrow is one `[<scope>] <state-change>` log call. The bug lives at whichever boundary the log message disagrees with the next log message.

Comment out retained debug logs (don't delete) if they may be useful for future debugging of the same subsystem.

### Trace data flow backward

**Rule:** When the visible symptom is N layers downstream of the cause, walk backward one layer at a time. Don't fix at the symptom layer until you've identified the cause.

**Worked example:** visible symptom *"the player washes white after a hit."* The hit-flash component correctly set the tint, but a per-frame visual controller overwrote it: its transient-effect reset stomped the tint with a bare base color, wiping persistent tints another service had registered. Fix required tracing backward: *wash* → *reset* → *effective-color computation* → *service layering*. Premature fixes at the symptom layer (re-applying tint after every hit) would have masked, not resolved, the contention.

*"Why is X using Y?"* is a retire-Y signal. Compatibility flags, axis-restricted geometry, and approximations of engine primitives are all symptom-fix tells.

---

## Phase 3 — Pattern Analysis

**Rule:** Find a working example before forming a hypothesis. If no working example exists, that is itself an investigation lead.

### Find working examples

- LSP `findReferences` on the type/method to find every consumer (per [`.claude/rules/csharp_lsp.md`](../../rules/csharp_lsp.md) for C#, or the project's language server).
- `Grep` for similar patterns (e.g., other abilities that call the same method without failing).
- Diff: what does the working call site do that the failing one doesn't?

### Identify differences

- Code-level: argument order, missing `await`, null vs default.
- Data-level: authored-data defaults, a missing assignment in authored configuration, ID drift.
- Lifecycle-level: the caller running in a different lifecycle phase (construction vs ready vs per-frame).

### Understand dependencies

**Rule:** Before fixing X, list everything that depends on X. If your fix changes X's contract, every dependent breaks. Use LSP `findReferences` to make the list non-speculative.

---

## Phase 4 — Hypothesise + Test

Two disciplines compose here. Skipping either burns time.

### Rank 3–5 hypotheses *visibly* — anti-anchor

Generate **3–5 ranked hypotheses** before testing any of them. Single-hypothesis generation anchors on the first plausible idea and you stop seeing alternatives.

Each hypothesis must be **falsifiable** — state the prediction it makes:

> Format: "If `<X>` is the cause, then changing `<Y>` will make the bug disappear / changing `<Z>` will make it worse."

If you cannot state the prediction, the hypothesis is a vibe — discard or sharpen it.

**Write the ranked list visibly, then proceed.** Auto Mode prefers continuous execution; do NOT block waiting for user approval of the ranking. Surface the list — the user often re-ranks instantly with domain knowledge ("we just changed #3", "we already ruled out #1") and will interject if needed. Cheap checkpoint, big time saver, but no hard pause.

### Test ONE at a time — anti-stack-tracking

Once the list is visible, **test the top hypothesis only**. Don't form Hypothesis 2 until Hypothesis 1 is fully confirmed or refuted.

*Why:* Stack-tracking competing hypotheses across multi-step debugging is how you spend 90 minutes on a 15-minute bug. Multi-hypothesis fixes ("fix A, B, and C — one of them is the bug") destroy the evidence needed to learn from the next bug. You will not know which fix worked.

If the top hypothesis is refuted, re-rank the remaining 2–4. Don't generate new hypotheses in flight unless the test result revealed information that changes the field — that's a return to Phase 2 (gather evidence), not a Phase 4 mid-step.

### Tool preference for testing

1. **Test assertions** that distinguish hypotheses. One sharper assertion beats ten logs.
2. **Debug-level logs** at the boundaries that distinguish hypotheses (see tagged-log convention below).
3. **Never "log everything and grep"** — that's how Phase 4 turns into a 30-minute log-reading swamp.

### Tagged-log convention — `[Subsystem][DIAG-<id>]`

Use the logger's **Debug level** as the diagnostic channel — ephemeral, and it won't pollute the `Warning` or `Error` signal in production. Compose tags as `[Subsystem][DIAG-<4-char-id>]`, e.g. `[Ability][DIAG-a4f2] cast state=<state> target=<target>`.

The subsystem prefix keeps the diag log visible to a log analyzer's subsystem filter; the `[DIAG-]` half is unique-to-this-session so Phase 6 cleanup is a single grep. Pick four random hex chars per session. The project's logging rule, where it ships one, owns level rules and the canonical tag list.

### CRITICAL — the Debug level is usually off

**Phase 4 procedure:**

1. Enable the Debug level (the project's logging rule names the switch).
2. Add `[Subsystem][DIAG-<id>]` instrumentation mapping to a specific Phase 4 prediction.
3. Run the loop, then slice the log by subsystem.
4. Phase 6 cleanup MUST restore the Debug switch and remove all `[DIAG-]` lines; any `[DIAG-]` log without a same-session removal commit needs a worklog item.

If you skip step 1, your instrumentation produces nothing and you'll think the code path didn't execute.

### Performance branch

For performance regressions, logs are usually wrong. Instead:

1. Establish a baseline measurement (a monotonic timer, profiler, frame-time sampling).
2. Bisect the regression (`git bisect run`) against the baseline.
3. Measure first, fix second.

Adding logs to a perf bug usually changes the timing enough to mask the bug.

---

## Phase 5 — Fix + Verify

**Rule:** Failing test FIRST → single fix → verify.

### Write a failing test FIRST

Cross-link to the project's test-first rule (CLAUDE.md §Development Philosophy: Hybrid TDD). The test must fail because of the hypothesised cause and pass after the fix. No "I'll add the test once it works" — you will adapt the test to the fix.

A correct seam is one where the test exercises the **real bug pattern** as it occurs at the call site. If the only available seam is too shallow (single-caller test when the bug needs multiple callers, Logic-domain unit test that can't replicate the engine-lifecycle chain that triggered the bug), a regression test there gives false confidence.

Even when the domain classifies as Gameplay, if the bug class IS the integration (hot-loop, race, shared-flag soup), write the seam-level integration test BEFORE shipping. Canonical case: a 3-line revert passed thousands of Logic tests but froze the app on first spawn — the seam-level integration test would have caught it.

### Picking the right suite

Use the project's suite classification (its testing skill, where it ships one):

- **Pure logic / data / math:** the logic suite (no engine runtime).
- **Cross-system seam** (state machine + child state, pool + spawn, component + collision + reaction): the integration suite — even if the diff looks trivial. Memorialised integration regressions REQUIRE a seam test.
- **Player- or user-observable behavior:** the end-to-end or sanity suite.

### If no correct seam exists, that itself is the finding

Note it explicitly. The codebase architecture is preventing the bug from being locked down. Hand off to the **Worklog** with `arch | <description>` — flag for future architectural work.

Do **not** invent a new slash command in this skill. Do **not** repurpose `/spell_arch_audit` (too narrow — Ability-only) or `/session_audit` (post-hoc, wrong shape). A general-purpose `/arch_audit` may exist later; today, the Worklog is the right escalation target.

### If a correct seam exists

1. Turn the minimised repro into a failing test at that seam.
2. Watch it fail (for the hypothesised reason — no other reason; if it could fail for two reasons, narrow the test).
3. Apply the fix.
4. Watch it pass.
5. Re-run the Phase 1 feedback loop against the original (un-minimised) scenario to confirm the fix addresses *the user's* bug, not just the test version.

### Single fix per attempt

**Rule:** One change per fix attempt. Resist the urge to "also clean this up while I'm here."

*Why:* Bundled fixes obscure which change resolved the bug. If the bug recurs, you cannot bisect; if the fix breaks something else, you cannot tell which sub-change caused the regression.

### Verify

- The failing test from above now passes.
- The original symptom is gone (manual repro or post-run log check).
- No logged errors in the run output.
- Run the broader test suite (the project's regression gate, which `change_control` §Gate cadence names, when appropriate) before claiming the bug is fixed. *"Should work now"* is not evidence.

### The 3-fixes-failed gate

**Rule:** If 3+ fixes have failed for the same bug — STOP. This is no longer a debugging task; it is a redesign signal.

**Procedure on gate trigger:**

1. Do NOT attempt fix #4. The pattern is wrong, not the implementation.
2. Question fundamentals: Is the architecture sound? Are we sticking with this pattern through inertia? Should we refactor the architecture instead of continuing to patch symptoms?
3. **Switch to the [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) skill** to explore alternative architectures. The brainstorming skill's Socratic clarifying-questions phase + 2–3 ranked approaches is the right shape for "is this pattern wrong?" questions; this skill's procedure assumes the pattern is sound and is just being applied incorrectly.
4. **Discuss with the user before attempting fix #4.** Do not silently continue.

**Cross-reference:** `feedback_recommended_fix_means_implement.md` reverses the usual presumption — when the *user* says "do the recommended fix," default to shipping. But the 3-fixes-failed gate is the inverse case: when *I* have proposed three fixes and none worked, default to *stopping* and asking, not silently trying again. The two rules compose: user-recommended → ship; self-recommended-after-3-failures → stop.

---

## Phase 6 — Cleanup + Post-mortem

Required before declaring done:

- [ ] **Original repro no longer reproduces** — re-run the Phase 1 loop.
- [ ] **Regression test passes** (or absence of seam is documented in Worklog).
- [ ] **All `[DIAG-...]` instrumentation removed** — `Grep tool` with pattern `\[DIAG-` across source files, delete each line. Restore the Debug switch if you flipped it.
- [ ] **Throwaway fixtures / harnesses deleted** (or moved to a clearly-marked fixtures location with a comment).
- [ ] **The hypothesis that turned out correct is stated in the commit / PR message** — so the next debugger learns.

### Post-mortem to auto-memory (mandatory if non-obvious)

If the correct hypothesis surfaced a non-obvious gotcha that would bite again, save it to auto-memory. **Memory is the project's durable surface; commit messages are not searched.** This aligns with CLAUDE.md "DOCUMENT, MEMORIZE, & CODIFY":

- ✅ Save: surprising behavior, cross-system races, lifecycle gotchas, "I burned 2 hours because X" stories.
- ❌ Don't save: API signatures, "fixed null check in MyClass.Method" — those belong in code docs.
- **Litmus test:** *"Would forgetting this cause another debugging session like this one?"* → Yes = Memory.

Pick the placement per CLAUDE.md §2 *Memory (One Store, Two Tiers)*:

- Surprising / cross-cutting rule worth surfacing every session → new hot topic file (`gotcha_*.md`) + a `MEMORY.md` pointer in the same turn.
- Bulk domain detail → extend the matching cold bucket under `archive/` (one `archive_<domain>_gotchas.md` per domain) — no index pointer.

### Architectural recommendation, after the fix

Ask: **what would have prevented this bug?** If the answer involves architectural change (no good test seam, tangled callers, hidden coupling, shallow module that should have been deeper), hand off to the Worklog with `arch` domain. Make the recommendation **after** the fix is in, not before — you have more information now than when you started.

If the architectural smell is "shallow module / leaky abstraction," apply the **Deletion Test** from `architecture_philosophy/SKILL.md` to articulate the recommendation precisely.

---

## Red Flags & Rationalizations

| Rationalization | Reality |
|---|---|
| "I think I see what's wrong, let me just patch it" | Confidence is not evidence. Run Phase 1 first. |
| "The error is obvious — no need to reproduce" | "Obvious" errors that resist a one-line fix are usually mis-diagnosed. |
| "I'll just test the top hypothesis without writing the others down" | Single-hypothesis generation anchors. Write the 3–5 list visibly even if you only test #1. |
| "I'll add the test once the fix works" | You will adapt the test to the fix. Write the test from the failing behavior FIRST. |
| "Let me also fix this other thing while I'm here" | Bundled fixes destroy bisection. One fix per attempt. |
| "Three fixes failed but the next one will work" | This is the 3-fixes-failed gate. STOP. |
| "I don't have time to instrument boundaries" | You have time to debug for 90 minutes; you don't have 5 minutes for boundary logs? |
| "The bug doesn't reproduce, but I know what's wrong" | If it doesn't reproduce, you cannot verify the fix. Reproduction is non-negotiable. |
| "I can't build a loop, I'll just stare at the code" | Stop and say so explicitly. Ask the user for environment access, captured artifacts, or instrumentation permission. |
| "Should work now" / "Probably fixed" / "Seems to be passing" | These are documented [refused claims](../../commands/agents/orchestrator_action_protocol.md#claims-to-refuse). Cite evidence (test output, log line, exit code) or use future-tense honestly. |

---

## Stop signals

- If you catch yourself making any of the rationalizations above → **return to Phase 1, gather evidence**.
- If three of your fixes have failed → **switch to architecture_brainstorm or restart cold**.
- If a fix "should work" but you cannot verify it via test or post-run log → **the fix isn't done**.

---

## Quick reference card

```
1. LOOP    — Memory seed (disposal/pool/process) → pick suite → make it fast & deterministic
2. REPRO   — confirm it produces the user's symptom; trace data flow backward; check recent changes
3. PATTERN — LSP findReferences for working examples; identify differences; map dependencies
4. RANK+TEST — 3–5 falsifiable hypotheses VISIBLY → test ONE at a time
                Debug log "[Subsystem][DIAG-a4f2] ..." — enable the Debug level first!
5. FIX     — failing test FIRST; pick the suite by bug class; no seam = Worklog arch; 3-fail = architecture_brainstorm
6. CLEAN   — re-run loop, grep [DIAG-, restore the Debug switch, save gotcha to Memory
```

---

## Cross-references

**auto-memory:**
- `feedback_recommended_fix_means_implement.md` — inverse rule for *user-recommended* fixes (compose with the 3-fixes-failed gate).
- `feedback_no_performative_agreement.md` — no *"you're absolutely right!"* / *"let me implement that now"* openers when receiving the user's diagnostic feedback.

**Skills:**
- The project's testing skill, where it ships one — failing-test-first for Phase 5; suite classification; test-framework specifics.
- [`architecture_philosophy`](../architecture_philosophy/SKILL.md) — *Init-Timing & Data-Source Readiness* and *Phased Lifecycle Methods* (common cause of init-timing bugs); the Deletion Test for Phase 6 architectural recommendations.
- [`architecture_brainstorm`](../architecture_brainstorm/SKILL.md) — handoff target for the 3-fixes-failed gate when the architecture itself is the suspect.

**CLAUDE.md sections:**
- Core Principles "Logs are Truth" — the discipline this skill operationalises.
- [`.claude/rules/csharp_lsp.md`](../../rules/csharp_lsp.md) (path-scoped on `.cs`) — `findReferences` for Phase 3 dependency-mapping.

**Commands:**
- The project's regression gate (`change_control` §Gate cadence names it) — broader-test verification step in Phase 5.
- `/test_skill debugging` — adversarial validation that this skill survives rationalisation pressure.
