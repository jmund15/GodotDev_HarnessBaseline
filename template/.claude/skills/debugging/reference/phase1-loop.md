# Phase 1 — Build a feedback loop

Read at Phase 1 of [`../SKILL.md`](../SKILL.md).

**This is the skill. Everything downstream consumes the loop.** Bisection, hypothesis-testing and instrumentation all consume one fast, deterministic, agent-runnable pass/fail signal.

## Memory seed first

Search auto-memory for the high-leverage gotcha buckets before designing the loop: object lifecycle and disposal, pooled-object reset ordering, and orphaned processes or silently skipped tests. `reference/memory_domains.md` supplies the project's seed terms. Then search the **specific** task domain. Recall is semantic search over `.claude/auto-memory/`: query an NL paraphrase of the symptom, facets searched separately (CLAUDE.md §2 *Memory*).

## Loop options (ranked roughly by leverage)

1. **Failing test at the right seam.** Match the suite to the bug class: pure logic, cross-system integration seam, or user-observable end-to-end behavior. The project's testing skill, where it ships one, names the suites and their classification rules.
2. **Harness on a minimal fixture** (a fixture scene, app instance or service stub): simulate input, assert the observable outcome. Prefer one that runs headless.
3. **Run the real app** for manual repro. Pattern: trigger, poll the runtime output, assert. The engine's path-scoped rules name the run and output tools, and what a poll returns (a tool that returns the last N lines retained until stop, not the lines since the previous call, needs a larger limit or a diff against the previous read).
4. **Replay a captured runtime log.** Real session, deterministic offline. A rotating log may be stale or belong to another session's live run: classify provenance first. `environment_bootstrap` owns the log locations.
5. **Throwaway fixture** hitting the bug path directly, when the production scene is too tangled to isolate the symptom.
6. **`git bisect run <the project's single-test command>`** when the bug appeared between two known-good states. **Verify the good-end FIRST**: run the failing test at the supposed-good commit before automating a wide window. Identical failure sets at both endpoints mean there is no green-to-red transition: reclassify as never-green-but-masked (red since its introduction commit, hidden by a stale baseline) and skip the bisect. Checking out an old commit also reverts submodules and the `.claude/` harness: restore them per step and on exit.
7. **Differential loop**: the same input through old and new version (or two configs), diff the outputs. Best for "regression appeared between commit X and Y."
8. **Property / seeded-RNG fuzz loop**: N random seeded inputs. The right tool for "it sometimes returns wrong output."
9. **HITL manual test**, last resort. Ask the user to reproduce while you read logs after, capturing log files, timestamped recordings, or any artifact that turns the manual repro into a replayable one.

## Cloud vs. local

- **Local:** running the real app is fastest for visual bugs.
- **Cloud** (`CLAUDE_CODE_REMOTE=true`): interactive run tools are usually unavailable. Force the loop to headless tests or to log replay; the project's cloud rule names what works there.

## Non-deterministic bugs

Chase a **higher reproduction rate**, not a clean repro. Loop the trigger in batched runs, narrow timing windows, inject `await`s. Keep raising the rate until a pass on fixed code is distinguishable from luck, while the failure keeps one cause. Once a fix leaves it failing for a different cause, more runs only surface the next cause: the defect is what admits them all (an actor running free, shared state, wall time), so stop and name what admits them (`SKILL.md` §The Recurrence Law).

## When you genuinely cannot build a loop

Stop and say so explicitly. List what you tried. First add temporary log instrumentation to the production paths (Phase 6 removes it). If that still yields no loop, ask the user for (a) access to the environment that reproduces it or (b) a captured artifact (runtime log dump, timestamped recording, seeded scenario). Do **not** hypothesise without a loop.

## The loop's done-condition

A loop is done when it is fast (cache setup, skip unrelated init, narrow the test scope), sharp (asserts the specific symptom, not "didn't crash"), deterministic (pin time, seed the RNG, isolate shared state, freeze inputs) and seen RED.

Run the loop against the *unfixed* code and watch it fail with the user's symptom, the debugging dual of TDD's verify-RED step. A green never-red loop means the loop is wrong (wrong seam, wrong trigger, a guard swallowing the failure), not that the bug is absent.

Do not proceed to Phase 2 until you have a loop you believe in and have seen it red.
