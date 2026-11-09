---
allowed-tools: Bash(git status:*), Bash(python3 .claude/tools/extract_numbers.py:*), Bash(python3 .claude/tools/restate_scan.py:*), Bash(python3 .claude/tools/lens_briefs.py:*), Bash(python3 .claude/tools/sidecar_fanout.py:*), Bash(python3 .claude/tools/fold_check.py:*), Bash(git ls-files:*), Bash(grep:*), Bash(python3 .claude/tools/review_ledger.py:*), Bash(python3 .claude/tools/post_check.py:*), Glob, Grep, Read, Write, Workflow
description: Audit a written plan for scope, evidence and design defects
---

# Plan check

Review the written plan before implementation. The findings are advisory; the normal execution approval remains the user's, unless explicit unattended authority applies. Run it only when plan check is admitted (`execution_depth.md` §Admission table); a large plan is not exempt merely because it is metadata.

## Phase 1: Scope & Load

### 1a. Read the plan
The parent accepts inline text or one or more `@<path>`s and selects the newest `.claude/plans/*.md` when no argument is supplied. The parent rejects the whole invocation if any path is missing or malformed. The parent applies and reports `--meta`/`--code` overrides; reject an override with several paths.

`PLAN_SHAPE=meta` when every change is harness/docs infrastructure; any production code, scene, resource or project-data change makes it `code`. The always-loaded surface is `CLAUDE.md`, `.claude/auto-memory/MEMORY.md`, any `rules/*.md` whose `paths:` glob the plan's own edits would trigger, and a skill `description:`. A plan for throwaway prototype work, at the paths or branch the project's prototype route names, goes to that route, not this audit; production code whose name merely resembles it does not qualify. A plan that only cites a prototype's answer under Constraints is a real plan — audit it fully. A plan spanning both shapes partitions per [`plan_file_format.md`](../skills/_brainstorm_shared/plan_file_format.md) §One shape per plan file, and the split plans audit in one invocation. If inseparable, use the Mixed shape: the code mandates plus `plc-instruction-quality` and `plc-doctrine-consistency`; report any unavailable lens as uncovered.

When plan check is not admitted, the plan executes from the short plan `execution_depth.md` §Admission table defines. Convergence then rests on the validation pass plus the `/explore` dossier, and the approval presentation states that plan check was skipped and what replaced it. A skip never satisfies convergence by itself. When plan check is admitted, every mandatory lens runs whatever the plan's size, because the Plan check row of `execution_depth.md` §Admission table decides the audit.

### 1b. Scope coverage
Map every requirement to an implementation and verification step. Check conflicting steps, invented scope, unverified dependencies, stubs/deferrals and closing work. A replacement enumerates the old contract before removal. C# drive closure names the full regression gate. A run wider than a slice's own targeted tests (a scoped verifier, bound proofs, the gate, a whole test file), ordered per slice or inside an executor's steps, is a scope gap: `drive_execution.md` §Slices owns the tiers and `change_control` §Gate cadence binds them. Meta changes name executable, channel, registration and reference checks as applicable. No-tests or unavailable coverage is not green. A `.cs` plan is incomplete if it omits the closing regression gate (`change_control` §Gate cadence names it), `<summary>` coverage for each new `[Export]`, or (roadmap Part) the closing `/update_roadmap` state flip.

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

Every admitted mandate of the shape runs. Width and depth come from the triage 1b read and the drive's rigor (`orchestration` reference `rigor.md`): `orchestration` §2 *Sizing the width* (size and room rules) and §5 *Effort*. A merge follows the order in the table below, and the report states the rigor, the seats and why. A merged seat returns separate evidence for each mandate it carries.

| Shape | Mandates | Merge order |
|---|---|---|
| Code | `plc-memory-alignment`, `plc-pattern-fit`, `plc-architecture-quality`, `plc-test-readiness`, `plc-evidence-grounding` | 1. `plc-pattern-fit` + `plc-evidence-grounding`; 2. that seat + `plc-architecture-quality` |
| Meta | `plc-memory-alignment`, `plc-instruction-quality`, `plc-doctrine-consistency`, `plc-evidence-grounding` | 1. text (`plc-instruction-quality` + `plc-doctrine-consistency`); 2. grounding (`plc-evidence-grounding` + `plc-memory-alignment`) |
| Mixed | `plc-memory-alignment`, `plc-pattern-fit`, `plc-architecture-quality`, `plc-test-readiness`, `plc-evidence-grounding`, `plc-instruction-quality`, `plc-doctrine-consistency` | 1. the Code merge 1 and the Meta text seat; 2. the Code merge 2 |

Split plans get one seat map. Classify each plan's shape; use the Mixed row when their shapes differ, else that shape's row. A shape-only mandate's brief names only its shape's plans; `plc-memory-alignment` and `plc-evidence-grounding` review every plan. Every finding's `file` begins with its plan's path.

Re-rank the order when the panel-yield lines of `/orchestration_metrics` flag a merged mandate or a solo seat with no unique finding.

Add a lens only for a distinct uncovered risk (`orchestration` §0). Judgment needs a qualified review role. Pin each seat to its lenses' `seat_tier` (`lens.py`) at the lowest value of that tier's review effort; `orchestration` §5 *Effort* owns a raise.

Before the panel, write the seats and the reviewed plan paths to `<run dir>/spec.json`. The seats are the seat map's keys, each at its lenses' `seat_tier` and that row's review effort.

Seat only the mandates the ledger returns. A design mandate covered at the unchanged design hash is not re-judged; that plan gets a spot-check instead: `plc-evidence-grounding`, `plc-test-readiness` and `plc-memory-alignment` only, sized by what the plan adds beyond the covered design (units the design does not state), per `orchestration` §2 *Later rounds*. Owner-fork answers no reviewer has seen still get the full check.

Set the spec's `noNewDecision` true when the §1b facts show no new decision. The post-check's ledger step carries it too, so each row it writes carries `noNewDecision: true`, .

```json
{
  "mode": "plan-check",
  "prefix": "pc",
  "topic": "<plans under review>",
  "seats": [{"key": "<seat key>", "effort": "<rung>"}],
  "pins": {"panel": "<row>"},
  "efforts": {"panel": "<rung>"},
  "fold": {"tier": "<fold tier>", "model": "<row>", "effort": "<rung>"},
  "subjectFiles": ["<reviewed-plan-paths>"],
  "seatFiles": {"<seat key>": ["<the plans that seat reviews>"]},
  "subjectPlans": ["meta:<meta-plan>", "code:<code-plan>"],
  "ledger": {"stage": "plan-check", "subject": "plan:<path>", "files": ["<reviewed-plan-paths>"], "design": "<design path>", "noNewDecision": false}
}
```

```bash
python3 .claude/tools/lens_briefs.py prepare --spec <run dir>/spec.json --out <run dir>
```

```
Workflow({scriptPath: ".claude/workflows/review_fanout.js", args: {argsFile: "<run dir>/args.json"}})
```

`prepare` runs `review_ledger.py plan` in process. Repeat `subjectPlans` for every reviewed plan, with its own shape; omit the inapplicable shape's example. A merged seat key joins its lens keys with `+`. Never `name` the Workflow: the file is underscore-named but registered as `review-fanout`, and a named call can run a cached older engine. File-backed fallback: pass the same generated jobs to `dispatch.js` with `contextPath` and `spillDir`; its prompts are the same brief files, not a second materialization. `tools/citer_coverage.py` checks every seat whose lens declares `citers: plan`, and every review seat, against the meta subjects its brief's sweep declares: each needs a sweep at its current worktree content hash (sweep HEAD is provenance, not freshness). It never reads `mustRead` content, so a must-read file's `**Shape:**` header declares nothing. Missing or stale coverage blocks the launch on both routes: rerun prep. Vault `absent` or unreadable coverage remains visible, never clean. Off-transport, set `"target": "sidecar"` and a registry alias as the panel pin; `prepare` writes the sidecar jobs file and `<run dir>/roster.json`, which `sidecar_fanout.py --review --ledger-roster <run dir>/roster.json` reads (`reference/sidecar_dispatch.md` owns the recipe). Do not substitute an unpinned Agent call.

Lenses are read-only except their evidence artifacts. Tests, builds and shared LSP operations remain serialized by the parent; independent source inspection is required. Briefs carry constraints, exclusions and done-conditions. A brief's operations fit its seat's profile (`orchestration` §5b capability preflight). Recover incomplete runs before any fresh dispatch.

## Phase 3: Consolidate & Report

Consume the result per [Step 1.5](agents/orchestrator_action_protocol.md). It reads the complete source, so critical and ASK accounting needs no separate retrieval. Use `delivery.coverage` for seat coverage.

Preserve every original finding and source ID. Group only as a view; never discard distinct findings because they share a location. Show received/grouped counts and uncovered lenses. Verify consequential source claims first-party and resolve contradictions against evidence, not votes.

Follow the action protocol in [`agents/orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md): critical first, then `FIX|ASK|PLAN`, then `bug|rule|improvement`. Report the shape, the tier with the fact that set it, the seat map, per-mandate coverage with UNCOVERED mandates, per-seat yield from `orchestration_metrics.py --run <runId>`, scope gaps, verified findings and the artifact path briefly. Record one verdict row per seat in the `mandates` shape (`/orchestration_metrics` *Incremental rating*). Account for every finding before acceptance. List applied alternatives as `<id> applied <options[0] short> — alternative: <options[1]>`: full mode uses `fold.digest.appliedWithAlternative` or each `fold.subjects[].digest.appliedWithAlternative`; bounded mode uses `fold.appliedWithAlternative` or each `fold.subjects[].appliedWithAlternative`. Retrieve the complete fold artifact when the preview is incomplete.

Verdict: the protocol's scale (`agents/orchestrator_action_protocol.md` §Verdict Logic). REVISE also covers orphan scope, missing required test-first/integration verification and an unresolved design fork.

Which changes need a failing test first and which need integration verification is CLAUDE.md §Hybrid TDD's. Meta reviews use harness verification, not unrelated Godot rules.

When the verdict calls for a revision round, read [`plan_check_detail.md`](reference/plan_check_detail.md) §Phase 3 rounds: it reconverges per `orchestration` §2 *Later rounds* from the revised diff and the ledger's own coverage queries, and scopes a round on split plans. Stop when coverage is complete, every finding that counts at the run's rigor has a disposition and no supported critical remains; otherwise halt per `drive_spine.md` §Halts.

## Phase 4: Execute Actions

The fold stage transcribes literal text fixes per [`review_fold.md`](agents/review_fold.md) §Procedure; the session applies and rules every other finding, critical and design-changing ones included. The session consumes the result per [Step 1.5](agents/orchestrator_action_protocol.md), each plan a blob subject per [Step 4 *Fold subject*](agents/orchestrator_action_protocol.md#fold-subject), and rules per Step 4. It owns the convergence verdict. A refutation [`review_fold.md`](agents/review_fold.md) step 1 defers is the session's to rule, never discarded or auto-folded; verify a versioned first-party citation through the source route first. For an UNVERIFIED quote, reread the source and replace a paraphrase with verbatim evidence in the findings the parent applies; consume the result per Step 1.5. A fabricated refutation is discarded and recorded; never re-dispatch that seat or spend a convergence round on it. The fold's tight-spec execution runs at the fanout tier's ladder cell. Resolve taste with the user or the explicit unattended policy; approval follows [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md) Step 4. Review completion is not implementation completion. Off-transport, launch per `reference/sidecar_dispatch.md` with `--fold-subject ".::<manifest>::<plan>"` (one per split plan) and `--ledger-stage plan-check --ledger-subject plan:<path> --ledger-roster <run dir>/roster.json`; the same fold runs and `post_check.json` is written.

## Constraints

- Do not review a plan while implementing it; landed work otherwise masquerades as a false premise.
- Missing/invalid lenses remain uncovered. An invocation or process exit is not verification.
- Keep existing safety, ownership and permission boundaries while simplifying procedure.
- An empirical refutation without verbatim tool output is discarded.
- Replacements enumerate their old public/behavioral surface in the plan; `/session_audit` Phase 1.5 verifies reproduction as a critical parity finding.
