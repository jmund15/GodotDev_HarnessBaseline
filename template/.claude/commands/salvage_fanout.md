---
allowed-tools: Read, Grep, Glob, Write, Bash, SlashCommand, Workflow
description: Recover a failed fan-out lens
---

# /salvage_fanout — transient lens recovery

Recover a failed lens only after proving what the engine actually ran. The run's journal is the source of
truth. A timeout, a present output file or an empty search does not prove that a lens died.

## Inputs

`/salvage_fanout <run dir> <lens-key>`

- `<run dir>` is the Workflow run's transcript directory, the one holding its `journal.jsonl`.
- `<lens-key>` is the lens or job key from the run's started row.

Reject an absent run directory, a malformed journal, or a key with no started row, before reading any
deliverable. Never guess a path from a key.

## Procedure

1. **Read the journal.** Run `/session_digest workflow <run dir>`, which runs
   `python3 .claude/tools/session_digest.py --workflow-dir "<run dir>"`. It prints the run's engine,
   each key's started and terminal rows, the agent id and the spill path. Take the selected key's rows
   from it. Read that agent's `agent-<agentId>.jsonl` by its exact name. An empty Grep across every
   transcript is not absence.
2. **Prove terminal state.** No terminal row for the key means **still running**: wait for the
   Workflow's completion notification.
3. **Recover a completed payload.** Take the spill path the journal names first, then the final payload in
   the agent transcript. Check it against the engine's result shape: an explore lens returns `claims`, a
   `dispatch.js` job its free-form result. A missing structured return is never read as an empty result,
   and consolidation never turns a missing lens into a clean panel. Write the recovered result to
   `<spill dir>/<key>.salvaged.md` with the run directory, the key and the source path. A recovered
   payload is not verified: recheck every empirical claim first-party before using it.
4. **Reconstruct after a transient reset.** A native Workflow child is not a resumable session, so
   `SendMessage` cannot resume it. Reconstruct from the agent transcript only when every started tool call
   has its result and the mandate's required inputs and search roots show engagement. Recheck each
   reconstructed empirical claim first-party, then write `.salvaged.md`.
5. **Re-dispatch the missing scope once.** When tool results or mandate engagement are missing, run one
   `/delegate` job over the named missing scope of the failed lens only, never a completed lens or the
   whole panel. When the work has a state file, record the job there. A re-dispatch that still leaves the
   evidence incomplete is non-convergence: report the lens uncovered and stop.
6. **Return the typed ledger.** Emit one row for the selected lens with state `recovered`, `uncovered`
   or `redispatched`, the evidence path and the verification status. Always emit `accounted lenses N/N`.
   Until N/N, the panel stays **UNCOVERED**. A salvaged row stays uncovered until its first-party checks
   pass.

## Output contract

The final report names the engine, key, terminal evidence, spill or final path, result-shape check, row
state, first-party checks and the `accounted lenses N/N` receipt. When a required proof is absent, state
`couldNotSatisfy` with the missing artifact and leave the panel UNCOVERED.
