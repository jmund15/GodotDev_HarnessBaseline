---
description: Cross-surface rule-consistency audit — gather each rule cluster across CLAUDE.md/skills/commands/memory, flag contradictions and stale refs.
allowed-tools: Bash(git grep:*), Bash(git ls-files:*), Read, Workflow
---

# /rule_consistency — Cross-Surface Rule-Consistency Audit

The same rule is stated across many knowledge stores (project + global `CLAUDE.md`, all skills, all commands, the `.claude/auto-memory/` store + its `MEMORY.md` index). Over time the copies DRIFT — a threshold tightened in `CLAUDE.md` while a skill keeps the old number; a memory says "defer X" while a feedback file says "never defer X"; a command cites a behavior the `CLAUDE.md` table contradicts. With no detector, the agent silently follows whichever surface loaded first — the worst failure mode for a rule-dense harness.

`/instruction_audit` is per-FILE quality; `autolearn` gates only new/recently-touched rules. **Nothing compares one rule's statements ACROSS surfaces** — this fills exactly that gap.

This is NOT a new engine and NOT a standing periodic job. It is a Claude-driven sweep that gathers each cluster's surfaces and fans the *comparison* out via `review_fanout`.

## When to use
- On-demand after a batch of harness edits, a big rename, or a CLAUDE.md/skill revision.
- Opportunistically at `/session_end` ONLY when the session touched 3+ `.claude/` instruction files.
- Skip if the session touched no instruction surfaces.

## Step 0: Pick clusters, gather surfaces (Claude-side — push-don't-pull)

`$ARGUMENTS` may name one cluster; else sweep the canonical set. Gather via `git grep` (reliable — the fanned agents must NOT discover; intermittent `Grep`/`Glob` empties read as false "no contradiction," see `gotcha_workflow_fanout_search_false_absence.md`).

Canonical rule clusters (start here; add as the harness grows):
1. **tool-routing** (read_files / LSP / semantic-search / Grep boundaries)
2. **TDD / Logic-domain** (strict-TDD, domain split)
3. **worklog routing** (trivial-do-now / regular / future / user-tasks)
4. **comment discipline** (default-to-none litmus)
5. **naming conventions** (files, commands, nullable returns)
6. **Obsidian write-locations** (PP vs Jmodot split)
7. **memory placement** (hot topic file vs cold `archive/`)
8. **Jmodot framework boundary**
9. **regression-gate mandate** (mandatory for `.cs`, meta exempt)
10. **plan-mode gates** (plan_check litmus, ExitPlanMode)

For each cluster, gather every statement across the surfaces with file:line:
```bash
ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" grep -nE "<cluster keyword(s)>" -- .claude/CLAUDE.md '.claude/skills/**/*.md' '.claude/commands/**/*.md' '.claude/auto-memory/**/*.md'
git -C "$ROOT" grep -nE "<cluster keyword(s)>" "$HOME/.claude/CLAUDE.md"   # global (outside repo — absolute path unavoidable)
```
(The `**/*.md` glob covers `.claude/auto-memory/archive/` too — the file store is the single memory source.)

## Step 1: Fan out one comparator agent per cluster via review_fanout

Each agent receives its cluster's gathered statements (verbatim, with file:line) embedded in the prompt — it compares, it does NOT search. Mandate:

> You are given every statement of the **<cluster>** rule across the harness, with file:line. Flag each pair that (a) CONTRADICTS, (b) uses a DIFFERENT threshold/number/list, or (c) is STALE (references a renamed/deleted file, command, or skill). An empty input is INCONCLUSIVE, not "consistent" — say so. Return findings: `category:"rule"`, `action:"ASK"` when a human must pick the canonical surface (`options` = the surfaces, recommended-first), `action:"FIX"` for a mechanical stale-ref, `file` = "surfaceA:line vs surfaceB:line", `description` = the divergence quoted, `rationale` = why it matters.

**Generation-fidelity guard (chunk the dispatch).** Each cluster prompt carries verbatim quoted statements — escape-dense and nested. Packing *all* swept clusters into one `Workflow` `args` blob is the `gotcha_workflow_args_generation_fidelity.md` death case (the model emits malformed JSON → `review_fanout.js`'s `JSON.parse(args)` throws → 4ms / 0-agent / 0-byte). So: keep the shared mandate in `contextPrefix` (NOT repeated per agent); and when a sweep covers **more than ~3–4 clusters, dispatch in batches** of ≤4 cluster agents per `Workflow` call, merging the returned findings across calls. Triage any 4ms/0-agent/0-byte death as **malformed args, not a broken tool** — re-dispatch the batch smaller, or fall back to one `Agent()` per cluster (one flat prompt). The file:line *dedup* `review_fanout` performs is incidental here (the `file` field is a `surfaceA:line vs surfaceB:line` *pair*, so cross-agent collapse never fires); the workflow is retained for its schema-enforced findings, deterministic sort, and the canonical consolidation path.

```
// ≤4 cluster agents per call; repeat per batch when sweeping the full canonical set
Workflow({
  scriptPath: ".claude/workflows/review_fanout.js",
  args: {
    contextPrefix: "Cross-surface rule-consistency audit. You compare PUSHED statements only — do not search.",
    agents: [ { key: "tool-routing", prompt: "<mandate + gathered statements>" }, /* ≤4 per batch */ ]
  }
})
```

## Step 2: Present grouped by cluster

`review_fanout` returns deduped findings (merge across batches if Step 1 chunked). Present grouped by cluster, sorted contradictions > divergent-thresholds > stale-refs > duplicate-but-consistent (informational). For each contradiction, the user picks the canonical surface; **Claude is sole writer of the reconciliation** (edit the non-canonical surfaces in place per the harness-file-edit rule — `Edit` directly, never `write_doc`).

Before applying any stale-ref `action:FIX`, run the **Step 1.5 verification** from `agents/orchestrator_action_protocol.md` against each fix's `old` text (fanned agents can hallucinate a file:line or paraphrase the cited statement; a stale-ref FIX with a hallucinated `old` either fails the `Edit` exact-match or mangles the wrong line). Apply only after the cited text is confirmed present at the cited location.
