---
description: Turn two or more same-prompt model arms (sessions, subagents, sidecar dispatches) into adjudicated ladder evidence via /codify.
---

# /ladder_ingest — model comparison → ladder evidence

The paired-ARM sibling of `/pin_ab`. An **arm** is one model's run of the shared prompt, however it was dispatched:

| arm | operand |
|---|---|
| a Claude Code session | `<id-prefix>` — from `session_digest.py --list 12` |
| a workflow or Agent-tool subagent | `<path>.jsonl` — a sibling `.meta.json` supplies model + label |
| a sidecar dispatch | `<path>.record.json` — deliverable read from the sibling `.out.json` |
| a whole workflow run | `<dir>/` — expands to every `agent-*.jsonl` in it |

Operands mix freely. `--out` is always required. With no arm the tool prints its usage — run `session_digest.py --list 12` for session ids, or point at a workflow run dir. One arm writes the files and says nothing is comparable.

**Precondition — the arms read a frozen input** (`orchestration` §0). Repo moved mid-run ⇒ ingest nothing, and say so in `doesNotShow`: this is not recoverable here.

**A changed board row invalidates the clauses that rank its task.** A re-judge, a new instrument generation or a new ranked cell counts. Re-run `ladder_ingest.py --apply` and `/codify` for each such clause; compare the row's `when` column with the clause's last edit to find them.

1. `python3 .claude/tools/ladder_ingest.py <arms...> --out <vault>/Claude/Meta/Benchmark/ladder-ingest --slug <slug>` → per-arm files + `COMPARISON.md`. Read its `same_prompt` and `prompt_not_recorded` lines first: arms that ran different prompts are a weaker comparison, and the file says so rather than hiding it.
2. Resolve the executor-role judge and its effort from `reference/model_ladder_evidence.md`; `model_registry.py for-role executor` only locates the rows. Then dispatch `judge_brief.md` + `judge_schema.json` through `dispatch.js`, `shape: review`, `agentType: "general-purpose"`, currency stated.
3. `ladder_ingest.py --apply <judge-result.json> ...` → Adjudication section, prints `ladderClause` + `orchestrationNote`.
4. `/codify` the clause onto the ladder row, then run `python3 .claude/tools/ladder_prose_check.py` — it must exit 0.

## What lands on the ladder

Lead with the routing verdict. State capability in the instrument's terms, never relative to another row; rows retire.

- **Write a model tendency, never this run:** *"exhaustive over a diff, slow"*, not *"this run found N defects"*.
- **Rewrite an existing clause or add none.** Appending one observation per ingest turns a routing table into a changelog.
- No score, task id, date, arm count, wall-clock figure, multiplier, run description or per-run link in `±` or `effort`; `ladder_prose_check.py` enforces this mechanical half.
- An observation that would not recur belongs nowhere. Say so in `doesNotShow` and stop.
- A new model family gets no ladder row until a battery scores it. Use `/pin_ab` to earn a comparison; explicit dispatchability stays in the registry meanwhile.
- Rows name model families, never versions or transports. A new version takes over its family's row: replace only the cells it ran, and keep older evidence for the rest until a cell or an owner ruling shows the new version differs. A version gets its own row only when two versions are worth running for different work.
- Candidate claims move from `model_ladder_candidates.md` only after their named measurement lands, and only as a tendency folded into an existing clause.

Done: Adjudication present, the ladder row reads as a tendency, `ladder_prose_check.py` exits 0, verdict in `orchestration_verdicts.json`.
