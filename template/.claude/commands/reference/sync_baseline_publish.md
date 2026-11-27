---
disable-model-invocation: true
---

# Baseline judging and publication

## Verdicts

`judge <relpath> --verdict ...` accepts `push`, `keep-local` and `fork`.

- A `keep-local` verdict clears `check --strict` for the content it judged, so one verdict needs no fork, except on a `local` row for a file the baseline ships (`local-shadows-upstream`). On a tracked row that never shipped, `keep-local` sets it `local`.
- A row you are unsure of gets `--borderline`; show it to the owner, who confirms with `judge <relpath> --verdict <v> --confirm`.
- `push` runs the identity scan and refuses a hit. Pass `--accept-hit <id>` only for a legitimate generic use. `push` refuses a new row with no layer; the entry command's **Candidates** assigns one.
- `push` refuses a row whose dependency is neither in sync with the baseline nor judged `push`. Exceptions: a `forked` dependency, a `local` row the baseline ships, and a `local-modified` dependency the baseline copy of the row already cites. `closure` lists the dependencies first. Judge each dependency `push`, or judge the citing row `keep-local` when the dependency never ships.
- `push` refuses a row whose baseline copy changed since the row's base (`diverged` or `upstream-updated`), because the push would overwrite that change. Run a bare `pull` first. A push that deletes a file or removes or reshapes a top-level Python name prints a `dependents:` line naming the shipped rows still citing it; publish them with it.
- A Python or Node proof row (`.claude/tests/test_*.py`, `*_test.js`, `*_test.mjs`) runs in the shipped set: the baseline's files at or below the row's layer plus every row judged `push`. The publish PR's CI runs it when the pinned baseline carries `.github/workflows/baseline.yml`. Without that workflow or with `--run-proofs`, `push` runs it here and refuses on red, naming the failing lines. Either way a red means the proof reaches a file consumers lack at run time, so ship that file or keep the proof local. `push` also refuses a new tool without a shipped proof. Track the proof first or judge it `push` in the same call.
- Every `judge`, `classify` and `ignore` prints the layer-closure findings its write adds. Fix each before publish, which refuses them.

A delegated judging fan-out pins a fanout-tier row for every row's first verdict (a rubric verdict, so cost leads). A stronger fanout-tier row re-reads only its `keep-local` and `--borderline` verdicts. The identity scan, layer closure and dry run refuse a `push` that leaks a token, breaks a layer or fails a proof; project-specific text with no token passes all three, and nothing re-reads it.

## Publish

Every publication is one `publish` transaction: identity scan, manifest check, `audit_baseline.py --strict`, the template battery, then a PR. The PR merges once the ruleset's required `root` check passes. No other lane blocks it, and a red lane is a main-check row. A merge without that check needs `--no-ci`, permitted only when the pinned baseline has no workflow, reporting CI skipped, never passed. Once a commit carrying `push` verdicts reaches the default branch, publish those rows as one small PR then, never batched to session end, because each batch leaves every other consumer a session behind. Validation runs on the local commit, so fix every red before the one land.

**Subject and proof travel together.** Publishing a row whose behavior an upstream proof asserts breaks that proof unless the proof publishes in the same transaction: the API it asserts arrives only with the publication. When the consumer's proof cannot publish (it names project content), the subject waits too, or the baseline copy is fixed in a companion PR that carries the subject.

1. Pick the source:
   - A universal change committed in this project: commit it, then `closure <rows you changed> --carry <base>..<head>`. It prints each dependency consumers would not receive and its citing row, transitively; the push-judged backlog count; and, per row, the commits since its baseline copy outside the range. Pick the scope from that, then `judge <scope rows> --carry <base>..<head>` carries each row's push verdict along the range, applies the [verdict refusals](#verdicts), and prints `carried:` or `not carried: <row> (<reason>)`. Judge a `not carried` row with `--verdict push --commit <sha>` (reads that commit, not a peer's staged copy). Then `paths --verdict push --unshipped --json > .claude/scratch/publish_rows.json` and `publish --from-commit <sha> --rows .claude/scratch/publish_rows.json --dry-run`.
   - Planned universal work: `author start` prints a worktree on `author/<session8>`. Edit and commit there, then `publish --from-worktree <path> --dry-run`. The dry run's `validate` step runs every check and the battery in the mode below; never run the full battery by hand first.
2. Show the owner the dry-run journal (`.claude/.cache/baseline-publish/<id>.json`): collected rows, their verdicts and step 1–5 evidence. Step 1 refuses a shipped row that is `diverged` or `upstream-updated`: run a bare `pull`, or `--over-upstream <relpath>` records the decline. Fix every red listed before the redo. An invocation that already said to publish skips this pause. A dry run red in steps 1–5 is fixed and redone with `publish --resume <id> --dry-run` only while the source commit is unchanged; a changed one needs a fresh `publish`. Show the repaired dry run again.
   - Validation runs the manifest check, `audit_baseline.py --strict` and the battery count, and leaves the template battery and root proofs to the PR's CI (`mode=ci`). CI runs them on Linux; the land's main check runs the same proofs on Windows. `--full-battery` also runs both locally (`mode=full`), to reproduce a red on this machine. Adding it to `publish --resume <id>` on an unpublished journal resets validation and the steps after it.
   - The PR's CI is the Linux check: `publish` runs no local Linux lanes, which are slower than CI. `baseline_linux_preview.py <id>` reruns a red lane's batteries in WSL to reproduce it.
3. `publish --resume <id> --open-only` pushes `publish/<id>` and opens the PR before the source commit lands, so CI runs beside the land's gate. It records `pr_url` and merges nothing. For a commit source, `publish --resume <id>` refuses until the source is on `origin/main` and leaves the PR open. Once the source is there, resume applies the journal's CI policy, merges, moves the lock pin and checks every published row. The publish commit body names the source, each row with its operation and a `Why:` line where `autolearn_outcomes.jsonl` records a reason, so a consumer's `git log --grep` returns what shipped and why.
   - An identity hit (project name, abbreviation, home path, topology token or content noun) stops the scrub step with a hit id. Fix the source or accept the hit as [Verdicts](#verdicts) describes.
   - A red required check stops before the merge, leaves the PR open and records `ci_run_url`. Fix the source, commit and run a fresh `publish`: a new source commit starts a new journal. Close the red PR with `gh pr close <number>`.
4. After a `--from-worktree` publication, `pull` the rows step 8 names. `publish --abandon <id>` ends a journal you will not resume; `publish --sweep` clears leaked worktrees.

Done when every journal step is green and `check --strict` exits 0.
