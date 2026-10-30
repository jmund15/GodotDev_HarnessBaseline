---
allowed-tools: Bash(git ls-files:*), Bash(git ls-tree:*), Bash(git branch:*), Bash(git grep:*), Bash(git check-ignore:*), Bash(python3 .claude/tools/lens.py:*), Bash(node:*), Bash(bash:*), Glob, Grep, Read, Write, Workflow
description: Establish current owners, constraints and consumers before designing a change
---

# Explore

Establish what exists before proposing a design. Return evidence-backed claims, not approval or recommendations.

## When to invoke

Run when grounding is admitted (`_brainstorm_shared/execution_depth.md` §Admission table). Revisit only changed or uncovered dimensions.

`/explore <topic>` uses the trigger-selected lenses. `--make-this-easy <specific change>` adds the change-friction lens; a vague topic is not enough.

## Phase 1: Scope & Seed

### 1a. State the topic
Name the capability/files and intended change; a topic naming no system, file or capability gets a clarifying question, not a dispatch. Record actual constraints and exclusions. Facts inherited from another session are claims to check, not ground truth.

### 1b. Select resources
Load `orchestration`; choose each lens's row and paying plan by the ladder's selection line. Every lens is `fanout`-tier work (a read-heavy survey); resolve the tier per `orchestration` §5, which owns when to declare it. Use one suitable arm per independently needed lens: `/explore` runs one seat per mandate and never merges (`orchestration` §2 *Sizing the width*), because its engine stamps each claim with its seat key. Provider availability does not create more jobs. Explicit comparisons are separate, frozen and budgeted.

### 1c. Infer domains
Use actual scope and `reference/memory_domains.md`; meta work does not need gameplay domains. Search memory by separate relevant facets, not one broad union query.

### 1d. Select lenses
Run `python3 .claude/tools/lens.py shared explore` for selection and evidence-reuse rules, then `get <keys>` for selected mandates. Keep every independently needed dimension its trigger table admits. `--make-this-easy` adds `exp-change-ease`, never replaces another lens. Record why each lens ran or was omitted. Resolve current pins through orchestration/registry, not historical vendor examples in a catalog.

### 1e. Assemble context
Put topic, questions, domains, exclusions, evidence handles and starting paths in one run-specific scratch file. For `exp-memory`, materialize and check the run's KFM index per the `kfm.py` docstring, and include the printed index path in the context. Push orientation, not conclusions. A seed is not a search boundary. Do not inline whole source sets or unrelated skills.

Map each question to the selected lens whose mandate owns it, and write the map into the context file. A question one command or script can enumerate (every call site, every file holding a field, every test naming a symbol) is answered by that script before dispatch, whichever lens owns it: the script also sorts each hit where a fact decides it (path class, declaration or use, test or production), and the owning lens receives the output file and judges only the hits the script could not sort. A question no selected mandate owns and no script answers returns to §1d for a bespoke lens (`commands/agents/explore_agents.md` *Extending the roster*). A script's output file is an evidence handle in the context, and its answer goes in the report.

## Phase 2: Dispatch

Invoking `/explore` is this fan-out's Workflow authorization (`orchestration` §0). Use the existing exploration engine with explicit model, effort and agent profile. Native: `Workflow({scriptPath: ".claude/workflows/explore_fanout.js", args})` (never `name`), whose header owns the argument schema (lenses carry `tier: "fanout"`). Off-transport: the registered sidecar owner with `.claude/workflows/explore_fanout.schema.json`. Preserve one run identity across routes (`transcriptDir`, which recovery and full retrieval key on); current transport capability, not vendor count, selects the mechanism.

Lenses are read-only; `orchestration` §5b owns the capability preflight and §4 the return contract. Keep completed evidence recoverable before the final response. Tests/builds/shared LSP operations stay serialized by the parent; cheap independent searches remain part of each lens's work.

Every claim has a subject, polarity, bearing, source/quote and verification status. `absent` needs git-aware proof within a named scope; when the seat has neither Bash nor a supplied enumeration, report `unclear` and the missing check. Return a short decision digest and artifact handles; do not reject full evidence to satisfy a presentation cap.

The engine returns a bounded preview of the claims. Capture `transcriptDir` from the Workflow result — the salvage path needs it — and retrieve the full claim set with `--workflow-kind explore` per `orchestration` §4. A dossier built from the preview alone is an undercount, not a short run. Consume the checked claims per [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md) Step 1.5 before they enter the dossier.

## Phase 3: Consolidate & Report

- Account for every required lens. A `flags` entry of kind `lens-did-not-run` or `lens-no-return` is an UNCOVERED dimension, not a clean one; recover from `<spillDir>/<key>.spill.md` first.
- Account for every context question: name the claims that answer it. A question with none is UNCOVERED, like a lens that did not run.
- For an `absent` claim a design or plan decision will rest on, its `{command, output: ""}` evidence is rerun in-run (`reference/evidence_contract.md`); per protocol Step 1.5, rerun first-party only when the claim is not VERIFIED.
- Verify premise contradictions first-party. Reconcile conflicting claims against evidence, never sort order or vote count.
- Preserve every material claim/provenance when grouping. Report uncovered scope before any clean-sounding summary.

Report topic, domains, lenses/omissions, transport/currency, counts and artifacts briefly, then the material claims:
1. Premise contradictions that change the task.
2. Existing owners/families, their actual coverage and relevant consumers, and the real integration target with its type contract.
3. Constraints and blast radius.
4. Resolved contradictions and remaining gaps.

For `--make-this-easy`, include verified change friction. The parent derives the smallest restructuring from that evidence and names what not to touch — the invariants and settled decisions the friction claims identified; a lens does not author the design. Do not expand a narrow change into speculative infrastructure.

## Handoff

Carry constraints into the plan, existing-owner claims into its family inventory, and gaps into its unresolved scope. A plan/check consumes the dossier; do not regenerate it merely to start the next procedure. In `--make-this-easy` mode the derived minimal restructuring becomes the plan's first slice, or goes back to the user as a preparatory Part when it outgrows one.

## Constraints

Read-only status is a contract, not proof of sandbox enforcement. Cite what was actually verified. Keep declared coverage, explicit pins and recovery. No verdict, no duplicated survey because a task returned late.
