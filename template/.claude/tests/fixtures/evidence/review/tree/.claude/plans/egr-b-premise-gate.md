- **K5 The seat's input today omits commit messages.** `/session_audit` §Harness-Quality Trigger fills `{{HARNESS_HUNKS}}` with `git diff <session-base>..HEAD -- <file>` plus uncommitted hunks per file (`session_audit.md:115`), so the seat never sees a commit message. The seam is a second placeholder, `{{DOCTRINE_COMMITS}}`, that the trigger fills (slice 3).

- **K6 `review_ledger.py owed` does not narrow to doctrine files.** `cmd_owed` (`review_ledger.py:2094-2138`) resolves the mandate set through `resolve_candidate`, which without `--roster` is the unconditional set and excludes `harness-quality`, and it checks every file of every commit in `@{u}..HEAD` (or `<base-ref>..HEAD` with no upstream), code included. It has no `--mandates` option, while `uncovered` has one (`review_ledger.py:2253-2256`). It also reads `@{u}..HEAD`, not the ranges of an explicit refspec push that `git_guardrails._push_ranges` resolves (`git_guardrails.py:633-702`). The plan factors the range logic out of `cmd_owed` into one function that `cmd_owed` and the push guard both call, adds `--mandates` and a doctrine file filter, and shares the doctrine-path predicate (K2).

- **K7 Coverage is per commit blob.** `cmd_owed` overrides each file with its blob in that commit (`review_ledger.py:2129-2131`), and an audit row covers the blob it read. A doctrine file changed by two unpushed commits therefore leaves the earlier blob uncovered after an audit at HEAD. The push guard judges each doctrine path at the range's final blob and attributes it to the last in-range commit that changed it; earlier commits' premises are still read by the lens because `{{DOCTRINE_COMMITS}}` lists every doctrine commit in the same ranges the guard resolves (Mechanism 2). Slice 1 proves the two-commit case. This is traced, not observed: the plan states the intended semantics, and slice 1's RED case fixes it.

## Design

**Mechanism 1: the premises block, in the commit message, no hook.** Each doctrine commit's message carries a fenced ```` ```premises ```` block, one JSON object per line `{"claim": "...", "evidence": [items]}` in the plan-A contract. An owner-directed edit uses one `{owner}` item; a session that executes another session's ruling names the task record in it (`{owner, task}`, plan A D2), so the quote verifies against the row `record_condition` checked when the ruling was recorded. Nothing judges the block at commit time: no commit-msg hook, no proof wait, no dispatch.

**Mechanism 2: the close audit judges the premises, in an existing seat.** The `sa-harness-quality` lens gains one per-commit step (after the per-hunk list, before the per-file table). For each doctrine commit in `{{DOCTRINE_COMMITS}}` it reads the `premises` block and the cited evidence, and reports a finding when:
- a doctrine commit has no `premises` block, or a premise is missing for a stated cause;
- a count's command or evidence does not read every counted item (a category label or a sample);
- a diagnosis is not shown from evidence.

### M1 Tracer: an unaudited doctrine commit's push is refused, an audited one passes

**Slice 1: tracer.** Factor `owed_range` out of `cmd_owed`, with `--not --remotes` as a parameter only the guard passes; add `owed --mandates` and the doctrine filter on the shared predicate; add `doctrine_audit_verdict` wired at the push site with a minimal deny text.
Test intent: RED first in a planted repo (`tests/test_git_guardrails*.py`, ledger rows planted through `HARNESS_REVIEW_LEDGER`): a push of an unaudited doctrine commit is refused; the same push after a `harness-quality` row covering its blob passes; a code-only push is not checked; a push whose only unpushed change is `.claude/auto-memory/x.md` is not denied (K2); a doctrine file changed by two unpushed commits passes once the final blob is covered (K7); a doctrine commit from an earlier session plus a no-range `/session_audit --lens sa-harness-quality` passes the push (slice 3 widens the subject, slice 6 proves it live). `tests/test_review_ledger*.py` keeps `owed`'s existing output unchanged for its current arguments.

### M2 The guard's full contract and the doctrine text

### M4 The command surfaces

**Slice 5: `/codify` and `/autolearn`.** Step 0 Done line in `codify.md`, the self-check citation in `autolearn.md`, and `baseline.lock.json` rows.
Test intent: static, `verify_doc_citations.py --kinds path,command --json --only missing` over the edited files has no missing path; the deny text's cited paths resolve.

### M5 Live proof

## Verification

- **`git_guardrails.py` and `review_ledger.py`:** `tests/test_git_guardrails*.py` and `tests/test_review_ledger*.py` pass every deny and allow case above, including the must-allow cases; the hook compiles and fires on a crafted push in a scratch repo; `owed`'s existing output is unchanged for its current arguments.
- **`task_record.py`:** `tests/test_task_record*.py` pass the add-time refusal, `--rerun` and the `--reproduction` replacement message.
- **Doctrine-path predicate:** importable by `review_ledger` and `git_guardrails`, and the existing tests of `harness_edit_skill_reminder.py` pass.
- **Lens and trigger:** `lens.py get --shared sa-harness-quality` shows the step; `review_ledger.py keys --check` is clean; a dry run fills `{{DOCTRINE_COMMITS}}`.
- **Doctrine edits:** `verify_doc_citations.py --kinds path,command --json --only missing` over each edited file shows no missing path that is not a file this plan creates; the retired `--reproduction` has no stale reference (`git grep` over the paths in K13 finds none beyond the parseable stub); `instruction_quality` §5 audit recorded for the `CLAUDE.core.md` clause.
- **Whole feature:** slice 6's live close audit. Proofs are staged with `git add -f`; the harness suite `python3 .claude/scripts/harness_tests.py --close` runs at close per `rules/harness_tooling.md`.

## Risks, and what reverses each decision
