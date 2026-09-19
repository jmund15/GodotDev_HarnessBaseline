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
2. Dispatch its `judge_brief.md` + `judge_schema.json` via `dispatch.js`, `opus·high`, `shape: review`, currency stated. Pass `agentType: "general-purpose"` with the resolved `model` and `effort`.
3. `ladder_ingest.py --apply <judge-result.json> ...` → Adjudication section, prints `ladderClause` + `orchestrationNote`.
4. `/codify` the clause onto the ladder row, then run `python3 .claude/tools/ladder_prose_check.py` — it must exit 0.

## What lands on the ladder

**A TENDENCY of the model, never a description of this run.** `reference/model_ladder_evidence.md` §Authoring an entry is the rule; this is where it is most often broken, because the run is what you just watched.

- Write what the model *does*, not what it did here: *"exhaustive over a diff, slow"* — not *"on the 5-lens audit it found 26 defects"*.
- **Rewrite an existing clause or add none.** Appending one observation per ingest turns a routing table into a changelog.
- No run description, no date, no arm count, no wall-clock figure, and **no link to a per-run write-up** — a comparison that needs a citation to make sense has not been converted into a tendency yet.
- An observation that would not recur belongs nowhere. Say so in `doesNotShow` and stop.

Done: Adjudication present, the ladder row reads as a tendency, `ladder_prose_check.py` exits 0, verdict in `orchestration_verdicts.json`.
