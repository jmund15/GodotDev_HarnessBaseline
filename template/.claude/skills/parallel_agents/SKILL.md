---
name: Parallel Agent Dispatch
description: >-
  Auto-load when there are 3+ unrelated test failures, multiple subsystems broken
  independently, batch PR reviews, cross-domain audits, or independent work units to
  parallelize. Triggers: "parallel", "fan out", "multiple subagents", "batch review",
  "cross-domain audit". SKIP when failures are related, agents would share file writes,
  or one finding would reframe the next.
---

# Parallel Agent Dispatch

This skill is the *decision procedure* for fanning out subagent work. The *spawn rules* live in `.claude/commands/agents/review_agents.md` (MANDATORY / PARALLEL / NO POLLING / NO TODOWRITE rules) — this skill does not duplicate them; it tells you **when** to apply them, and — now that dynamic Workflows exist — **which mechanism** (scripted Workflow vs. manual Agent dispatch) to fan out with.

## 0. Workflow vs. Manual Agent Dispatch — decide this FIRST

Two mechanisms now fan out subagents. Pick before anything else:

- **Dynamic Workflow** (`Workflow` tool, scripts in `.claude/workflows/*.js`) — when the fan-out shape is **known up front** and **deterministic**: a fixed or derivable item set, a barrier/pipeline, a scripted merge/score/assert. The orchestration (counts, barriers, dedup, the cap) lives in code and runs **identically every run**. Right for audits, batch review, scored scans, the test/skill batteries, find→verify→synthesize. Commands invoke it via `Workflow({scriptPath: ".claude/workflows/<file>.js", args})` (scripts parse-guard `args` — it has arrived as a JSON string on some harness versions and as a parsed value on others; see `reference_workflow_integration_mechanics.md`).
- **Manual Agent dispatch** (the `Agent`/`Task` tool, §3 below) — when the fan-out is **discovered at runtime**, **exploratory**, or a **one-off**: you don't know the item set until you've looked, or one finding reframes the next, or it's a single ad-hoc parallelization not worth a script.

**Litmus:** *Could I write the loop before seeing any results?* Yes → Workflow. No → manual dispatch. And when a command's prose is shouting anti-drift warnings at itself (*"MANDATORY: spawn exactly N in one message"*, *"do NOT run_in_background"*), that prose is a **drift fossil** — the determinism belongs in a Workflow script.

Everything below (independence, the cap, worktree races, model selection) governs **both** mechanisms unless noted. Two rules are mechanism-specific:
- **Concurrency:** a Workflow auto-caps at `min(16, cores−2)` and **queues** overflow — pass all items, no manual batch math. The §6 15-cap + nested arithmetic applies to **manual `Agent` dispatch only**.
- **Single-flight under concurrency:** Workflow `parallel()`/`pipeline()` agents must NOT each run GdUnit4 tests (named-pipe wedge) or fan out csharp-ls LSP calls (single-flight wrapper). Pre-compute the symbol map orchestrator-side and pass it in; run the test gate once, serially, outside the barrier. See `gotcha_workflow_single_flight_concurrency.md`.

---

## Pre-Dispatch Checklist (All Parallelizations)

Before fanning out:

- [ ] **Verify independence:** Does each agent have everything it needs to complete without seeing another agent's result? If no → sequential.
- [ ] **Check for shared file writes:** Will any two agents edit the same file (`.cs`, `.tscn`, `.tres`)? If yes → either serialize those agents OR partition the file's edits explicitly.
- [ ] **Estimate concurrency:** Flat dispatch ≤15 agents (cap from `review_prs.md`, *Concurrency limit* rule). Nested dispatch (each agent spawns subagents) requires `floor(15 / subagents-per-agent)` outer agents.
- [ ] **Confirm worktree isolation isn't needed:** Parallel agents share the SAME `.claude/worktrees/<name>/` working tree. If you need isolation, dispatch sequentially across separate worktrees instead.

---

## 1. When to Use Parallel Dispatch

**Trigger phrases:** "fan out", "in parallel", "batch X", "audit across N files", "fix all failing tests", "review all open PRs"

Use parallel dispatch when:

- [ ] **3+ independent investigations:** Multiple unrelated test failures, each with its own root cause. Each agent gets one failure.
- [ ] **Multiple subsystems broken independently:** A merge brought in changes to AI + VFX + Spell Architecture; each subsystem can be audited in isolation.
- [ ] **Batch PR/audit operations:** `/review_prs` reviewing N PRs; `/session_audit` running 3 orthogonal axes.
- [ ] **Cross-domain audits:** A check that walks the entire codebase but each agent owns a different domain (AI, Combat, UI, Inventory).
- [ ] **Per-test-category fix-ups:** N independent Logic-domain unit-test failures with no shared root cause.
- [ ] **`/test_skill` adversarial prompts** (sibling Batch B command): each adversarial scenario tests a different rationalization in isolation — prototypical flat parallel dispatch.

**PP exemplars** (read these to see the pattern in action):
- `/review_prs` (*Concurrency limit* + batch protocol sections) — N review agents launched in parallel batch, 15-cap exemplar.
- `/session_audit` Phase 2 — 3 orthogonal axes (design-semantics / robustness-performance / intuitiveness-testability) dispatched through the `review_fanout.js` Workflow engine; the canonical §0 "known-up-front fan-out → Workflow" exemplar.

---

## 2. When NOT to Parallelize

**Skip phrases:** "related failures", "investigation", "I'm not sure what's wrong yet"

Do NOT parallelize when:

- [ ] **Related failures** → "if all 5 test failures cascade from one `Wizard.cs` change, parallelize the fix-write step but NOT the root-cause investigation — one investigation, one fix."
- [ ] **Shared file state** → "two agents editing the same `.tscn` file race the file write — Godot's editor scene format is not merge-friendly."
- [ ] **Exploratory debugging** → "early-stage investigation where one agent's finding reframes the next agent's prompt — sequential is faster overall; you don't want to launch 5 agents and discard 4 results."
- [ ] **Need full context** → "the work requires the orchestrator to hold the full picture. Parallel agents cannot share intermediate findings without a slow round-trip back to the orchestrator."
- [ ] **One file, multiple concerns** → "a single bug touching one file. Don't split 'fix syntax + fix logic + add test' into three agents — context fragmentation costs more than time saved."

---

## 3. The Dispatch Procedure

*(Manual `Agent` dispatch. For a deterministic, known-up-front fan-out, encode it as a Workflow instead — §0. The single-message / no-`run_in_background` rules below are what a Workflow's `parallel()` gives you for free.)*

For each parallelizable task:

1. **Identify independent domains.** Write down each agent's exact scope and verify it doesn't depend on another agent's output.
2. **Create focused tasks.** Each task should be self-contained — the agent shouldn't need to ask for context mid-flight.
3. **Choose model per agent** (see Model Selection below).
4. **Dispatch in single message.** Multiple `Task` tool uses in ONE message → parallel execution. **Never split across messages** — that serializes them.
5. **No `run_in_background`.** Per `review_agents.md` *Agent Spawn Rules*, all results return together when the slowest agent finishes; polling is wrong.
6. **Wait for ALL results.** Do not partially proceed — incomplete agents may surface findings that change the integration step.
7. **Integrate results.** Merge findings (deduplicate by `file:line`), reconcile contradictions, present the unified view to the user.
8. **Verify with full test suite** if any fixes were applied. Per-domain agent fixes can interact at integration boundaries.

---

## 4. Agent Prompt Structure

Each parallel-dispatched agent prompt must be:

- [ ] **Focused** — one clear deliverable, not a checklist of unrelated tasks.
- [ ] **Self-contained** — all context the agent needs is INLINE. Do not point at "see the code in the repo" — the agent has tools to read it but minimizing surprises improves accuracy. (`archive_agent_task_gotchas.md`: orchestrator pushes context; agents don't pull.)
- [ ] **Specific output format** — JSON findings array OR a structured table OR a one-line verdict. Free-form prose is hard to integrate.
- [ ] **No coordination implied** — the prompt should not reference other agents (*"the AI agent will handle this"*) since order of completion is non-deterministic.

**Template:**

```
You are <role-description>.

CONTEXT:
<inline file contents, schemas, conventions — everything the agent needs>

TASK:
<single specific deliverable>

OUTPUT:
<exact format expected, e.g., "JSON array per orchestrator_action_protocol.md schema">

CONSTRAINTS:
<any hard rules, e.g., "do not modify files", "limit to <scope>">
```

**Cross-reference:** `feedback_inspect_existing_abstractions_first.md` — when scoping per-agent task boundaries, extending an existing 2+ subclass family beats inventing parallel work. The same logic applies to agent task scoping.

---

## 5. Model Selection

This skill is the canonical home of fan-out model selection (the guidance formerly lived in CLAUDE.md §8 *Agent Delegation*, since removed in compression). Evidence base: `archive_agent_task_gotchas.md`.

> Built-in `Explore` has run on Haiku and hallucinated file paths (e.g., `mooyum_milk.tres` in Phase 1e.2). Use `Agent(subagent_type:"general-purpose", model:"sonnet")` for accuracy-critical or cross-codebase work; `Explore` is fine for scoped lookups where a wrong answer is cheap to reject.

Decision tree:

- [ ] **`general-purpose` + `sonnet`** — DEFAULT for accuracy-critical work, cross-codebase reasoning, code review, audit, fix authorship. PP commands (`session_audit`, `review_pr`, `test_skill`) all use this combination.
- [ ] **`general-purpose` + `haiku`** — Validation steps (verify a PASS verdict, double-check a finding's correctness), simple lookups where a wrong answer is cheap to reject and re-prompt.
- [ ] **`general-purpose` + `opus`** — Reserved for the most complex orchestration (e.g., `session_audit_agents.md` design-semantics agent). Costs scale; use when the task genuinely requires deep reasoning.
- [ ] **`Explore`** — **WARNING:** built-in `Explore` runs on Haiku and has hallucinated paths. Use only for scoped lookups where you can cheaply reject a wrong answer. Most PP commands DO NOT use `Explore` — they use `general-purpose` + explicit model selector instead.

**Rule:** When in doubt, choose `general-purpose` + `sonnet`. The cost premium over Haiku is small; the accuracy premium is significant. Note: omitting `model` inherits the main session's model — usually the most expensive option; for fan-outs, set the model explicitly.

---

## 6. The 15-Agent Concurrency Cap

*(Applies to **manual `Agent` dispatch**. A Workflow auto-caps at `min(16, cores−2)` and queues overflow — you pass all items and skip this arithmetic; see §0.)*

**Source:** `review_prs.md`, *Concurrency limit* rule — *"Concurrency limit: 15 agents max per batch."*

Two calculation modes:

### Flat dispatch (simple)

Each agent is one direct subagent with no nested sub-dispatches.

- **Cap applies directly:** ≤15 prompts per batch.
- **Examples:** `/test_skill` adversarial prompts, per-test-category Logic-domain fix-ups, per-PR review status checks, the 3-axis `/session_audit` (3 ≤ 15, well within cap).

### Nested dispatch (compound)

Each "outer" agent spawns its own subagents internally.

- **Cap applies to the multiplied total:** `outer × subagents-per-outer ≤ 15`.
- **Example:** `/review_prs` spawns N review agents, each of which spawns 4-7 subagents. With 6 subagents per review on average, the cap allows `floor(15 / 6) = 2` outer reviews per batch. Larger batches must serialize.

**Rule:** Calculate the multiplied total before dispatching. If the total exceeds 15, split into sequential batches. Don't double-count (treating a flat 12-agent dispatch as if it were nested) and don't under-count (treating a 3-outer × 6-inner = 18 dispatch as if 3 ≤ 15).

---

## 7. Worktree Caveat

PP frequently runs in `.claude/worktrees/<name>/`. **Parallel agents do NOT get isolated worktrees by default** — they all write to the same working tree.

Implications:

- [ ] If two agents edit the same file, the second write wins (or fails on lock).
- [ ] Agents that modify `.tscn` files MUST be partitioned to non-overlapping scenes.
- [ ] If you need genuine isolation (parallel agents mutating overlapping files), pass `isolation: "worktree"` per agent — each gets its own fresh worktree (auto-removed if unchanged) and they CAN run in parallel. Setup costs ~200–500ms + disk per agent; reserve it for write-parallel work, never read-only fan-outs. Each fresh worktree needs the Jmodot submodule re-init.

**Cross-references:**
- `archive_worktree_session_setup.md` (auto-memory) — worktree-init recipe and submodule gotchas.
- `archive_worktree_submodule_gotcha.md` (auto-memory) — Jmodot submodule needs `git submodule update --init --recursive` after every checkout in a worktree.

---

## 8. Verification After Integration

After all agents return:

1. **Deduplicate findings.** Same `file:line` from different agents → keep the more specific one (or the `critical: true` one). Per `orchestrator_action_protocol.md` Step 1 (Merge & Deduplicate).
2. **Reconcile contradictions.** Two agents may disagree on a finding's category or fix. Surface this as a `## Notes` synthesis (orchestrator-only, per the protocol's Step 3) for the user.
3. **Run the broader test suite** if fixes were applied. `/regression_gate` is the canonical full-suite verification (3-tier: silent-skip sentinel + baseline drift + explicit failures).
4. **Do not claim completion before verification.** Per the protocol's `Claims to Refuse` section — *"should work now"*, *"probably passes"*, *"seems to be fixed"* are all unverified. Cite test output or use future-tense honestly.

---

## Cross-references

**Spawn rules (do not duplicate — point to):**
- `.claude/commands/agents/review_agents.md` — Agent Spawn Rules (MANDATORY / PARALLEL / NO POLLING / NO TODOWRITE).

**Canonical PP exemplars:**
- `/review_prs` (*Concurrency limit* + batch protocol) — N review agents in parallel; 15-cap exemplar with nested-concurrency math.
- `/session_audit` Phase 2 — 3 axes via the `review_fanout.js` Workflow engine (§0 Workflow-path exemplar).
- `/test_skill` (`.claude/commands/test_skill.md`) — adversarial flat-dispatch example (sibling Batch B artifact).

**auto-memory (cold tier `archive/`):**
- `archive_agent_task_gotchas.md` — orphan Godot processes after parallel dispatch, no-polling pattern, subagent file-read direction (orchestrator pushes, agents don't pull), Haiku Explore hallucinations.
- `archive_worktree_session_setup.md` — worktree initialization recipe.
- `archive_worktree_submodule_gotcha.md` — Jmodot submodule init after worktree checkout.

**File-based memory:**
- `feedback_inspect_existing_abstractions_first.md` — when scoping per-agent task boundaries; extending an existing 2+ subclass family beats inventing parallel work.

**Orchestrator integration:**
- `.claude/commands/agents/orchestrator_action_protocol.md` — Step 1 Merge & Deduplicate, Step 2 Present Unified Report, Step 3 NOTE Synthesis, Step 4 Execute Actions, Claims to Refuse section.

**Skills:**
- [`debugging`](../debugging/SKILL.md) — Phase 2 (Pattern Analysis) cross-codebase work often parallelizes well; sibling Batch B skill. Decides whether to parallelize investigation.
