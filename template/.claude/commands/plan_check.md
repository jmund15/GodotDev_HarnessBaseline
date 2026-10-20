---
allowed-tools: Bash(git status:*), Bash(python3 .claude/tools/extract_numbers.py:*), Bash(python3 .claude/tools/restate_scan.py:*), Bash(python3 .claude/tools/lens_briefs.py:*), Bash(python3 .claude/tools/sidecar_fanout.py:*), Bash(python3 .claude/tools/fold_check.py:*), Bash(git ls-files:*), Bash(grep:*), Bash(python3 .claude/tools/review_ledger.py:*), Bash(python3 .claude/tools/post_check.py:*), Glob, Grep, Read, Write, Workflow
description: Audit a written plan for scope, evidence and design defects
---

# Plan check

Review the written plan before implementation. The findings are advisory; the normal execution approval remains the user's, unless explicit unattended authority applies. Run it only when plan check is admitted (`execution_depth.md` §Admission table); a large plan is not exempt merely because it is metadata.

## Phase 1: Scope & Load

### 1a. Read the plan
The parent accepts inline text or one or more `@<path>`s and selects the newest `.claude/plans/*.md` when no argument is supplied. For several paths, prepare one shared seat map and one fold subject per plan; the engine folds only subjects receiving routed findings. The parent rejects the whole invocation if any path is missing or malformed. The parent applies and reports `--meta`/`--code` overrides; reject an override with several paths.

`PLAN_SHAPE=meta` when every change is harness/docs infrastructure; any production code, scene, resource or project-data change makes it `code`. The always-loaded surface is `CLAUDE.md`, `.claude/auto-memory/MEMORY.md`, any `rules/*.md` whose `paths:` glob the plan's own edits would trigger, and a skill `description:`. A plan for throwaway prototype work, at the paths or branch the project's prototype route names, goes to that route, not this audit; production code whose name merely resembles it does not qualify. A plan that only cites a prototype's answer under Constraints is a real plan — audit it fully. A plan spanning both shapes partitions per [`plan_file_format.md`](../skills/_brainstorm_shared/plan_file_format.md) §One shape per plan file, and the split plans audit in one invocation. If inseparable, use the Mixed shape: the code mandates plus `plc-instruction-quality` and `plc-doctrine-consistency`; report any unavailable lens as uncovered.

When plan check is not admitted, the plan executes from the short plan `execution_depth.md` §Admission table defines. Convergence then rests on the validation pass plus the `/explore` dossier, and the approval presentation states that plan check was skipped and what replaced it. A skip never satisfies convergence by itself. When plan check is admitted, every mandatory lens runs whatever the plan's size, because the Plan check row of `execution_depth.md` §Admission table decides the audit.

### 1b. Scope coverage
Map every requirement to an implementation and verification step. Check conflicting steps, invented scope, unverified dependencies, stubs/deferrals and closing work. A replacement enumerates the old contract before removal. C# drive closure names the full regression gate. A slice runs only its items' targeted tests; the scoped verifier runs once at Part close, by the dispatcher. A wider run (scoped verifier, bound proofs, the gate) ordered per slice or inside an executor's steps is a scope gap (`change_control` §Gate cadence). Meta changes name executable, channel, registration and reference checks as applicable. No-tests or unavailable coverage is not green. A `.cs` plan is incomplete if it omits the closing regression gate (`change_control` §Gate cadence names it), `<summary>` coverage for each new `[Export]`, or (roadmap Part) the closing `/update_roadmap` state flip.

Run `git status --short` over the plan's write set before seats launch. Each dirty path another session owns is resolved before the audit by `environment_bootstrap` §Concurrent-session shared-tree etiquette item 7, and the plan states the outcome. An unresolved one is a scope gap, never a reason to widen the panel.

Read reach, reversibility, size and the sensitivity-tag matches from the task record's triage (`task_record.py block`). Collect only the facts the record lacks that the mandate admission rules read (`execution_depth.md` §Recording the triage): the file count and whether each file is new, edited or deleted; added types, exports, nodes, commands or concepts; the top-level subsystems touched; and replaced contracts and their callers' subsystems. Also note the design path the plan cites and whether the plan holds a new decision the Plan check row of `execution_depth.md` §Admission table names (a new type, export, authored surface, family or rule); Phase 2's `plan` call reads both. Before any seat, confirm each upstream element the triage admits (`execution_depth.md` §Admission table: Grounding, Design, Design challenge) left an artifact the plan cites: the `/explore` dossier, the design (a design doc or the entry command's Design capture), the red-team verdict. A missing one sends the plan back to that element's owner in the Admission table, and the report names it; plan check spends no seat on design no upstream element produced.

### 1c. Domains and memory
Reuse the `/explore` dossier's domains and memory claims as the memory seat's seed when the plan cites a dossier; otherwise infer domains from the written paths and scope against `reference/memory_domains.md`, not incidental words, and search the relevant memory facets and ordering hazards. State which route you took. Recheck a historical claim before treating absence as regression. Meta uses `Harness/Meta`. A code plan with no identifiable domain reports that limit rather than pretending the domain checks passed.

### 1e. Known failure modes
`plc-memory-alignment` declares `kfm: index`, so `lens_briefs.py` materializes and checks the snapshot and names its index in that seat's brief. The seat Reads only selected body files; the parent may still use `kfm.py get` for its own focused check. Refresh a stale snapshot before consuming the review, never inject the full catalog.

### 1f–1g. Code surfaces, inbound references and lens context
Read [`plan_check_detail.md`](reference/plan_check_detail.md) §Phase 1 context before the seats launch: §1f for a code plan (introduced types and authored fields against their existing owners), §1f-meta for a meta plan (the `INBOUND_REFS` sweep that `lens_briefs.py` produces) and §1g for every plan (the evidence each seat receives, and the `extract_numbers.py` and `restate_scan.py` outputs a meta or mixed plan passes to its text seats).

## Phase 2: Launch Plan-Check Sub-Agents

Run the seats with explicit model, effort and agent profile through the existing engines. `orchestration` owns role/transport/currency selection. One arm per seat, because the engine keys coverage per seat (`args.agents`); extra providers are not automatic work. Same-task comparisons require an explicit comparison request and budget.

Every admitted mandate of the shape runs. Width and depth come from the triage 1b read and the drive's rigor: `orchestration` §2 *Sizing the width* (size and room rules) and §5 *Effort*. A merge follows the order in the table below, and the report states the rigor, the seats and why. A merged seat returns separate evidence for each mandate it carries.

| Shape | Mandates | Merge order |
|---|---|---|
| Code | `plc-memory-alignment`, `plc-pattern-fit`, `plc-architecture-quality`, `plc-test-readiness`, `plc-evidence-grounding` | 1. `plc-pattern-fit` + `plc-evidence-grounding`; 2. that seat + `plc-architecture-quality` |
| Meta | `plc-memory-alignment`, `plc-instruction-quality`, `plc-doctrine-consistency`, `plc-evidence-grounding` | 1. text (`plc-instruction-quality` + `plc-doctrine-consistency`); 2. grounding (`plc-evidence-grounding` + `plc-memory-alignment`) |
| Mixed | `plc-memory-alignment`, `plc-pattern-fit`, `plc-architecture-quality`, `plc-test-readiness`, `plc-evidence-grounding`, `plc-instruction-quality`, `plc-doctrine-consistency` | 1. the Code merge 1 and the Meta text seat; 2. the Code merge 2 |

Split plans get one seat map. Classify each plan's shape; use the Mixed row when their shapes differ, else that shape's row. A shape-only mandate's brief names only its shape's plans; `plc-memory-alignment` and `plc-evidence-grounding` review every plan. Every finding's `file` begins with its plan's path.

Re-rank the order when the panel-yield lines of `/orchestration_metrics` flag a merged mandate or a solo seat with no unique finding.

Add a lens only for a distinct uncovered risk (`orchestration` §0). Judgment needs a qualified review role. Pin each seat to its lenses' `seat_tier` (`lens.py`) at that tier's review rung's lowest value; `orchestration` §5 *Effort* owns a raise.

Before the panel, write the seats to `.claude/scratch/plan_check_<sid8>_roster.json` (`{key, mandates, tier, effort}`, where `mandates` is each lens's `mandates:` line) and run `python3 .claude/tools/review_ledger.py plan --stage plan-check --subject plan:<path> --roster <roster.json> --files <reviewed-plan-paths> --design <design path>`; add `--no-new-decision` when the §1b facts show no new decision. Seat only the mandates it returns: a design mandate covered at the unchanged design hash is not re-judged, and that plan gets a spot-check instead: `plc-evidence-grounding`, `plc-test-readiness` and `plc-memory-alignment` only, sized by what the plan adds beyond the covered design (units the design does not state), per `orchestration` §2 *Later rounds* (owner-fork answers no reviewer has seen still get the full check). The flag is the caller's word, so the post-check's ledger step (`args.ledger.noNewDecision`, or `--no-new-decision` on the sidecar route) passes it too and each row it writes carries `noNewDecision: true`, which makes a wrong skip auditable.

Materialize the selected ledger roster, never fetch raw mandates. Write shared context and one must-read list file per seat; each list names only the plans that seat reviews. Use `--must-read <list>` for a shared list or `--must-read-for <seat>=<list>` for split coverage. A merged seat key joins its lens keys with `+`; `lens_briefs.py` supplies every mandate and its coverage contract. Run:

```bash
python3 .claude/tools/lens_briefs.py --plan <selected-panel.json> --out <out> --prefix <prefix> --context-path <absolute-context.md> --model <pin> --effort <rung> --must-read <list> --subject-plan meta:<meta-plan> --subject-plan code:<code-plan>
```

Repeat `--subject-plan` for every reviewed plan, with its own shape; omit the inapplicable shape's example flag. Pass seat overrides with `--model-for`, `--effort-for` and `--must-read-for`. The tool writes `jobs_<prefix>.json` under `--out` and the brief files it names. It sweeps meta subjects only and refuses a missing or invalid meta `surfaces` block before spend. `lens_briefs.py` is the sole `INBOUND_REFS` producer; retain its full sweep JSON beside the briefs.

`tools/citer_coverage.py` checks a seat whose key or mandate resolves to a lens declaring `citers: plan`, or any native `review_fanout.js` agent, `dispatch.js` review-shape job or sidecar review seat whose `mustRead` names a meta subject. The subject's `**Shape:**` value identifies `meta`. Each such subject needs a lens brief header and sweep pointer recording its current worktree content hash; sweep HEAD is provenance, not a freshness condition. Missing, stale or split-plan-incomplete coverage names this materialization step and blocks the launch on both routes. Vault `absent` or unreadable coverage remains visible, never clean.

Invoking `/plan_check` is this fan-out's Workflow authorization (`orchestration` §0). Native route: `Workflow({scriptPath: ".claude/workflows/review_fanout.js", args})`, never `name`: the file is underscore-named but registered as `review-fanout`, and a named call can run a cached older engine. Its header owns the args schema. Read the generated jobs JSON and map each job to an agent: `{key: job.seat, label: job.label, mandates: job.mandates, mustRead: job.mustRead, promptPath: job.promptPath, model: job.model, effort: job.effort, agentType: job.agentType}`. Pass the shared context file as `args.contextPrefixPath`; do not assemble another prompt. Each seat takes its lenses' `seat_tier` from `lens.py`, and a `tier` written on the call cannot override it. File-backed fallback: pass the same generated jobs to `dispatch.js` with `contextPath` and `spillDir`; its prompts are the same brief files, not a second materialization. Before the panel, snapshot every plan as a blob subject ([protocol Step 4 *Fold subject*](agents/orchestrator_action_protocol.md#fold-subject)): root `.`, files `<plans>`, out `.claude/scratch/plan_check_<sid8>_snapshot.json`. Add `args.spillDir` (`.claude/scratch/plan_check_<sid8>_spill`) and `args.fold`, with its own `model` and `effort` and one `subjects` entry per plan (`{root: ".", snapshot: <that manifest>, files: [<plan>]}`), plus `args.ledger` `{stage: "plan-check", subject: "plan:<path>", roster: <roster.json>}` (Phase 4). The engine folds each subject with routed findings; a finding that matches several subjects, cites an absolute path no subject holds, or (in a multi-plan check) cites no file, is flagged `fold-routing`; one citing a relative file no subject holds is held for the session (`fold.outOfSubject`). Off-transport: rerun the materializer with `--target sidecar` and a registry alias as `--model`, keeping the selected roster, typed subjects and must-read lists. Launch `python3 .claude/tools/sidecar_fanout.py <generated-jobs.json> --review` with the jobs file under `--out` and Phase 4's fold and ledger values (`reference/sidecar_dispatch.md` owns the recipe); the generated jobs carry the canonical `.claude/schemas/review_findings.json`. Keep the same finding/coverage contract across routes. Do not substitute an unpinned Agent call.

Lenses are read-only except their evidence artifacts. Tests, builds and shared LSP operations remain serialized by the parent; independent source inspection is required. Briefs carry constraints, exclusions and done-conditions. A brief's operations fit its seat's profile (`orchestration` §5b capability preflight). Recover incomplete runs before any fresh dispatch.

## Phase 3: Consolidate & Report

Consume the result per [Step 1.5](agents/orchestrator_action_protocol.md). It reads the complete source, so critical and ASK accounting needs no separate retrieval. Use `delivery.coverage` for seat coverage.

Preserve every original finding and source ID. Group only as a view; never discard distinct findings because they share a location. Show received/grouped counts and uncovered lenses. Verify consequential source claims first-party and resolve contradictions against evidence, not votes.

Follow the action protocol in [`agents/orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md): critical first, then `FIX|ASK|PLAN`, then `bug|rule|improvement`. Report the shape, the tier with the fact that set it, the seat map, per-mandate coverage with UNCOVERED mandates, per-seat yield from `orchestration_metrics.py --run <runId>`, scope gaps, verified findings and the artifact path briefly. Record one verdict row per seat in the `mandates` shape (`/orchestration_metrics` *Incremental rating*). Account for every finding before acceptance. List applied alternatives as `<id> applied <options[0] short> — alternative: <options[1]>`: full mode uses `fold.digest.appliedWithAlternative` or each `fold.subjects[].digest.appliedWithAlternative`; bounded mode uses `fold.appliedWithAlternative` or each `fold.subjects[].appliedWithAlternative`. Retrieve the complete fold artifact when the preview is incomplete.

| Verdict | Meaning |
|---|---|
| APPROVE | No critical or orphan requirement and no remaining actionable findings |
| APPROVE WITH NOTES | No critical or orphan requirement; accepted actionable noncritical findings have supported dispositions |
| REVISE PLAN | Critical, orphan scope, missing required test-first/integration verification, or unresolved design fork |

Which changes need a failing test first and which need integration verification is CLAUDE.md §Hybrid TDD's. Meta reviews use harness verification, not unrelated Godot rules.

When the verdict calls for a revision round, read [`plan_check_detail.md`](reference/plan_check_detail.md) §Phase 3 rounds: it reconverges per `orchestration` §2 *Later rounds* from the post-check's `nextRound` facts, measures your own design edits with `fold_check.py --measure`, and scopes a round on split plans. Stop when coverage is complete and no supported critical remains; otherwise halt per `drive_spine.md` §Halts.

## Phase 4: Execute Actions

The fold stage rules on every finding per [`review_fold.md`](agents/review_fold.md) §Procedure, critical and design-changing ones included. The session consumes the result per [Step 1.5](agents/orchestrator_action_protocol.md) with each plan as a blob subject, and rules per Step 4. It owns the convergence verdict. A refutation [`review_fold.md`](agents/review_fold.md) step 1 defers is the session's to rule, never discarded or auto-folded; verify a versioned first-party citation through the source route first. For an UNVERIFIED quote, reread the source and replace a paraphrase with verbatim evidence in the refold's findings; consume the refold per Step 1.5. A fabricated refutation is discarded and recorded; never re-dispatch that seat or spend a convergence round on it. Over a markdown plan the fold transcribes and holds, which is tight-spec execution at the fanout tier's ladder cell; a subject holding a code path takes the executor tier's cell (`orchestration` §5). Resolve taste with the user or the explicit unattended policy; approval follows [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md) Step 4. Review completion is not implementation completion. Off-transport, launch per `reference/sidecar_dispatch.md` with `--fold-subject ".::<manifest>::<plan>"` (one per split plan) and `--ledger-stage plan-check --ledger-subject plan:<path> --ledger-roster <roster.json>`; the same fold runs and `post_check.json` is written.

## Constraints

- Do not review a plan while implementing it; landed work otherwise masquerades as a false premise.
- Missing/invalid lenses remain uncovered. An invocation or process exit is not verification.
- Keep existing safety, ownership and permission boundaries while simplifying procedure.
- An empirical refutation without verbatim tool output is discarded.
- Replacements enumerate their old public/behavioral surface in the plan; `/session_audit` Phase 1.5 verifies reproduction as a MERGE-BLOCKER.
