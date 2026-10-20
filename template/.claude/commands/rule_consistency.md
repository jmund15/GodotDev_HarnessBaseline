---
description: Cross-surface rule-consistency audit — scan every instruction surface for restated rules, flag contradictions and stale refs.
disable-model-invocation: true
allowed-tools: Bash(python3 .claude/tools/lens_briefs.py:*), Bash(git grep:*), Bash(git ls-files:*), Bash(git rev-parse:*), Bash(python3 .claude/tools/restate_scan.py:*), Bash(python3 .claude/tools/post_check.py:*), Read, Workflow
---

# /rule_consistency — Cross-Surface Rule-Consistency Audit

The same rule is stated across many knowledge stores (project + global `CLAUDE.md`, all skills, all commands, the `.claude/auto-memory/` store + its `MEMORY.md` index). Over time the copies DRIFT — a threshold tightened in `CLAUDE.md` while a skill keeps the old number; a memory says "defer X" while a feedback file says "never defer X"; a command cites a behavior the `CLAUDE.md` table contradicts. With no detector, the agent silently follows whichever surface loaded first — the worst failure mode for a rule-dense harness.

`/instruction_audit` is per-FILE quality; `autolearn` gates only new/recently-touched rules. **Nothing compares one rule's statements ACROSS surfaces** — this fills exactly that gap.

This is NOT a new engine and NOT a standing periodic job. It is a Claude-driven sweep that gathers the scan's candidate groups and fans the *comparison* out via `review_fanout`.

## When to use
- On-demand after a batch of harness edits, a big rename, or a CLAUDE.md/skill revision.
- On a harness commit that changes instruction text, adjudicating the near-duplicate scan its Step 0 runs.
- Skip when the change touched no instruction surface.

## Step 0: Scan, then gather (Claude-side, push-don't-pull)

**Self-test first.** Run `python3 .claude/tools/restate_scan.py selftest`; it exits 0 only when a planted near-copy comes back as a group, so an empty scan below means no candidates, never a blind scan. A non-zero exit stops the run and reports the scan uncovered.

**Scan.** Run `python3 .claude/tools/restate_scan.py pairs --changed --base <ref> --json` over the edit batch under audit: `<ref>` is the `--base` argument, else the upstream branch (`@{u}`). A `--base` ref that `git rev-parse --verify` rejects stops the run before the scan. `--full` runs `pairs --json` over the whole scope instead, as the periodic backlog report. The scan covers every surface its `instruction_scope` names; when it reports no instruction scope, stop and report the surfaces uncovered. A group ruled as distinct decisions is recorded with `python3 .claude/tools/restate_scan.py rule <id> distinct --reason "<why>" --changed --base <ref>`.

**Gather.** The scan output already holds each statement with its file and line. The fanned agents never search: an intermittent `Grep`/`Glob` empty reads as a false "no contradiction" (`gotcha_workflow_fanout_search_false_absence.md`). Pack candidate groups into comparator batches no larger than the comparator's input, and write each batch to its own file under `.claude/scratch/rule_consistency/`.

## Step 1: Fan out one comparator agent per batch via review_fanout

Each agent compares its batch's statements, which are in its prompt file; it does NOT search. Mandate:

> You are given candidate groups of statements from across the harness, with file:line. Flag each pair that (a) CONTRADICTS, (b) uses a DIFFERENT threshold/number/list, or (c) is STALE (references a renamed/deleted file, command, or skill). An empty input is INCONCLUSIVE, not "consistent" — say so. Return findings: `category:"rule"`, `action:"PLAN"` with the canonical surface as `options[0]` (the home `instruction_quality` §3 names), `action:"ASK"` only when the statements encode different owner intent and `question` names it, `action:"FIX"` for a mechanical stale-ref, `file` = "surfaceA:line vs surfaceB:line", `description` = the divergence quoted, `rationale` = why it matters.

Put each batch's mandate and statements in its own file and pass `promptPath` per agent, with the shared mandate in one `contextPrefixPath` file, so `args` stays flat (`gotcha_workflow_args_generation_fidelity.md` owns the failure). The batch file is its seat's must-read entry: pass it as the seat's `mustRead`, the entries that `python3 .claude/tools/lens_briefs.py must-read <list file>` prints.

```
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: {
    contextPrefixPath: ".claude/scratch/rule_consistency/context.md",
    agents: [ { key: "<batch>", promptPath: ".claude/scratch/rule_consistency/<batch>.md", tier: "fanout", model: "<resolved native id>", effort: "<resolved effort>", agentType: "general-purpose", mustRead: [<must-read entries>] }, /* one per batch; resolve the fan-out pin through orchestration; never inherit the session model */ ]
  }
})
```

## Step 2: Present grouped by batch

`review_fanout` returns deduped findings (merge across batches). Present sorted contradictions > divergent-thresholds > stale-refs > duplicate-but-consistent (informational). For each contradiction, **Claude is sole writer of the reconciliation** (edit the non-canonical surfaces in place): reconcile to the declared owner when one surface owns the decision (`instruction_quality` §3; `CLAUDE.core.md` §Core Principles owner rule). Ask the owner, in one batched packet, only when no surface owns it or the choice turns on appetite.

A stale-ref `action:FIX` is applied by the protocol's standalone fold (`agents/orchestrator_action_protocol.md` Step 1.5 and Step 4): a blob subject over the files the batches' statements cite, snapshotted before the fold dispatches. `fold-old-missing` confirms each fix's `old` text. Consume the result per protocol Step 1.5.
