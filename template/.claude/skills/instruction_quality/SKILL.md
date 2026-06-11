---
description: >-
  Auto-load when reviewing or refining skill descriptions, command procedures, hooks,
  or always-loaded guidance. Triggers: "audit this skill", "is this description good",
  "review CLAUDE.md", "instruction quality", "trigger words", "skill description",
  "audit the hooks", "hook quality". Does NOT measure trigger accuracy (use
  `skill-creator`) or adversarial pressure (use `/test_skill`).
---

# Instruction Quality Principles

Codifies what makes skill descriptions, command procedures, and CLAUDE.md sections instruction-followable. Derived from Anthropic's CLAUDE.md guide (https://code.claude.com/docs/llms.txt) plus inaugural-run findings from the 2026-05-03 CLAUDE.md compression.

## Universal principles (skills, commands, CLAUDE.md)

### 1. Specificity over abstraction

Concrete instructions get followed; abstract ones drift.

**Audit checks:**
*   Description / opening prose names file types, paths, domains, or trigger phrases?
*   Procedure steps are verifiable (`run X and confirm Y`) not vague (`review thoroughly`)?
*   Each abstract headline has at least one inline litmus test?

**Failure mode example:** Skill description `Use when working with code` (matches everything) vs `Use when writing or refactoring .cs files in Tests/Logic/` (matches the actual trigger).

### 2. Internal consistency

Within a single artifact, no two statements should recommend opposing actions on the same decision.

**Audit checks:**
*   Does a table's "avoid" column contradict prose elsewhere in the file?
*   Do `do` rules conflict with `don't` rules? (Three layers of override on one decision = collapse to a single decision tree.)
*   Does a skill description say `auto-fires on X` while the body says `manual invocation only`?

**Canonical example:** 2026-05-03 CLAUDE.md's Memory-section forbidden-keyword list listed `refactor` and `MCP` as broad-and-banned, while the Proactive Context Loading table listed both as recommended primary search terms.

### 3. Single source of truth

When two artifacts cover the same rule, they drift. Pick canonical home; others reference it.

**Audit checks:**
*   Does the artifact restate content that lives canonically elsewhere (skill, memory file, CLAUDE.md, command)?
*   Cross-reference instead of restatement?

**Inaugural example:** Build & Test Commands restated testing-skill content; Hybrid TDD restated testing-skill workflow. Both reduced to cross-references.

**Auto-loaded-context duplication (skill-specific failure mode):** SKILL bodies that list companion rule files (which auto-load via `paths:` frontmatter on `.claude/rules/*.md`) or sister skills (already covered by the description's `SKIP` clauses) duplicate what the harness loader already surfaces. The loader is the canonical source; the SKILL body listing is redundancy that drifts on rename/move. Carve-out: inline a one-liner *fact* from a rule if it's load-bearing at planning time (before any file under the rule's `paths:` glob would naturally be read). List the fact, not the rule file's existence.

### 4. Cross-reference durability

References that name line numbers, section titles, or moved content silently rot when the target changes.

**Audit checks:**
*   Skill/command cites a CLAUDE.md section by number — does that section still exist?
*   References a file or section that has been renamed, moved, or merged into another artifact?
*   `see line 42 of X` — almost always rot-prone; prefer named anchors or cross-link.
*   References include `lines N-M` anchors? Those silently rot on any insertion/compression upstream. Prefer named anchors (`§3 *Obsidian (The Design Source)* tiebreaker`) or direct file links (`.claude/rules/csharp_lsp.md`). Found 3 high-severity instances during the 2026-05-03 post-reorg audit.
*   Quotes the source verbatim? Verify the quote is still verbatim — compression often paraphrases. If the quote diverges, either re-quote current wording, paraphrase the cite, or own the wording without the quote. (Inaugural example: `brainstorming/SKILL.md` claimed "verbatim from CLAUDE.md §3" but the source was paraphrased post-compression.)
*   **Cheap outbound checks are first-class, not a punt:** every cited file path (`agents/foo.md`, `.claude/rules/bar.md`, `gotcha_*.md`) resolves on disk? every cited `§N` / named anchor still exists in the target? every named skill/command still registered? Each is one mechanical `Glob`/`Grep` and must run on every audit. Only *semantic* outbound rot (a cite whose meaning drifted while the path still resolves) is the harder manual pass.
*   **Inbound-reference rot (auditing a rule/skill/command target):** when the target was revised, do its *consumers* (test fixtures, hook comments, sibling skills citing it as exemplar) still encode the pre-revision behavior? Test fixtures are the highest-risk class — a stale fixture rewards the obsolete agent behavior and penalizes the corrected one. Discovered 2026-05-03 auditing `.claude/rules/csharp_lsp.md`: the rule file's own test fixture (`tests/routing_compliance.md` Test C1) expected `LSP workspaceSymbol(query=...)` — a call shape the rule file's schema-quirks block explicitly documents as impossible. Run `Grep("<target-name>")` across `.claude/{tests,hooks,skills,commands}/` and verify each hit reflects the *current* revision.

### 5. Size proportional to load mode

The 200-line target is for **always-loaded** content (CLAUDE.md, auto-memory's `MEMORY.md`). Conditionally-loaded artifacts (skills, commands, rules) have softer thresholds.

**Audit checks:**
*   Skill >800 lines? Could it split by sub-domain or path-scope?
*   Command >300 lines? Procedure may benefit from extraction into a sub-command or skill.
*   CLAUDE.md >220 lines? Run `/claudemd_compact`.

**Don't:** reflexively apply the 200-line cap to skills or commands. They're not always-loaded; the cap doesn't transfer.

### 6. Conciseness — no editorial padding

Every sentence should change behavior. Hedge text, narrative restatements of structured data, and "as you can see" framing add bytes without changing what gets followed. Distinct from §1 (a sentence can be specific yet padded) and §3 (a sentence can be SSOT-clean yet wordy).

**Audit checks:**
*   Hedge text that lampshades narrowness or importance ("(NARROW — read carefully)", "this is critical", "importantly") — content should narrow itself; lampshading is filler.
*   Dense paragraphs of NEVERs/ALWAYS that scan as one wall when they could be a short bullet list.
*   Editorial sentences inside table cells (the cell says WHAT to do; WHY belongs in surrounding prose or a footnote).
*   Narrative restatement of a table immediately above or below that same table.
*   A summary that has its own summary — when you find this, the middle layer is the verbosity.
*   **Provenance / design-rationale residue** — text explaining *why the artifact has its shape* or *what debate produced it* ("created after back-and-forth over structure", "no new engine", a *"What this is — and is NOT"* preamble that only restates the positive scope). The executing agent needs the contract, not its history. Cut unless the rationale changes a runtime decision.
*   **Negative scope — keep only the misfire-preventing kind.** "Does NOT do X" / "not a `Workflow`" / `SKIP when` earns its place ONLY when it preempts a *real, tempting* wrong action — and then state it ONCE, at temptation-time (usually one Anti-patterns row), not also as a framing paragraph. Contrast-to-sound-thorough is padding. Litmus: *does this line prevent a concrete wrong action or change a runtime decision? Keep once — else cut.* (This is the cut side of §7's SKIP-clause discipline: §7 wants the misfire-preventing negative; §6 cuts the rest.)

**Composition with §5:** Conciseness gates hardest on always-loaded content (CLAUDE.md, auto-memory's `MEMORY.md`) where every byte costs every session. Conditionally-loaded artifacts (skills, commands, rules) get softer gating — readability and trigger-phrase coverage outrank byte count.

**Don't:**
*   Don't compress dense lookup tables. They scan well at format level; byte:information ratio is already minimal. (Mirrors `/claudemd_compact`'s "Don'ts" — preserved here as a universal because the temptation generalizes.)
*   Don't compress decision-gate prose past the point of clarity. Specificity (§1) outranks conciseness — a 220-line specific CLAUDE.md beats a 180-line vague one.
*   Don't reflexively shorten skill descriptions below the §7 trigger-phrase floor. Compression at the description level can starve the matcher.

**Inaugural example (2026-05-03 §9 Tool Routing audit):** three layers of the same rule (top callout → §9 callout → §9 table); the middle layer was paraphrase. Removing it and bullet-restructuring the top callout improved scannability without losing content. Pattern: *when a summary has its own summary, the inner one is the verbosity.*

## Skill-specific principles

### 7. Description-as-trigger discipline

Skill descriptions ARE the trigger mechanism. Vague descriptions trigger inconsistently; over-specific descriptions miss legitimate cases; over-long descriptions confuse the matcher.

**Audit checks:**
*   Description length 50–500 chars AND within ~150% of the peer-skill median word count in the same `.claude/skills/` directory? (`/instruction_audit` Phase B computes the char count and the sibling median — don't eyeball it. Description is always-loaded cost paid every session; anything larger than stated here must have strong justification.)
*   Imperative trigger guidance (`Use when X` / `Use BEFORE Y` / `SKIP for Z`)?
*   `SKIP` / `DO NOT USE` clauses present where the skill could falsely fire?
*   Concrete trigger phrases that mirror real user prompts?

### 8. Frontmatter convention

Skills use `description: >-` multi-line block scalar; commands use single-line description (or none). Mixing produces parser inconsistencies.

## Command-specific principles

### 9. Idempotency / no-op gate

A command invoked when its work is already done should detect that and exit, not redo work.

**Audit checks:**
*   Command measures current state before acting?
*   Has clear "no action needed" branch?

**Inaugural example:** `/claudemd_compact` exits if CLAUDE.md ≤200 lines.

### 10. Procedure verifiability

Each step in a command's procedure should be either: (a) a concrete tool invocation with arguments, or (b) a delegation to a named sub-procedure. `Review thoroughly` is not a procedure step.

**Audit checks:**
*   Each step starts with a verb + object that another agent could execute cold?
*   Sub-procedures (Phase A / B / C / etc.) clearly delineated?
*   Verification step exists at the end?

### 11. Argument handling

Commands taking arguments should specify: (a) expected format, (b) what happens with no argument, (c) what happens with malformed argument.

## Orchestration-specific principles (artifacts that invoke Workflow / Agent / scripts)

These apply to any skill or command whose procedure dispatches subagents — a `Workflow({scriptPath})`, parallel `Agent()` calls, or a fan-out engine like `review_fanout`. A target can pass §1–§11 cleanly (specific, consistent, well-sized prose) and still fail at runtime because its *orchestration contract* is wrong. This class is invisible to a prose-only audit, so it gets its own principle.

### 12. Orchestration-contract & known-failure-mode integrity

**Audit checks:**
*   **Invocation shape matches the invoked artifact's contract.** If the target calls `Workflow({scriptPath: X, args})`, read X's arg parse-guard (e.g. `review_fanout.js` destructures `args.agents = [{key, prompt, model?}]` + `args.contextPrefix`). Does the target build exactly that shape? A mismatched/missing key makes the script no-op or error — it does not warn.
*   **Output contract matches.** If the engine enforces a schema (e.g. `review_fanout`'s `FINDINGS_SCHEMA` requires `agent, action, category, description, rationale`), does the target's mandate ask agents to produce those fields, using only allowed enum values (`action ∈ FIX/ASK/PLAN`, `category ∈ bug/rule/improvement`)?
*   **Screened against memorialized Workflow/agent gotchas.** Semantic-search `.claude/auto-memory` for workflow/agent failure modes and confirm the design doesn't walk into one. The load-bearing ones:
    *   `gotcha_workflow_args_generation_fidelity` — large × nested × escape-dense `args` (many long agent prompts, verbatim code/quotes) makes the *model* emit malformed JSON → 4ms/0-agent/0-byte death. Mitigation: push shared bulk via one `contextPrefix` string, keep per-agent prompts lean, chunk across calls, or run the heaviest single payload as a standalone `Agent()`.
    *   `gotcha_workflow_fanout_search_false_absence` — fanned agents' `Grep`/`Glob` return intermittent empties read as false "absence." Mitigation: push-don't-pull (Claude gathers via `git`, embeds in the prompt; agents compare, never discover).
    *   `gotcha_workflow_single_flight_concurrency` — never fan out GdUnit4 tests or csharp-ls LSP across `parallel()` agents; the engine guard reaches only the top-level agent.
*   **Known-failure-mode citation present.** When the design matches a gotcha's shape, the procedure cites the gotcha AND prescribes its mitigation + recovery (e.g. "4ms death = malformed args, not a broken tool") — so a cold executor doesn't re-discover the failure.
*   **Schema-loss trade acknowledged when substituting `Agent()` for a workflow.** The `Agent` tool has no `schema` param; dropping a workflow for parallel `Agent()` trades schema-enforced output for prompt-and-parse. Fine when args-fidelity risk dominates (prose comparison), but the trade should be deliberate, not accidental.

**Failure mode example:** a pre-PR battery that embeds verbatim OLD/NEW `.cs` into four nested `Workflow` agent prompts — structurally clean against §1–§11, but dies at 4ms on the first large diff because the `args` blob exceeds the generation-fidelity envelope.

## Hook-specific principles (`.claude/hooks/` + settings.json wiring)

Event hooks are code, not prose — §1–§6 apply to their docstrings, but the failure modes that decide whether a hook *works* are mechanical. Verify against the live `settings.json` and the official hooks docs (code.claude.com/docs/en/hooks), never from memory; channel semantics have drifted across Claude Code versions. Canon for the verified channel matrix: `archive_hook_gotchas.md` in auto-memory.

### 13. Channel-contract validity

A hook's output matters only if its channel is model-visible for its event. Per the verified matrix: stdout→model only for UserPromptSubmit/SessionStart (exit 0); stderr→model only on exit 2; `hookSpecificOutput.additionalContext` JSON (stdout, exit 0) is the ONLY model-visible advisory channel on PreToolUse/PostToolUse; `systemMessage` reaches the user, not the model.

**Audit checks:**
*   Every message intended for the MODEL uses a model-visible channel for its event? stderr+exit-0 on PreToolUse/PostToolUse is a dead channel — the advisory silently does nothing (2026-06-09 audit found two hooks writing nudges there).
*   Docstring claims about visibility match the matrix? ("stderr surfaces in the tool result frame" on an exit-0 path = rot.)
*   Block paths use exit 2 + stderr; permission decisions use `permissionDecision` JSON. A PreToolUse `allow` approves the ENTIRE command string — auto-approve hooks must validate everything the call will execute, not just its prefix.

### 14. Registration & liveness

**Audit checks:**
*   Every event-hook file in `.claude/hooks/` is registered in settings.json, OR is a library/CLI with an identified consumer (grep `.claude/` for importers/invokers before calling a file dead)?
*   No orphan artifacts: `__pycache__` entries without source, log files no live hook writes, settings.json entries pointing at deleted files (exit-2 'file not found' bricks every matched tool — Hook_Gotchas).
*   Temporary diagnostics state an expiry condition AND haven't outlived it — grep hooks for "Remove after" / "Remove once" and check whether the condition is now met.

### 15. Bounded state & cost

Hooks fire per-prompt or per-tool-call; costs multiply invisibly. Measure on disk, don't eyeball (the 2026-06-09 audit found an 8.4 GB backup dir and a 24.5 MB expired-diagnostic log).

**Audit checks:**
*   Every append-only log/state file has rotation, a stale-sweep, or a size cap?
*   Per-call matchers stay subprocess-light: each registered command = one interpreter spawn per matched call; N hooks on one matcher = N spawns per call. Hooks sharing libraries/state are consolidation candidates (one dispatcher per event also removes write-race ordering hazards).
*   Repeated injected context is deduped per session/turn when repetition changes no behavior (`critical_analysis_reminder` precedent: ~25K tokens/session before its dedupe).
*   Internal subprocess timeout budget fits inside the hook's settings.json timeout.

### 16. Fail posture & docstring/behavior parity

**Audit checks:**
*   Fail-open vs fail-closed is deliberate: enforcement gates may fail closed; advisory/context hooks must fail open (exit 0, silent).
*   Shared state across hooks on one matcher: atomic writes (temp+rename), writer-first ordering in one matcher block (`gotcha_posttooluse_hook_read_after_write_ordering`).
*   The docstring advertises only checks that are live — commented-out patterns still listed in the header are §2 drift in code form.
*   Hardcoded environment facts (plugin versions, install paths, engine versions) are flagged for silent-breakage on upgrade.

## Reference-content principles (artifacts that assert codebase or harness facts)

Procedures and reference skills assert facts beyond their own prose: codebase claims (paths, type/member names, test-tree locations, data-file conventions) and harness claims (tool contracts, concurrency caps, model behavior, "the harness can't X"). These rot silently — the prose stays internally consistent while the world moves. Distinct from §4: that checks *citations* (links, anchors, quotes); this checks *asserted facts* that cite nothing.

### 17. Claim freshness

**Audit checks:**
*   Sample the target's load-bearing codebase claims (up to ~10 — prefer paths a procedure writes to, types it instantiates, conventions it propagates) and verify each with one Glob/Grep/LSP call. A reference skill that names APIs is making testable claims — test them. Empty Glob/Grep ≠ absence; confirm with `ls` before flagging (`gotcha_grep_glob_miss_tracked_files.md`).
*   Harness-behavior claims (tool params, agent caps, model defaults, capability negations) — verify against the CURRENT tool schema before trusting. A claim that the harness *can't* do something deserves a re-check on every audit; capabilities are added between versions.
*   The checks are mechanical and independent — for multi-file audits, delegate to parallel read-only agents (`parallel_agents` skill); synthesis stays in the orchestrator.

**Inaugural examples (2026-06-09 harness audit):** `spell_authoring` routed new TDD tests to `Tests/Unit/` (tree no longer exists — suites would silently miss the regression gate's `Tests/Logic` filter); archetype convention cited `.res` (dead — `.tres` is live); `ISpellVisuals` (interface never shipped; it's `ISpellBodyVisuals`); `parallel_agents` prescribed *sequential* dispatch for worktree isolation after the `Agent` tool had gained parallel `isolation: "worktree"`.

## Anti-patterns

*   **Don't audit trigger accuracy with this checklist.** That's `skill-creator`'s eval — it has actual measurement tooling.
*   **Don't audit adversarial resistance with this checklist.** That's `/test_skill`.
*   **Don't apply size principles to path-scoped rule files.** Their size is amortized across only the sessions that load them.
*   **Don't reflexively shrink content to hit numerical targets.** The targets are heuristics, not gates. A 220-line CLAUDE.md with high specificity beats a 180-line one with vague headlines.
*   **Don't run audits on actively-edited targets.** Diff churn produces false positives. Wait until edits land.

## Composition with other tools

| Concern | Tool | Method |
|---|---|---|
| Static structural quality | `/instruction_audit` | Read file, classify against this checklist |
| Hook mechanical validity | `/instruction_audit` on a `.claude/hooks/` file | §13–§16 + settings.json wiring + on-disk state measurement |
| Claim freshness (code/harness facts) | `/instruction_audit` claim-freshness phase | Sample ≤10 concrete claims → Glob/Grep/tool-schema verify (delegable to parallel agents) |
| Cross-surface rule consistency | `/rule_consistency` | Gather a rule's statements across all surfaces, fan out comparators, flag drift |
| Trigger accuracy (skill description) | `anthropic-skills:skill-creator` | Run prompt evals |
| Adversarial content resistance | `/test_skill` | Spawn adversarial subagents |
| CLAUDE.md size + compression | `/claudemd_compact` | Phased measure → audit → plan → execute |
| Auto-memory file cleanup | `anthropic-skills:consolidate-memory` | Reflective pass over memory files |

These are non-overlapping; different failure modes.
