---
allowed-tools: Bash(git ls-files:*), Bash(grep:*), Glob, Grep, Read, Write, Workflow, Task
description: Audit a plan for missing scope, weak evidence and design or harness defects
---

# Plan check

Review the written plan before implementation. The findings are advisory; the normal execution approval remains the user's, unless explicit unattended authority applies. Do not enter Plan Mode. Skip a known mechanical change below the project's planning threshold, not a large plan merely because it is metadata.

## Phase 1: Scope & Load

### 1a. Read the plan
Accept inline text or `@<path>`; no argument selects the newest `.claude/plans/*.md`. Missing/malformed input is an error, not an empty review. `--meta`/`--code` overrides detection and is reported.

`PLAN_SHAPE=meta` when every change is harness/docs infrastructure; any production C#/scene/resource/project change makes it `code`. The always-loaded surface is `CLAUDE.md`, `.claude/auto-memory/MEMORY.md`, any `rules/*.md` whose `paths:` glob the plan's own edits would trigger, and a skill `description:`. A plan targeting `prototypes/<slug>/` files, or landing on a `prototype/<slug>` branch, is prototype work (`prototype` skill) — route it to `/prototype`, not this audit; the capital-P `Prototype/` arena-floor subsystem is production code and does not qualify. A plan that only cites `prototypes/<slug>/ANSWER.md` under Constraints is a real plan — audit it fully. Split independent code and meta work. If inseparable, use the Mixed shape: the code mandates plus `plc-instruction-quality` and `plc-doctrine-consistency`; report any unavailable lens as uncovered.

### 1b. Scope coverage
Map every requirement to an implementation and verification step. Check conflicting steps, invented scope, unverified dependencies, stubs/deferrals and closing work. A replacement enumerates the old contract before removal. C# drive closure names the full regression gate; intermediate slices use the scoped verifier. Meta changes name executable, channel, registration and reference checks as applicable. No-tests or unavailable coverage is not green. A `.cs` plan is incomplete if it omits the closing `/regression_gate`, `<summary>` coverage for each new `[Export]`, or (roadmap Part) the closing `/update_roadmap` state flip.

Collect the tier facts `orchestration` §2 *Sizing the width* reads: the file count and whether each file is new, edited or deleted; added types, exports, nodes, commands or concepts; operations git cannot undo; the top-level subsystems touched and any Jmodot/game crossing; replaced contracts and their callers' subsystems; and rule changes in an always-loaded surface. Record the tier with the fact that set it.

### 1c. Domain inference
Infer from the written paths and scope against `reference/memory_domains.md`, not incidental words. Meta uses `Harness/Meta`. A code plan with no identifiable domain reports that limit rather than pretending the domain checks passed.

### 1d. Memory
Search relevant memory facets and ordering hazards. The results seed the memory lens; they do not cap its independent search. Recheck a historical claim before treating absence as regression.

### 1e. Known failure modes
Use `python3 .claude/tools/kfm.py index`, then selected bodies. Never inject the full catalog by default. Only the applicable index/selected evidence belongs in a lens brief.

### 1f. Code surfaces
For code plans, enumerate introduced types and authored fields/parameters/flags. Find existing owners by name and behavior; include relevant callers, scene/resource references and generated abstraction-family rows. Use LSP when available; state search-only gaps otherwise. A new name does not prove a new concern. Two `[GlobalClass]` Resources sharing a simple name collide regardless of namespace.

### 1f-meta. Inbound references
For changed harness rules, paths, anchors and keys, Grep the cited path or key across `.claude/` (commands, skills, rules, hooks, memory, tests) and the Obsidian vault, then inspect active callers and fixtures. A rename must not leave a matcher or test silently on the retired contract. Distinguish intentional policy migration from accidental contradiction.

### 1g. Lens-specific context
Supply the full plan plus the evidence each lens needs, not a shared megabundle of every skill. Code design lenses receive `rules/design_litmus.md` whole, `rules/scene_authoring.md` §Scene anatomy, the matching family rows, and for pattern-fit `architecture_philosophy/SKILL.md` with its `structure_rules.md`, because the mandates forbid re-loading them; test readiness receives the TDD contract. Meta instruction quality receives its rubric; doctrine consistency reads the actual changed owners. Inject facts (surfaces, sibling counts, structure), never conclusions. Label inherited claims unverified until checked.

## Phase 2: Launch Plan-Check Sub-Agents

Run the seats with explicit model, effort and agent profile through the existing engines. `orchestration` owns role/transport/currency selection. One arm per seat; extra providers are not automatic work. Same-task comparisons require an explicit comparison request and budget.

Every mandate of the shape always runs. The tier from 1b picks the seat map: Wide runs one seat per mandate, and Standard and Small follow this table. The Small column is provisional.

| Shape | Mandates | Standard seats | Small |
|---|---|---|---|
| Code | `plc-memory-alignment`, `plc-pattern-fit`, `plc-architecture-quality`, `plc-test-readiness`, `plc-evidence-grounding` | one seat per mandate (the width replay's merged design + tests + grounding map recovered 31% of baseline findings against a 34% bar) | one seat |
| Meta | `plc-memory-alignment`, `plc-instruction-quality`, `plc-doctrine-consistency`, `plc-evidence-grounding` | text (`plc-instruction-quality` + `plc-doctrine-consistency`) and grounding (`plc-evidence-grounding` + `plc-memory-alignment`) | one seat |
| Mixed | `plc-memory-alignment`, `plc-pattern-fit`, `plc-architecture-quality`, `plc-test-readiness`, `plc-evidence-grounding`, `plc-instruction-quality`, `plc-doctrine-consistency` | one seat per code mandate plus the meta text seat (`plc-instruction-quality` + `plc-doctrine-consistency`) | one seat |

Add a lens only for a distinct uncovered risk. Judgment needs a qualified review role. Effort follows the plan-check tier row in the ladder.

Fetch mandates with `python3 .claude/tools/lens.py get --shared <keys>`. Do not load the whole mandate catalog. Put shared context and each seat's prompt in files; a merged seat's prompt carries each of its mandates and the §2 per-mandate coverage rule.

Invoking `/plan_check` is this fan-out's Workflow authorization (`orchestration` §0). Native route: `review_fanout.js` under `.claude/workflows/`, with `args.agents=[{key,promptPath,model,effort,agentType}]`, `contextPrefixPath`, and run-specific `spillDir`. Off-transport: the registered sidecar owner with the canonical `.claude/schemas/review_findings.json`. Keep the same finding/coverage contract across routes. Do not substitute an unpinned Agent call.

Lenses are read-only except their evidence artifacts. Tests, builds and shared LSP operations remain serialized by the parent; independent source inspection is required. Briefs carry constraints, exclusions and done-conditions. A profile without Write cannot promise a spill file. Recover incomplete runs before any fresh dispatch; failed coverage is not a clean review.

## Phase 3: Consolidate & Report

Findings return bounded by default; retrieve the complete set with `python3 .claude/tools/session_digest.py --workflow-dir <transcriptDir> --workflow-kind review --workflow-manifest`, then `--workflow-select <ID>`, `--workflow-page {items,lenses,reports,gaps,merges} --page <N> --page-size <N>` or `--workflow-full`, each with `--expect-journal-sha256 <hash from the manifest>`, before counting findings or issuing a verdict. A bounded result's `delivery.criticalFindingIds` and `delivery.askFindingIds` are complete even when previews are shed, so critical/ASK accounting may start from them; the full retrieval is still required before a verdict.

Preserve every original finding and source ID. Group only as a view; never discard distinct findings because they share a location. Show received/grouped counts and uncovered lenses. Verify consequential source claims first-party and resolve contradictions against evidence, not votes.

Follow the action protocol in [`agents/orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md): critical first, then `FIX|ASK|PLAN`, then `bug|rule|improvement`. Report the shape, the tier with the fact that set it, the seat map, per-mandate coverage with UNCOVERED mandates, per-seat yield from `orchestration_metrics.py --run <runId>`, scope gaps, verified findings and the artifact path briefly. Record one verdict row per seat in the `mandates` shape (`/orchestration_metrics` *Incremental rating*). Account for every finding before acceptance.

| Verdict | Meaning |
|---|---|
| APPROVE | No critical or orphan requirement; at most two noncritical findings |
| APPROVE WITH NOTES | No critical or orphan requirement; remaining findings have clear dispositions |
| REVISE PLAN | Critical, orphan scope, missing required test-first/integration verification, or unresolved design fork |

A Logic change without a failing-test step is critical. Deterministic Gameplay behavior needs integration verification; only subjective feel is manual. Meta reviews use harness verification, not unrelated Godot rules.

Revise critical architecture findings and reconverge. At most two dispatched rounds; round two sends each seat only its changed or uncovered mandates, and its briefs carry round one's first-party-verified evidence quotes as trusted rather than re-deriving them. Verify mechanical folds inline; dispatch again only for new design or unresolved evidence. After two divergent rounds, surface the real fork rather than keep spending.

## Phase 4: Execute Actions

Apply agreed plan corrections, not production edits. Resolve `ASK` decisions with the user or the explicit unattended policy. Do not add a second approval ritual when the calling drive already supplies authority. Review completion is not implementation completion.

## Constraints

- Do not review a plan while implementing it; landed work otherwise masquerades as a false premise.
- Missing/invalid lenses remain uncovered. An invocation or process exit is not verification.
- Keep existing safety, ownership and permission boundaries while simplifying procedure.
- An empirical refutation without verbatim tool output is discarded.
- Phase 1b DoD/stub findings and `plc-test-readiness` are detect-and-report, never auto-applicable `old`/`new` edits: test content and Definition-of-Done are scope decisions a downstream auto-apply loop (`/part_drive`) must not fold silently.
- Replacements enumerate their old public/behavioral surface in the plan; `/session_audit` Phase 1.5 verifies reproduction as a MERGE-BLOCKER.
