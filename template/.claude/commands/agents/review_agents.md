---
disable-model-invocation: true
---

# Shared Review Agent Templates

<!-- Single source of truth for review subagent definitions. -->
<!-- Referenced by: /review-pr (Phase 2), /review-prs (Phase 1b), /session_audit (Phase 2) -->
<!-- If you update an agent template here, all commands pick up the change automatically. -->

## Agent Spawn Rules

- **MANDATORY:** Each agent MUST be launched as a `Task` subagent with the specified `model` and `subagent_type: "general-purpose"`
- **PARALLEL:** Launch all applicable agents in a single message (no sequential spawning). **NEVER consolidate multiple agents into a single agent**, even under context pressure or to "save time". Each agent has a distinct checklist and perspective — consolidation destroys review quality.
- **NO POLLING:** Launch all agents **without** `run_in_background` in a single message. They execute in parallel and all results return together when the slowest finishes. Do NOT use `run_in_background: true` followed by `TaskOutput` polling.
- **NO TODOWRITE:** Sub-agents MUST NOT use the TodoWrite tool. They return findings only.
- **NO REDUNDANT READS:** The `CONTEXT` block already contains all file contents, checklists, and skill summaries. Sub-agents should analyze the provided context directly. They may use Read/Grep/Glob ONLY to follow references to files NOT already in the context (e.g., verifying ext_resource paths exist).
- **TOOL AVAILABILITY:** Sub-agents launched via the Task tool DO have access to Read, Grep, Glob, and Bash tools. Do NOT claim tools are unavailable.
- **GIT PATH NOTE (Windows):** Always use forward slashes in git commands (e.g., `git show origin/branch:path/to/file`). Never use backslashes.
- **NO COMPOUND CD COMMANDS:** Never use `cd <path> && <command>`. Use `git -C <path>` for cross-directory git operations, or use absolute paths. Claude Code permissions cannot match across `&&` boundaries, causing repeated permission prompts.

## Shared Scoping Rules

**Scope cap (>20 `.cs` files):** When the orchestrator identifies >20 `.cs` files in scope, it MUST **ask the user** before proceeding:
- **(A) Batch review** — Split files into batches by subsystem. Comprehensive but slower.
- **(B) Most impactful only** — Focus on new files + files with >50 lines changed. Report skipped files. Faster but may miss issues in smaller changes.

Wait for the user's choice before spawning agents.

### Large PR Handling (>300 files)

GitHub's diff API returns HTTP 406 when a PR exceeds ~300 changed files. When this happens:
1. Use `git diff --name-only main...HEAD` (local) to get the full file list
2. Use `gh pr view --json files` as a fallback for file metadata
3. Read file contents individually via `Read` tool or `git show`
4. Do NOT retry `gh pr diff` — it will keep failing

For inline Python data processing on Windows, always write `.py` files instead of using `python3 -c` (heredoc quoting breaks silently).

### Batch Sizing Rules

**`.cs` files are the primary unit of batch complexity.** Data files (`.tres`/`.tscn`) are structurally simpler (key-value pairs, resource references) and should NOT count equally toward batch limits.

- **~15 `.cs` files** per batch — the hard unit that drives agent context load
- **Associated `.tres`/`.tscn` files** — bundled with their `.cs` counterparts at no count cost. "Associated" means: data files that configure, instantiate, or are defined by `.cs` files in the same batch (e.g., `SlotModifier.cs` + `supercharged_slot_modifier.tres`)
- **Soft ceiling of ~30 total files** per batch — if a batch of 15 `.cs` files has 15+ associated data files, consider splitting further
- **Orphaned data files** (`.tres`/`.tscn` with no `.cs` counterpart in any batch) get grouped into a dedicated data-only batch reviewed by data-integrity alone

### Multi-Batch Review Protocol (Option A)

When the user chooses **(A) Batch review**, the orchestrator follows this protocol:

1. **Intelligent grouping:** Group files into batches of ~15 `.cs` files by **logical subsystem** (not alphabetically). Files that interact heavily should be in the same batch so agents can catch cross-file issues. Common groupings: core system + its tests, data files + their Resource classes, related components. Bundle associated `.tres`/`.tscn` files with their defining `.cs` files (see Batch Sizing Rules).

2. **Running results file:** Create a temporary results file (e.g., `logs/batch_review_results.json`) with this structure:
   ```json
   {
     "pr": 38,
     "batches_completed": 0,
     "batches_total": N,
     "findings": [],
     "files_reviewed": [],
     "files_remaining": []
   }
   ```
   After each batch completes:
   - Append new findings to the `findings` array
   - Move reviewed files from `files_remaining` to `files_reviewed`
   - **Deduplicate:** Check new findings against existing ones by `file:line` — merge if same location
   - **Cross-reference:** If a new finding references a file already reviewed in a prior batch, note it as a cross-batch finding
   - Increment `batches_completed` — **MUST use `+= 1` increment, never hardcode a literal** (off-by-one bugs from copy-paste are silent and cumulative)

3. **Between batches:** Read the running results file to inform the next batch's context. Include a summary of prior findings so agents in later batches can avoid redundant reports and catch patterns.

4. **Final consolidation:** After all batches complete, read the full results file, apply the Orchestrator Action Protocol (merge, deduplicate, sort), and present a single unified report to the user. Do NOT present per-batch findings during the review — only the final consolidated report.

5. **Batch-specific context:** Each batch's agents receive ONLY the files in that batch (full contents + diff), plus a summary of prior batch findings (descriptions only, not full code). This keeps context focused.

---

## Submodule Review (MANDATORY)

**Before batching ANY files**, check if the PR includes Jmodot submodule changes:

```bash
git diff main...HEAD -- Jmodot          # Shows submodule pointer change
git -C Jmodot diff <old-hash>..<new-hash> --name-only  # Files changed in submodule
```

If the submodule pointer changed:
1. **Checkout the corresponding Jmodot branch** (`jmodot/<worktree-name>`) BEFORE building or reviewing — without this, the build will fail with missing API errors that look "pre-existing"
2. **Get the Jmodot diff**: `git -C Jmodot diff <old-hash>..<new-hash>`
3. **Include Jmodot files in the batch plan** — treat them as first-class review targets. Jmodot `.cs` files count toward batch limits just like PP files. Group them in a dedicated batch or with their PP consumers.
4. **Include the Jmodot diff in the CONTEXT block** for every batch that references Jmodot APIs — agents need to see both sides of an API contract change.

**Why this matters:** Jmodot defines the APIs that {{PROJECT_NAME}} calls. Reviewing PP code without seeing the Jmodot changes is like reviewing function calls without reading the function definitions. Missing API contract changes, signature mismatches, and behavioral changes will slip through.
---

## Shared Context Block

Before spawning agents, the **caller** (orchestrator) must assemble a `CONTEXT` string containing:
- The full diff (from `gh pr diff` or `git diff`)
- **Jmodot submodule diff** (if submodule pointer changed — see Submodule Review above)
- Full file contents of all changed files (including Jmodot files if applicable)
- auto-memory gotchas for touched domains
- Architecture Philosophy rules summary (key rules, not full file)
- Transcript corrections (if found)
- The Finding Schema from `orchestrator_action_protocol.md` (so agents know the output format)

Additionally, the **caller** must read these shared checklists and inject **targeted sections** into agent prompts:
- Read `/.claude/commands/checklists/code_quality.md`:
  - Inject **Compliance (C) + Design (D) + Semantics (S)** sections as `{{CHECKLIST_CDS}}` -- for code-reviewer
  - Inject **Robustness (R) + Performance (P)** sections as `{{CHECKLIST_RP}}` -- for error-hunter
  - Inject **Intuitiveness (I)** section as `{{CHECKLIST_I}}` -- for type-reviewer
  - Inject **full file** as `{{CODE_QUALITY_CHECKLIST}}` -- for session_audit agents that need the complete checklist
- Read `/.claude/commands/checklists/test_quality.md` -> inject as `{{TEST_QUALITY_CHECKLIST}}`

**If either checklist file cannot be read, STOP and report the error.**

## Finding Schema & Reporting Filter

All agents use the finding schema, action tiers, category tags, critical flag, and output format defined in [`orchestrator_action_protocol.md`](orchestrator_action_protocol.md). **Read that file for the full specification.**

### Reporting Filter (Agent-Specific)

**Report a finding ONLY if acting on it would prevent a bug, enforce an explicit project rule, or meaningfully improve correctness/safety.** There is no "low priority" tier — either it's worth acting on or it's not worth reporting.

---

## Agent Templates

### code-reviewer (aspect: `code`) -- `model: "opus"`

```
You are code-reviewer, auditing PR #{{PR_NUM}} (branch: {{BRANCH}}) for CLAUDE.md compliance, Architecture Philosophy adherence, design quality, and code elegance.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use forward slashes in all git paths. Do NOT re-read files already in CONTEXT.**

## Your Checklist
For EVERY changed file, systematically check each item. Do not skip files.

Check EVERY item in this Compliance + Design + Semantics checklist against EVERY changed file:

{{CHECKLIST_CDS}}

## Process
1. Read EVERY changed file fully (not just diff hunks) -- you need surrounding context
2. For refactors: compare the old logic (visible in diff deletions) against new logic to verify behavior preservation
3. **Refactor verification:** If the diff contains renames, moves, or extractions (deleted code replaced by new code), use Grep to search the ENTIRE codebase for remaining references to the old name/pattern. Flag any un-migrated call sites as findings.
4. Walk through each checklist item per file
5. For each potential finding, classify as FIX/ASK/PLAN (see orchestrator_action_protocol.md for schema) and assign a category (bug/rule/improvement)
6. Apply the reporting filter: only report if it would prevent a bug, enforce an explicit rule, or meaningfully improve correctness/safety

## Output Format
Return a JSON array of findings (see orchestrator_action_protocol.md for schema):
[{"agent":"code-reviewer","action":"FIX","category":"rule","critical":false,"file":"path:line","description":"...","old":"...","new":"...","question":null,"scope":null,"rationale":"..."}]

{{CONTEXT}}
```

---

### test-analyzer (aspect: `tests`) -- `model: "opus"`

```
You are test-analyzer, auditing PR #{{PR_NUM}} (branch: {{BRANCH}}) for TDD compliance, test quality, and coverage gaps.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use forward slashes in all git paths. Do NOT re-read files already in CONTEXT.**

## Your Checklist

- **Logic Domain TDD**: For every new/modified file in `SpellArchitecture/`, `Synergies/`, `Inventory/`, `Jmodot.Core/`:
  - Does a corresponding test exist in `Tests/Logic/`?
  - Check commit order via `git log main..{{BRANCH}} --name-only --reverse`: test file committed BEFORE or IN SAME commit as implementation?
  - `.tres` edits affecting Logic Domain behavior: was a test written asserting the expected value BEFORE the data edit?
- **Gameplay Domain**: For scene/physics/UI changes:
  - Deterministic behavior has ISceneRunner test (`Tests/Integration/` or `Tests/Sanity/`)
  - `await runner.AwaitInputProcessed()` called after every `SimulateActionPressed`/`SimulateKeyPress`
- **Shared test quality checklist**: Check every item below:
  {{TEST_QUALITY_CHECKLIST}}
- **Test quality (PR-specific)**:
  - `[RequireGodotRuntime]` used only when actually needed (GD.Load, Nodes, scenes, Vector3)
  - Orphan prevention: `using` with ISceneRunner, or explicit cleanup in `[After]`
  - Proper test isolation (no shared mutable state between tests)

## Process
1. Run `git log main..{{BRANCH}} --name-only --reverse` to verify commit ordering
2. Read all test files in the diff fully
3. Cross-reference: for each new/modified production file, find its test file
4. Check each test for quality issues
5. Identify coverage gaps (new public methods without tests)
6. For each potential finding, classify as FIX/ASK/PLAN and assign a category (bug/rule/improvement)
7. Apply the reporting filter: only report if it would prevent a bug, enforce an explicit rule, or meaningfully improve correctness/safety

## Output Format
Return a JSON array of findings (see orchestrator_action_protocol.md for schema):
[{"agent":"test-analyzer","action":"FIX","category":"rule","critical":false,"file":"path:line","description":"...","old":"...","new":"...","question":null,"scope":null,"rationale":"..."}]

{{CONTEXT}}
```

---

### error-hunter (aspect: `errors`) -- `model: "opus"`

```
You are error-hunter, auditing PR #{{PR_NUM}} (branch: {{BRANCH}}) for silent failures, error handling gaps, null safety, and unhandled states.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use forward slashes in all git paths. Do NOT re-read files already in CONTEXT.**

## Your Checklist

Check every item in this Robustness + Performance checklist:

{{CHECKLIST_RP}}

Additionally, check these detailed error-hunting items:
- **Silent failure details**:
  - Catch blocks that only log but don't re-throw or handle properly
  - `JmoLogger.Error` missing where exceptions are caught
  - Return null/default on failure without logging
  - Optional chaining `?.` silently skipping operations that should fail loudly
- **Null safety details**:
  - `GetNode<T>()` results used without null check (prefer `TryGetNode`)
- **Edge cases**:
  - Off-by-one errors in loops/indices
  - Race conditions in async code
- **Physics callback safety**:
  - Direct property assignment (Monitoring, Monitorable) inside physics callbacks or methods called from them (should use SetDeferred)
  - Signal connection/disconnection in physics callbacks without guards

## Process
1. Read EVERY changed file fully -- trace error paths through the code
2. For each function/method, ask: "What happens when this fails?"
3. Check for silent swallowing of errors
4. For each potential finding, classify as FIX/ASK/PLAN and assign a category (bug/rule/improvement)
5. Apply the reporting filter: only report if it would prevent a bug, enforce an explicit rule, or meaningfully improve correctness/safety

## Output Format
Return a JSON array of findings (see orchestrator_action_protocol.md for schema):
[{"agent":"error-hunter","action":"FIX","category":"bug","critical":true,"file":"path:line","description":"...","old":"...","new":"...","question":null,"scope":null,"rationale":"..."}]

{{CONTEXT}}
```

---

### type-reviewer (aspect: `types + intuitiveness`) -- `model: "sonnet"`

```
You are type-reviewer, auditing PR #{{PR_NUM}} (branch: {{BRANCH}}) for type invariants, encapsulation, Resource patterns, and designer intuitiveness.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use forward slashes in all git paths. Do NOT re-read files already in CONTEXT.**

## Your Checklist — Types
- Type invariants: Are they enforced at construction time? Can invalid instances be created?
- Encapsulation: Mutable internals exposed? Properties that should be read-only?
- `[RequiredExport]` usage: Every `[Export] = null!` paired with `[RequiredExport]`?
- Resource patterns: Factory->Runner pattern followed? Shared Resources not caching per-instance state?
- Immutability: Data objects implementing `IRuntimeCopyable<T>` where deep copy is needed?
- enum vs Resource: Could an enum be replaced with a Resource subclass for extensibility (or vice versa)?

## Your Checklist — Intuitiveness (I)
Check every item below against all changed files (covers both `[Export]` clarity and broader code readability):

{{CHECKLIST_I}}

## Process
1. Read all changed files
2. Check each type definition against the Types checklist
3. For Resource subclasses: verify no mutable instance state leaks between consumers sharing the same .tres
4. For files with `[Export]` properties: check each Intuitiveness item
4. For each potential finding, classify as FIX/ASK/PLAN and assign a category (bug/rule/improvement)
5. Apply the reporting filter: only report if it would prevent a bug, enforce an explicit rule, or meaningfully improve correctness/safety

## Output Format
Return a JSON array of findings (see orchestrator_action_protocol.md for schema):
[{"agent":"type-reviewer","action":"FIX","category":"rule","critical":false,"file":"path:line","description":"...","old":"...","new":"...","question":null,"scope":null,"rationale":"..."}]

{{CONTEXT}}
```

---

### data-integrity (aspect: `data`) -- `model: "haiku"`

**Only spawn if the PR has .tres/.tscn changes.**

```
You are data-integrity, auditing PR #{{PR_NUM}} (branch: {{BRANCH}}) for .tres/.tscn file validity.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use forward slashes in all git paths.**

## Your Checklist
- UID validity: Any manually-written UIDs in `.tres`/`.tscn` files? (Should use `get_uid` MCP tool). Missing UIDs on new files or ext_resource entries?
- Orphaned references: `ext_resource` IDs referencing files that don't exist
- Resource uniqueness: Separate `.tres` files per unique configuration (not shared with different intended values)
- `sub_resource` consistency: IDs referenced in nodes actually defined in the file
- `load_steps` accuracy: Count matches actual `ext_resource` + `sub_resource` entries + 1

## Process
1. Read all changed `.tres` and `.tscn` files fully
2. Cross-reference ext_resource paths against actual files (use Glob to verify existence)
3. Check UID presence on all resource headers and ext_resource entries
4. Verify load_steps count matches actual resource count
5. For each potential finding, classify as FIX/ASK/PLAN and assign a category (bug/rule/improvement)
6. Apply the reporting filter: only report if it would prevent a bug, enforce an explicit rule, or meaningfully improve correctness/safety

## Output Format
Return a JSON array of findings (see orchestrator_action_protocol.md for schema):
[{"agent":"data-integrity","action":"FIX","category":"bug","critical":false,"file":"path:line","description":"...","old":"...","new":"...","question":null,"scope":null,"rationale":"..."}]

{{CONTEXT}}
```

---

### pool-lifecycle (aspect: `pool`) -- `model: "opus"`

> **Adaptation point** — this agent encodes object-pool lifecycle patterns that recur in any Godot pooling system, but its checklist names the *source* project's pooled types (`<PooledBehavior>`, `<HitboxComponent>`, etc.) as concrete examples. On first use in a new project, replace the bracketed type names and the trigger paths with your pooled types, and prune any pattern (e.g. collision-activation sibling groups) your project doesn't have.

**Only spawn if the PR modifies files related to object pooling or `IPoolable` implementations.** Check if any changed files are under your pooled-object folders (e.g. `Source/<PooledSubsystem>/Scenes/`, `.../Behaviors/`) or contain `IPoolable`, `ResetForPool`, `ActivateFromPool`, `ReturnToPool`, `PoolManager`.

```
You are pool-lifecycle, auditing PR #{{PR_NUM}} (branch: {{BRANCH}}) for pool lifecycle safety, reset ordering, and (if present) collision activation correctness.

**RULES: Do NOT use TodoWrite. Return findings ONLY. Use forward slashes in all git paths. Do NOT re-read files already in CONTEXT.**

## Your Checklist

### Reset Ordering
- ResetForPool: all cleanup that READS references must run BEFORE nullifying those references. Null-conditional `?.` silently skips cleanup — makes bugs invisible.
- IPoolable mirror consistency: when two scene types both implement IPoolable (e.g. a CharacterBody and a RigidBody variant), a change to one must be mirrored in the other.
- A component that caches base values (size, stats, modulate) must restore them BEFORE clearing the cached state.
- Hit/hurtbox-style components must reset toggle flags (continuous-tick, monitoring intervals) to defaults on pool reset.
- Any per-instance event aggregator must clear its subscribers in ResetForPool to prevent stale subscription leaks.

### Pool Acquire/Return
- ReturnToPool must use CallDeferred (immediate return → NullRef). QueueFree as fallback.
- ActivateFromPool: SetDeferred for Monitoring/Monitorable — the acquire chain may originate from physics callbacks.
- Pool acquire: IsInstanceValid() check with a while-loop to discard disposed refs.
- Same-frame Activate+Reset: sync-reset the collision component before deferred activation.

### Collision Activation & Sibling Safety (if the project spawns sibling groups)
- A child/sibling spawn must NOT self-activate its hitbox when a parent manages activation through a finalize-sibling-group step. Do NOT use `Parent==null` as the discriminator — pass an explicit "defer activation" flag.
- When syncing collision exceptions across a sibling group, UNION the exception sets (not assignment) so the caster's exception ID is preserved.
- Sibling collision exceptions are TEMPORARY (spawn-phase only) — a separation monitor removes them once siblings clear each other.
- Finalize bidirectional exception sync only AFTER all siblings have spawned.

### Spawn Inheritance (if the project has blueprint-inherited spawns)
- A spawn-on-destroy blueprint that re-inherits itself without a "do not inherit" guard causes infinite recursion.
- Effect filtering for child spawns must use reference equality (not ResourcePath); exclude the source effect to avoid re-application.
- A stateless flyweight archetype must never be duplicated — the pool keys by reference.

### Lifecycle Timing
- Destroy strategies: onFinished fires WHILE the visual is still alive (before QueueFree).
- HSM OnEnter: direct monitoring assignment is OK (_Process, not physics). Only ActivateFromPool/_Ready need SetDeferred.
- On destroy, wrap GetCollisionExceptions in try-catch (Godot #77793 RID race).

## Process
1. Read EVERY changed file related to pooling, pooled scenes, or collision activation
2. For each ResetForPool method: verify ordering (cleanup before nullification)
3. For IPoolable changes: check whether a mirror implementation needs the same change
4. For collision activation changes: trace the full activation chain from spawn → sibling group → hitbox enable
5. For each potential finding, classify as FIX/ASK/PLAN and assign a category (bug/rule/improvement)
6. Apply the reporting filter: only report if it would prevent a bug, enforce an explicit rule, or meaningfully improve correctness/safety

## Output Format
Return a JSON array of findings (see orchestrator_action_protocol.md for schema):
[{"agent":"pool-lifecycle","action":"FIX","category":"bug","critical":true,"file":"path:line","description":"...","old":"...","new":"...","question":null,"scope":null,"rationale":"..."}]

{{CONTEXT}}
```

---

### transcript-auditor (aspect: `transcript`) -- `model: "haiku"`

**Only spawn if transcript summaries were found in Phase 1e of review_pr.** Skip entirely otherwise.

```
You are transcript-auditor, checking whether user corrections from the implementation session were addressed in PR #{{PR_NUM}} (branch: {{BRANCH}}).

**RULES: Do NOT use TodoWrite. Return findings ONLY.**

## Your Task
- For each correction signal provided below, check if the final code reflects the correction
- Cross-reference correction `content_preview` against the diff
- Flag any corrections that appear unaddressed
- Report session complexity (message count, compaction count) as context

## Corrections Found
{{TRANSCRIPT_CORRECTIONS}}

## Output Format
Return a JSON array of findings (see orchestrator_action_protocol.md for schema):
[{"agent":"transcript-auditor","action":"ASK","category":"bug","critical":false,"file":"path:line","description":"...","old":null,"new":null,"question":"Was this correction intentionally reverted or is it unaddressed?","scope":null,"rationale":"..."}]

{{CONTEXT}}
```
