---
allowed-tools: Read, Grep, Glob, Write, Bash, SlashCommand, Workflow
description: Recover one failed fan-out lens from its engine receipt without repeating completed work.
---

# /salvage_fanout — transient lens recovery

Recover a failed lens only after proving what the engine actually ran. The run receipt and journal are the
source of truth; a timeout, output-file presence, or empty search does not prove that a lens died.

## Inputs

`/salvage_fanout <transcriptDir-or-run-receipt> <lens-key> [--kind explore|review|dispatch] [--spill-dir <path>]`

- `<transcriptDir-or-run-receipt>` is the exact Workflow transcript directory or the engine run receipt.
- `<lens-key>` is the selected lens/job key from the run's started row.
- `--kind` is optional only when the run receipt identifies exactly one workflow kind.
- `--spill-dir <path>` is required with a bare transcript directory; omit it only when the run receipt carries
  the engine's `spillDir` or per-lens `spillMetadata`.

Reject missing or unknown workflow kind, missing or unknown lens key, an absent or ambiguous transcript/run
receipt, missing spill metadata, and malformed journals before reading a deliverable. Do not guess a path
from a key. Resolve the engine kind, run ID, journal hash, schema, started/terminal rows, and exact spill
path from the run receipt plus validated journal; the journal alone does not carry spill metadata.

## Procedure

1. **Validate the run receipt.** For the exploration or review kind, build the journal manifest with:

   ```bash
   python3 .claude/tools/session_digest.py --workflow-dir "<transcriptDir>" --workflow-kind <explore|review> --workflow-manifest
   ```

   `session_digest` supports only explore and review. A `dispatch` reads `journal.jsonl` directly, computes
   its SHA-256, and validates the `launched`, selected `started`, and terminal rows; never call the unsupported
   `--workflow-kind dispatch`. Use the selected started row's `agentId` to read the exact
   `agent-<agentId>.jsonl`; never search every transcript and treat an empty Grep as absence. Bind the exact
   spill path from the run receipt or `--spill-dir` plus the engine-reported sanitizer.
2. **Prove terminal state.** Require the engine's terminal row for the selected key. If no terminal row exists,
   report **still running** and wait for the harness notification. For a sidecar, also require its `.exit`/record and process-state answer. For a manual Agent,
   require the harness task's terminal notification.
3. **Recover a completed payload.** Prefer the exact engine-reported spill path, then the final payload in the
   transcript. Validate against the resolved schema: the exploration kind uses `claims`, `review` uses `findings`, and
   `dispatch` uses its free-form job result. Write a recovered result only to the engine-reported
   `<spillDir>/<sanitized-key>.salvaged.md` path, with the run ID, journal hash, source path, and lens key.
   A spill or payload is **recovered**, not verified; recheck every empirical claim first-party before using it.
4. **Handle a transient reset.** If the receipt contains a real resumable session ID and that transport
   documents resume, use that documented resume path for a sidecar or manual Agent/session only. Native
   Workflow child IDs are not SendMessage sessions; never promise `SendMessage` can resume one. A native
   Workflow child, or a missing session receipt, may use parent reconstruction only when the transcript has
   a complete tool-result for every started tool call and the mandate's required inputs/search roots show
   engagement. Recheck each reconstructed empirical claim first-party and write `.salvaged.md`.
5. **Recover incomplete evidence once.** If complete tool results or mandate engagement are missing, do one
   failed-lens-only dispatch over the named missing scope. First compose `orchestration` §0, §5, and §11 into
   a **Dispatch Topology Receipt**; record its pre-dispatch half and compare the post-journal/PINS half after
   return. Do not redispatch a completed lens or the whole panel.
6. **Return the typed ledger.** Emit exactly one row for the selected lens with state `recovered`, `uncovered`,
   or `redispatched`, the evidence path and verification status. Always emit `accounted lenses N/N`; until
   N/N the panel remains **UNCOVERED**. A salvaged row stays uncovered until its first-party checks pass.

## Engine-specific receipts

- **Exploration workflow (the coding layer's engine):** use the manifest's claims schema, per-lens journal row, and engine-reported spill
  sanitizer. Do not infer a result from a missing structured return.
- **Workflow `review`:** use the findings schema and preserve every source finding; consolidation never turns
  a missing lens into a clean panel.
- **Workflow `dispatch`:** use the job's free-form result and its exact spill/digest metadata; honor the
  engine's PINS and terminal row.
- **Sidecar or manual Agent/session:** a documented real session ID may resume.

## Output contract

The final report names the resolved kind, key, terminal evidence, exact spill path or final path, schema validation,
row state, first-party checks, and the `accounted lenses N/N` receipt. If a required proof is absent, state
`couldNotSatisfy` with the missing artifact and leave the panel UNCOVERED.
