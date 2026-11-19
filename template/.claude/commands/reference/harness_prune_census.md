---
disable-model-invocation: true
---

# `/harness_prune` — census rulings

Read at `/harness_prune` §Census rulings. Entrypoint: [`../harness_prune.md`](../harness_prune.md). The litmus
is [`../../reference/mechanism_litmus.md`](../../reference/mechanism_litmus.md).

## Read

`git fetch -q origin main-check-state`, then read `mechanism_census.json` from that ref beside `main_check.json`. The
census's `changedSinceRuling` array is the work list: one row per item that took a fix commit, a red, a flaky row or
a deny since its last ruling, with the item's `date` and each event count. Never recompute the list: a re-measured
count with no new event is no change, and a renamed file keeps its ruling. An item absent from the array is
unchanged. When the ref carries no census file, write `census: never published` in the section and stop the step.

## Propose

Send the changed items to judged jobs, batched to fit each job's context. Each job reads its items' census rows
(runs, seconds, fails, close reds, clearing kinds, flaky, denies, fix commits and their subjects) and proposes keep,
fix or cut per item, with the evidence:
- **fix** — the item pays but fails L4 or *A fix re-asks the litmus*: name the decision the cases share and its one
  owner. A chain of fixes to one decision is one fix ruling, never one per case.
- **cut** — the item fails L1, L2 or L3: name the cheaper catcher or the observation that retires it.

A keep stands without reaching the owner; fix and cut proposals go to the owner.

## Rule

The owner rules every fix and cut proposal and approves the cut list before anything is deleted. Append one row per
disposition to `.claude/census_reviews.jsonl` and commit it: `date`, `item`, `kind` (proof, hook, tool or script),
`proposal` (keep, fix, cut or split), `status` (`standing` for a keep, `awaiting-owner` for an unruled proposal,
`approved` or `rejected` for the owner's ruling), `litmus`, `evidence`, `change`, `source`. The item's latest `date`
is its next comparison point, so a ruled item re-lists only on new facts.

## Per-cut steps

For each approved cut, in this order:
1. `git grep -n '<the cut file's path>'` for live callers. A caller keeps the file; the cut is not taken.
2. Remove the proof's `proof_excluded` entry from `.claude/skills/project_subsystems/adaptation.json`.
3. A file the baseline ships is retired by publishing its deletion: delete it, `baseline_sync.py judge <path>
   --verdict push`, and publish it with its push-judged dependents (`/sync_baseline` §Publish). A local deletion
   alone returns with the next pull, and a shipped tool left without its proof goes unproven. Any other file's row
   leaves `baseline.lock.json` with `baseline_sync.py forget <path>`.
4. Remove its hook registration from `settings.base.json` or `settings.project.json`, then run
   `python3 .claude/tools/baseline_sync.py compose`.
5. Delete the file and commit the cut with its record row.

Done when `python3 .claude/scripts/harness_tests.py --tiers` lists no deleted proof, no `proof_excluded` row names a
deleted file, `settings.json` parses and names no missing hook, and `/sync_baseline`'s check reports the removed rows.
