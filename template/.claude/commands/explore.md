---
allowed-tools: Bash(git ls-files:*), Bash(git ls-tree:*), Bash(git branch:*), Bash(git grep:*), Bash(git check-ignore:*), Bash(node:*), Bash(bash:*), Glob, Grep, Read, Write, Workflow, Task
description: Establish current owners, constraints and consumers before designing a change
---

# Explore

Establish what exists before proposing a design. Return evidence-backed claims, not approval or recommendations. The project planning path is defined in CLAUDE.md; no Plan Mode transition is needed.

## When to invoke

Use for a named system/change before design, through a drive command or directly. Skip a known single mechanical fix or an unchanged exploration already completed in this session. Revisit only changed or uncovered dimensions.

`/explore <topic>` uses the trigger-selected lenses. `--make-this-easy <specific change>` adds the change-friction lens; a vague topic is not enough.

## Phase 1: Scope & Seed

### 1a. State the topic
Name the capability/files and intended change. Record actual constraints and exclusions. Facts inherited from another session are claims to check, not ground truth.

### 1b. Select resources
Load `orchestration` and read the current currency/band before choosing pins. Use one suitable arm per independently needed lens. Provider availability does not create more jobs. Explicit comparisons are separate, frozen and budgeted.

### 1c. Infer domains
Use actual scope and `reference/memory_domains.md`; meta work does not need gameplay domains. Search memory by separate relevant facets, not one broad union query.

### 1d. Select lenses
Run `python3 .claude/tools/lens.py shared explore` for triggers, then `get <keys>` for selected mandates. Keep the `exp-memory` and `exp-prior-art` floor lenses and every triggered dimension. `--make-this-easy` adds `exp-change-ease`, never replaces another lens. Record why each lens ran or was omitted. Resolve current pins through orchestration/registry, not historical vendor examples in a catalog.

### 1e. Assemble context
Put topic, domains, exclusions, evidence handles and starting paths in one run-specific scratch file. Push orientation, not conclusions. A seed is not a search boundary. Do not inline whole source sets or unrelated skills.

## Phase 2: Dispatch

Invoking `/explore` is this fan-out's Workflow authorization (`orchestration` §0). Use the existing exploration engine with explicit model, effort and agent profile. Native: `.claude/workflows/explore_fanout.js`, with selected `{key,promptPath,model,effort,agentType}` lenses, `contextPrefixPath` and `spillDir`. Off-transport: the registered sidecar owner with `.claude/workflows/explore_fanout.schema.json`. Preserve one run identity across routes; current transport capability, not vendor count, selects the mechanism.

Lenses read only source and write only their declared evidence artifact when their profile permits it. Do not promise Write from a read-only profile. Keep completed evidence recoverable before the final response. Tests/builds/shared LSP operations stay serialized by the parent; cheap independent searches remain part of each lens's work.

Every claim has a subject, polarity, bearing, source/quote and verification status. An absence needs a proving command and scope; empty Glob/Grep alone is not proof. Missing output is uncovered, not a valid empty result. Return a short decision digest and artifact handles; do not reject full evidence to satisfy a presentation cap.

The engine returns a bounded preview of the claims. Capture `transcriptDir` from the Workflow result — the salvage path needs it — and retrieve the full claim set with `python3 .claude/tools/session_digest.py --workflow-dir <transcriptDir> --workflow-kind explore --workflow-manifest`, then `--workflow-select <ID>`, `--workflow-page {items,lenses,reports,gaps,merges} --page <N> --page-size <N>` or `--workflow-full`, each with `--expect-journal-sha256 <hash from the manifest>`. A dossier built from the preview alone is an undercount, not a short run.

## Phase 3: Consolidate & Report

- Account for every required lens. Recover a failed result from its artifact/transcript before considering a fresh dispatch. A `flags` entry of kind `lens-did-not-run` or `lens-no-return` is an UNCOVERED dimension, not a clean one; recover from `<spillDir>/<key>.spill.md` first.
- Verify premise contradictions first-party. Reconcile conflicting claims against evidence, never sort order or vote count.
- Preserve every material claim/provenance when grouping. Report uncovered scope before any clean-sounding summary.

Report topic, domains, lenses/omissions, transport/currency, counts and artifacts briefly, then the material claims:
1. Premise contradictions that change the task.
2. Existing owners/families, their actual coverage and relevant consumers.
3. Constraints and blast radius.
4. Resolved contradictions and remaining gaps.

For `--make-this-easy`, include verified change friction. The parent derives the smallest restructuring from that evidence and names what not to touch — the invariants and settled decisions the friction claims identified; a lens does not author the design. Do not expand a narrow change into speculative infrastructure.

## Handoff

Carry constraints into the plan, existing-owner claims into its family inventory, and gaps into its unresolved scope. A plan/check consumes the dossier; do not regenerate it merely to start the next procedure. In `--make-this-easy` mode the derived minimal restructuring becomes the plan's first slice, or goes back to the user as a preparatory Part when it outgrows one.

## Constraints

Read-only status is a contract, not proof of sandbox enforcement. Cite what was actually verified. Keep ownership and permission boundaries, declared coverage, explicit pins and recovery. No verdict, no hidden provider multiplication, no duplicated survey because a task returned late.
