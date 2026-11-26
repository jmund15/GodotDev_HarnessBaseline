---
allowed-tools: Bash(git ls-files:*), Bash(git ls-tree:*), Bash(git branch:*), Bash(git grep:*), Bash(git check-ignore:*), Bash(python3 .claude/tools/lens.py:*), Bash(node:*), Bash(bash:*), Glob, Grep, Read, Write, Workflow, Skill
description: Ground a design: map what exists, its owners, constraints and consumers, with explicit coverage
---

# Explore

Establish what exists before proposing a design. Return evidence-backed claims, not approval or recommendations.

## When to invoke

Run when a design's questions need a survey (`_brainstorm_shared/drive_spine.md` §Stages Ground). Revisit only changed or uncovered dimensions.

`/explore <topic>` uses the trigger-selected lenses. `--make-this-easy <specific change>` adds the change-friction lens; a vague topic is not enough.

## Phase 1: Scope & Seed

### 1a. State the topic
Name the capability/files and intended change; a breadth map with no design question is a `/delegate` scout (`CLAUDE.interactive.md` §When work becomes a job); a design question too vague to set coverage gets a clarifying question. Record actual constraints and exclusions. Facts inherited from another session are claims to check, not ground truth.

### 1b. Select resources
Load `orchestration`; choose each lens's row and paying plan by the ladder's selection line. Every lens is `fanout`-tier work (a read-heavy survey); resolve the tier per `orchestration` §5, which owns when to declare it. Use one suitable arm per job: `/explore` sizes the jobs from the work (`orchestration` §0), and a job carrying several lenses stamps each claim with its lens key. Provider availability does not create more jobs. Explicit comparisons are separate, frozen and budgeted.

### 1c. Infer domains
Use actual scope and `reference/memory_domains.md`; meta work does not need gameplay domains. Search memory by separate relevant facets, not one broad union query.

### 1d. Select lenses
Run `python3 .claude/tools/lens.py shared explore` for selection and evidence-reuse rules, then `get <keys>` for selected mandates. Cover every lens the trigger table admits: answer its questions or mark them not applicable with why. `--make-this-easy` adds `exp-change-ease`, never replaces another lens. Record why each lens ran or was omitted. Resolve current pins through orchestration/registry, not historical vendor examples in a catalog.

### 1e. Assemble context
Put topic, questions, domains, exclusions, evidence handles and starting paths in one run-specific scratch file. Push orientation, not conclusions. A seed is not a search boundary. Do not inline whole source sets or unrelated skills. `prepare` (Phase 2) writes every `kfm: index` seat's run snapshot and names its index in that seat's brief.

Map each question to the selected lens whose mandate owns it, and write the map into the context file. A question one command or script can enumerate (every call site, every file holding a field, every test naming a symbol) is answered by that script before dispatch, whichever lens owns it: the script also sorts each hit where a fact decides it (path class, declaration or use, test or production), and the owning lens receives the output file and judges only the hits the script could not sort. A question no selected mandate owns and no script answers returns to §1d for a bespoke lens (`commands/agents/explore_agents.md` *Extending the roster*). A script's output file is an evidence handle in the context, and its answer goes in the report.

## Phase 2: Dispatch

Invoking `/explore` is this fan-out's authorization (`orchestration` §0). Every lens job is pinned, so `tools/sidecar_fanout.py` places it: each job runs on the plan the ladder's selection line picks, as a native `dispatch.js` agent when that is the session's own plan and as a sidecar otherwise. `prepare` never picks the plan.

**In a drive with a design job to follow,** a spec that names a `designJob` writes a chains file that runs the design job in the same dispatch: the lenses run first, `explore_result.py`'s consolidation runs as a step inside that dispatch, and the design job receives the checked dossier path with its coverage status, with no session step between (the engine hand-off contract). That join runs only where every lens and the design job are native and the dispatch fits the session's width. When any of them needs a sidecar, or the join does not fit, run the lens-only fan-out below, close it with `explore_result.py`, and launch the design job with the dossier path and status once it passes the gate; the session reads, copies and judges none of it between the two. The fallback costs one launch step. Say at route selection which of the two applies, because a refusal at launch costs more than the sentence.

One `prepare` call (a standalone `/explore`, and the lens-only fallback) writes the briefs, the context and the fan-out's jobs file and launches it. Write `<run dir>/spec.json`:

```json
{
  "mode": "explore",
  "prefix": "explore",
  "topic": "<topic>",
  "questions": {"<seat key>": ["<question it owns>"]},
  "seats": [{"key": "<lens key, or several joined with +>", "rows": {"<row>": "<rung>"}}],
  "subjectFiles": ["<evidence handles the lens reads>"],
  "context": "<absolute context.md from 1e>"
}
```

A seat's `rows` lists every row that meets its bar, each with its effort (`orchestration` Pre-Dispatch Checklist); `pin` and `effort` name one row, and `pins.panel` with `efforts.panel` default both. A seat may add `kind` (default `lens`) and `tier`.

```bash
python3 .claude/tools/lens_briefs.py prepare --spec <run dir>/spec.json --out <run dir>
```

`prepare` launches the lenses (`reference/sidecar_dispatch.md` owns the recipe). The fan-out prints one `NATIVE` line holding the `Workflow` call for the lenses it placed on the session's own plan, and hands the rest to its driver; issue the call and keep its `transcriptDir`. Never pass `name`: a named call can run a cached older engine. The session watcher (`reference/sidecar_dispatch.md` step 6) wakes you when the sidecar lenses end. Once every lens has ended, one call consolidates both kinds the same way:

```bash
python3 .claude/tools/explore_result.py <run dir>/jobs_explore.json [--workflow-dir <transcriptDir> ...]
```

Each lens carries `tier: "fanout"`; `prepare` refuses a seat that does not fit its pin's window, naming the seat and tokens.

Lenses are read-only; `orchestration` §5b owns the capability preflight and §4 the return contract. Keep completed evidence recoverable before the final response. Tests/builds/shared LSP operations stay serialized by the parent; cheap independent searches remain part of each lens's work.

Every claim has a subject, polarity, bearing, source/quote and verification status. `absent` needs git-aware proof within a named scope; when the seat has neither Bash nor a supplied enumeration, report `unclear` and the missing check. Return a short decision digest and artifact handles; do not reject full evidence to satisfy a presentation cap.

`explore_result.py` stamps each claim with its lens and a place id, groups on the subject, flags contradictions and corroboration, checks every claim's evidence through `verify_claims.py`, and writes the full result to `<run dir>/explore_result.json`. Its printed summary is bounded to counts, flags and UNCOVERED lenses; a dossier built from the summary alone is an undercount, not a short run, so read the file for the claims. Consume the checked claims per [`orchestrator_action_protocol.md`](agents/orchestrator_action_protocol.md) Step 1.5 before they enter the dossier.

## Phase 3: Consolidate & Report

- Account for every required lens. A `flags` entry of kind `lens-did-not-run` or `lens-no-return` is an UNCOVERED dimension, not a clean one; recover from the lens's `<label>.md`, stream and record in the fan-out out dir first.
- Account for every context question: name the claims that answer it, or mark it not applicable with why. A question with none is UNCOVERED, like a lens that did not run.
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
