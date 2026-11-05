---
disable-model-invocation: true
---

# Review Fold Stage

`review_fanout.js` runs this stage after review seats when a caller passes `args.fold` (this file is its default brief); `sidecar_fanout.py --review --fold` runs it off-transport; a findings source with no review engine runs it as one `dispatch.js` author job. The prompt names `Root`, `Snapshot`, `Files`, `New paths` (when the caller declared any), the complete findings file, the checker verdicts file and the digest file, and, when the run states a rigor, a `Bar:` line and each finding's `counts`. Edit only inside `Root`, and only the `Files` it lists. Write only to the paths its `Write only` line names. When the prompt says the findings and verdicts files already exist, read them and skip writing them in step 1. `tools/post_check.py` checks the fold on disk through `tools/fold_check.py`.

The fold transcribes. It makes no design choice, adapts no wording, moves, renames or creates no file, and writes no code or test. Every finding it does not transcribe is the parent's (`orchestrator_action_protocol.md` Step 4).

## Procedure

1. Write the complete consolidated findings JSON array from the prompt to the findings file. Run `python3 .claude/tools/verify_claims.py --findings <findings-file> --out <verdicts-file>` and read every verdict; a failed checker is not a clean review. Finding verdicts follow `reference/evidence_contract.md`. Include seat-marked and self-identified refutations.
2. The caller snapshotted `Files` before dispatch (`Snapshot:` names the pre-fold bytes in a form you cannot write). Never copy, write or rewrite the snapshot. A path outside `Files` is read-only for you.
3. Transcribe a finding only when all of these hold; otherwise defer it with a one-line `reason`:
   - its action is FIX, its `old` is a nonempty string and its `new` is a string (an empty `new` deletes);
   - its `file` is a declared existing `.md` or `.txt` file in `Files`;
   - its `old` text occurs exactly once in that file's snapshot, and no other finding you transcribe overlaps it (findings that carry one identical pair are one edit);
   - when it has `evidence`, the verdict is VERIFIED, and a refutation has verified evidence. Read each cited source yourself; `orchestrator_action_protocol.md` Step 1.5 owns what a VERIFIED quote proves.

   A below-bar finding (`counts` false against the `Bar:` line, `orchestration` reference `rigor.md`) takes the same rules. Defer a PLAN, an ASK, code, authored data, settings, a path move, a creation, a conflict with another finding and a destructive rewrite, whoever proposed it.
4. Replace `old` with `new` byte for byte at its one occurrence. Reworded text fails the application check; it never becomes an `adapted` entry.
5. Write the digest to the digest file, then return the same object.

## Digest

The digest holds your judgment only: what you applied and what you held. `post_check.py` computes verdicts, id coverage and the old/new facts, so report none of them.

| Field | Content |
|---|---|
| `applied` | `{id, change, source: "finding", file?}`: what changed and where, one line; a fileless application over several subject files names its subject-relative `file` (`changedPaths` cannot attribute it) |
| `deferred` | `{id, reason}`: one line |
| `changedPaths` | every path you edited, relative to `Root` |

Every input id appears once across `applied` and `deferred`; an id you omit is held for the session. An `applied` list over unchanged files is a failed fold.
