---
disable-model-invocation: true
---

# Review Fold Stage

`review_fanout.js` runs this stage after review seats when a caller passes `args.fold` (this file is its default brief); `sidecar_fanout.py --review --fold` runs it off-transport; a findings source with no review engine runs it as one `dispatch.js` author job. The prompt names `Root`, `Snapshot`, `Files`, `New paths` (when the caller declared any), the complete findings file, the checker verdicts file and the digest file. Edit only inside `Root`, and only the `Files` and `New paths` it lists. Write only to the paths its `Write only` line names. When the prompt says the findings and verdicts files already exist, read them and skip writing them in step 1. `tools/post_check.py` checks the fold on disk through `tools/fold_check.py`.

## Procedure

1. Write the complete consolidated findings JSON array from the prompt to the findings file. Run `python3 .claude/tools/verify_claims.py --findings <findings-file> --out <verdicts-file>` and read every verdict; a failed checker is not a clean review. Finding verdicts follow `reference/evidence_contract.md`. Include seat-marked and self-identified refutations.
2. The caller snapshotted `Files` before dispatch (`Snapshot:` names the pre-fold bytes in a form you cannot write). Never copy, write or rewrite the snapshot. A path outside `Files` and `New paths` is read-only for you.
3. Read the source and subject context for every finding. Take its FIX edit, or a resolved PLAN's `options[0]`. Read each cited source and judge support yourself (`orchestrator_action_protocol.md` Step 1.5 owns what a VERIFIED quote proves). Check the real callers of a named symbol, not only its public type. Record which you wrote as the decision's `source`: `finding` for its fix or option verbatim, `adapted` for reviewers' fixes merged or reworded with no design of your own, `fold` only when applying a finding needs you to rearrange or specify within the subject's existing mechanisms, and `ruling` for a ruled id whose prompt-given ruling is not one of the finding's options; the next review round re-reviews a `fold` decision, and step 4 holds any other design of your own.
4. Hold back only these cases. Name the hold class and reason in `deferred`; the session rules them, because added design, whoever proposed it, draws its own review round.
   - an ASK, whose `question` names an owner-held input (`owner-input`); an open PLAN, one with no `options` (`orchestrator_action_protocol.md` action table), whose `question` names a decision the reviewer could not make (`own-design`);
   - a taste fork whose answer needs an unstated appetite or preference (`taste-fork`);
   - a premise that needs a measurement or spike no source read settles (`spike`: name the spike);
   - a conflict with another finding or a ruling in the subject (`conflict`);
   - a remedy whose whole change is a number with no basis `instruction_quality` §1 accepts (`unbased-number`); a remedy that adds an unbased number beside other changes is applied with that concern written as a litmus in place of the number;
   - a remedy that adds a mechanism the subject lacks, such as a new component, channel, flag, file, contract or plan slice, whether you or the finding proposes it (`new-mechanism`: name the gap it would answer and who proposed it); a test proving a fix is not a mechanism;
   - any other remedy the finding's fix and options leave for you to design (`own-design`: name the decision it needs);
   - a destructive or rewrite finding: one that deletes a file, rewrites a unit wholesale or discards content (`destructive`);
   - a resolved PLAN whose alternative's `wins when` holds in the subject's constraints (`plan-alternative`: name the constraint);
   - a finding from a seat its ladder `±` cell flags for false positives (`read-first`);
   - a path finding whose `git grep` hits or new path fall outside `Files` and `New paths` (`path-scope`: list the hits). The caller widens the subject with those hits, re-snapshots and runs the standalone fold over the held ids.

   A refutation with no evidence, or whose first-party citation lacks local bytes (`refutation`), is held. Apply an ASK or open PLAN only when the parent supplies its authorized ID through `fold_check.py --ruled`; the digest's `source: ruling` alone grants no authority.
5. Apply each supported finding. If a finding carries `evidence`, apply it only with a VERIFIED verdict, whether or not it refutes. Do not limit application by criticality; the hold list above alone decides a hold.
6. Edit the subject files for applied findings. Before applying an `old`/`new` pair, confirm its exact `old` text occurs once at the intended target in the snapshot's file; resolve drift instead of guessing. Then apply it. Write other supported changes at their cited section.
   - **Path FIX** (`old`/`new` are paths): `git mv` the file (a plain move when `Root` is outside a git checkout) and update every reference the finding's evidence or a `git grep` (Grep outside a checkout) of the old path finds, inside `Files` and `New paths`.
   - **Code clause:** a code path is what `fold_check.is_code_path` says, and a test path is what `fold_check.is_test_path` says. A finding that changes a code path's behavior writes or extends a failing test first and observes it fail, then applies the fix and observes the test pass. The test lives under the project's test root (`adaptation.json` `tests_root`), or for harness code in a proof bound to the path (`harness_tests.py` `select_for`); the caller declared those paths in `Files` or `New paths`. A decision whose change needs no test carries `noTest: "<reason>"`; the checker never accepts the label as proof, and the unit goes to the next round's re-review seat.
   - **Commits:** on a rev subject (a worktree) commit in the worktree: the test commit precedes the fix commit, one finding per commit pair where findings are separable; report each as `commits`. On a blob subject in the shared checkout, report `changedPaths` and never commit (`orchestration` reference `writer_returns.md`).
   - A native fan-out author runs the harness's Python and Node proofs only. It leaves the project's own test suite and regression gate to a sidecar or solo dispatch, which follows the project's testing skill.
7. For every applied `old`/`new` pair, check its file with a script: `old` occurs fewer times than in the snapshot's file (or never occurred there), and for a `finding` source `new` is present. Report each result in `mechanicalChecks`, including failures.
8. Diff each changed file against its snapshot (`git diff --no-index` over the blob, read with `git -C <objects> cat-file blob <sha>` when the manifest names `objects`, or `git diff <base>` for a rev subject). List each changed design element in `designDelta` as `<repo-relative path>: <element>`: rule unit, type, signature, export, default, scope, home or deletion. Read it from the diff. An intent-preserving fold leaves it empty.
9. Write the digest to the digest file, then return the same object.

## Digest

| Field | Content |
|---|---|
| `applied` | `{id, change}`: what changed and where, one line |
| `deferred` | `{id, class, reason}`: `class` is the step 4 token, `reason` one line |
| `decisions` | `{id, taken, source, why, file?, noTest?}`: one line for each finding disposition; `taken` is the boolean `true` when the id is in `applied`, else `false`; a taken decision's `source` is `finding`, `adapted`, `fold` or `ruling` per step 3; a fileless application over several subject files names its subject-relative `file` (`changedPaths` cannot attribute it); `noTest` per step 6; any nuance goes in `why` |
| `verdicts` | `{id, refuting, verdict}` for every finding with evidence, every seat-marked refutation and every refutation you identify |
| `verdictPath` | the checker output file named in the fold prompt |
| `designDelta` | changed elements from step 8 |
| `changedPaths` | every path you created, edited, moved or deleted, as paths relative to `Root` |
| `commits` | rev subjects only: `[{sha, ids, kind}]` with `kind` `test` or `fix`, in commit order |
| `mechanicalChecks` | `{id, oldAbsent, newPresent}` per applied `old`/`new` finding |

Every input id appears exactly once across `applied` and `deferred`, and once in `decisions`. An `applied` list over unchanged files is a failed fold.
