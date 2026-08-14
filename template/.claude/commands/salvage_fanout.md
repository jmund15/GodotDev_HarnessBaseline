---
allowed-tools: Read, Grep, Glob, Write, Bash
description: Recover a failed fan-out lens's deliverable from its spill file or transcript (no re-dispatch).
---

A Workflow lens that dies on structured-output validation still completed its exploration — the deliverable
is on disk. Recover it before any re-dispatch: the spill file first, the transcript second.

## Inputs

- `<transcriptDir>` — the workflow transcript dir from the Workflow invocation result (contains `journal.jsonl` + `agent-<id>.jsonl`; also printed in the run's flags)
- `<key>` — the failed lens key, e.g. `exp-memory`

## Procedure

1. **Spill file first.** If `<spillDir>/<key>.spill.md` exists — the engine's spill-before-validate contract had every lens write its complete deliverable BEFORE attempting structured output — that file IS the deliverable. Parse it, sanity-check the shape (required fields and types, never length), and report the claim count. Done.
2. **Transcript otherwise.** In `journal.jsonl`, every agent has a `{"type":"started", ...agentId}` entry; a lens with NO `{"type":"result"}` entry is a failed one. Grep the `agent-*.jsonl` files for `You are <key>` to find its transcript.
3. **Find the deliverable.** The LAST assistant message before the final `Output does not match required schema` rejection carries the complete deliverable JSON (the retry loop never overwrites it). Grep for the rejection line to locate it, then Read the transcript tail.
4. **Extract and write.** Pull the JSON payload from that message, validate its shape, and write it to `<spillDir>/<key>.salvaged.md` with a provenance header: transcript file, line, date, lens.
5. **Mark provenance.** Salvaged output is recoverable, not verified: any `confidence: "verified"` claim must be re-verified first-party, because the engine's evidence-downgrade pass never ran on it. Report the claim count and say the caveat.

## Never

- Re-dispatch a lens before checking its spill file and transcript — that re-buys the exploration (~100K+ cache-read tokens per lens, measured 2026-08-08).
- Treat a salvage as equal to a live schema-validated return without saying so — a salvaged dossier is `UNCOVERED` until verified.
