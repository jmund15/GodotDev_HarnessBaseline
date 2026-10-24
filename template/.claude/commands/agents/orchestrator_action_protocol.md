---
disable-model-invocation: true
---

# Orchestrator Action Protocol

One owner for consuming review findings. Keep original evidence, verify the claim, then act under the caller's authority.

## Finding Schema

Use `.claude/schemas/review_findings.json`; do not maintain a second schema example here. A finding names its agent, action, category, location, claim and rationale. The schema leaves `old`/`new`/`question`/`options`/`scope`/`refuting`/`evidence` optional, but a lens must still supply the fields its action needs: FIX carries `old`/`new` for one span; `new` is the literal replacement text, not instructions to the applier. A fix the seat can only describe is a PLAN with `scope` and `options`. PLAN carries `scope`; a resolved PLAN carries `options` with `options[0]` the recommended approach (its `question` may name what it settles), and an open PLAN carries no `options` and sets `question` to the decision the reviewer could not make. ASK carries a `question` naming the owner-held input. An option after the first in any action, and every ASK option, ends `wins when <condition>` naming one falsifiable fact. A reviewer deletes an option it rejects and keeps a status-quo option only with a real condition. A resolved PLAN may carry one alternative as `options[1]`; a FIX with an alternative states its own edit in one line as `options[0]` and the alternative as `options[1]`. A number that `new` or an option adds to a rule, guard, threshold or default names a basis `instruction_quality` §1 accepts, or the text states that concern as a litmus instead. A source-refuting finding sets `refuting: true` and cites verbatim quotes in `evidence: [{file, quote}]`. A downstream consumer, such as a PR pipeline, applies FIX and resolves ASK/PLAN from those fields, so a finding missing them is not actionable even though it validates. The review result's advisory `tier-shape` flag (in its top-level `flags`) reports a finding that breaks these shapes.

### Reporting Filter

Report a finding only when acting on it would prevent a bug, enforce an explicit project rule, or meaningfully improve the result. There is no low-priority tier: either it is worth acting on or it is not worth reporting. A false positive costs the developer time, so when in doubt, do not report.

### Action Tiers

| Action | Meaning |
|---|---|
| FIX | One supported, concrete edit pair (`old`/`new`); verify it against current source |
| ASK | A choice only the owner can make: the answer turns on the owner's intent, appetite, authority over a plan ruling or over owner-authored text, an irreversible step, or a fact only the owner holds (what they authored or plan to author, what is deprecated, whose session or process it is). The `question` names that input. The recommended default is `options[0]`, marked by its position and never by a `(Recommended)` label (the tier-shape flag reports one), and it never demotes an ASK. A reversible, in-scope choice that turns on none of these inputs is FIX or PLAN. |
| PLAN | A change larger than one edit pair: names `scope`. Resolved when it carries `options`, `options[0]` its recommended approach (a `question` may name what it settles); open when it carries none, its `question` naming the missing decision |

A finite set of alternatives does not make an ASK: a reviewer who can recommend files FIX (one edit pair) or PLAN (larger). Severity is triage, not permission to discard a valid improvement. `NOTE` is parent synthesis, not an agent escape tier.

### Category Tags

`bug` changes correctness; `rule` violates an explicit obligation; `improvement` makes the result better without claiming either. Cite the real obligation or consequence.

### Critical Flag

Use `critical` for material correctness, safety or silent-failure exposure. Missing means false, not unreviewed. Do not turn an unverified hypothesis into a critical fact.

## Orchestrator Action Protocol

### Step 1: Merge & Deduplicate

Preserve every original finding with an engine-owned source ID before sorting/grouping. A location is an anchor, never identity. Distinct defects at one line survive; duplicate contributors retain provenance. A later round's findings dedup against the earlier rounds' ledger by `orchestration` reference [`later_rounds.md`](../../skills/orchestration/reference/later_rounds.md).

`review_fanout.js` returns immutable source records plus a derived view. Its optional merger groups source IDs; original facts and edit proposals remain authoritative. Invalid grouping falls back to originals. Report received/valid/grouped counts and missing coverage separately. Grouping is not verification or proof that every member has been fixed.

### Step 1.5: Verify Findings

Read the result's `verification` and, in review mode, `held`, `failures` and `nextRound` from the block's `report` path; the report's verdict counts must cover the received findings. Accept unread a fit-for-tier seat's finding whose quotes are VERIFIED and whose id is not in `held`. Read the cited source first-party for every id in `held` and for every UNVERIFIED or UNCHECKED claim before acting on it. `held` carries every id the fold deferred (the hold classes of `review_fold.md` steps 4 and 5), every id a failed check or an omission left unproven (its class is the failure kind, `unapplied` for an unchanged file, `unrouted` for a finding no fold job received, or `unreported` for an omitted id) and every ASK and open PLAN; for a run with no fold it also carries every FIX and resolved PLAN as `unfolded`. A VERIFIED quote proves presence, never support. A report's `status` passes when every claim is VERIFIED; missing or unreadable verification is UNCHECKED, never a pass. A verification block that is absent or `unavailable` means the verifier never ran: the Stop audit's message names its error, and the next step is to rerun it (`post_check.py review|claims --workflow-dir <run dir> --out <file>`), not to read the audit's recomputed quote differences as mismatches. Its `outcome` passes when no subject-wide check failed and no source is incomplete: a failed check on one id holds that id alone, the fold's checks prove what it applied, and each claim it did not apply reaches you through `held` or `unverified`. A fold is accepted when its report's `outcome` passes: a `provisional` status, an author's digest or its exit is never acceptance.

For grouped views, resolve the original source IDs. Different edit pairs are separate actions; do not concatenate snippets or treat one applied patch as completion of the entire group.

#### Death resume

Use `resumeFromRunId` to recover a run that died. Cached replay requires a byte-identical engine and rails; a resume after an engine or rails edit re-runs completed agents at full cost because prompt identity is unverified. Read the dead run's `<transcriptDir>/journal.jsonl` for one result line per completed agent before paying again.

Before issuing the resume, find what changed since the dead run began: the run's start is its earliest `agent-*.meta.json` (the journal's mtime is not the start), and the sources are the engine script and the rails files under `.claude/guards/`.

```bash
find <scriptPath> .claude/guards -type f -newer "$(ls -tr <transcriptDir>/agent-*.meta.json | head -1)"
```

A listed file, or a run directory with no meta file, means the resume re-runs completed agents at full cost. Resume anyway only when that cost is acceptable; otherwise recover from the journal.

For a completed run's shed output, read its `archive.commands` pages (`items`, `merges`, `lenses`, `reports`, `gaps`) and fold spill files. They hold every agent return, not the fields the engine computes: contradictions, flags and a claim's applied confidence, contested and corroboration marks. Recover a shed computed field with `archive.commands.full` (`session_digest.py --workflow-full`): it replays the run locally through `tools/workflow_replay.mjs` with no model call, and names the gap when it falls back to the archive pages.

### Step 2: Present Unified Report

Start with a bounded overview: outcome, counts, unresolved/critical IDs and the full artifact. Retrieve the complete result per `orchestration` §4 before accounting. Unverified findings are visible first. Order: critical first, then FIX → ASK → PLAN, then bug → rule → improvement. Preserve all original evidence and account for every item before acceptance. A display limit never drops a finding.

Each displayed item states the defect, evidence, consequence and next action plainly. Fetch exact old/new/source details when acting; do not duplicate every full block in the parent merely to report it.

### Step 3: NOTE Synthesis (Orchestrator-Only)

Reconcile cross-lens patterns and contradictions against source evidence, not majority vote. Notes may connect findings; they do not replace required dispositions.

### Step 4: Execute Actions

Use the caller's existing approval or explicit unattended authority; do not add a second confirmation ritual. Otherwise obtain approval for the proposed actions before applying them.

The parent rules only `held` ids and failed checks. The fold author applies every other FIX and resolved PLAN (`review_fold.md` step 4). After the round's last ruling, one refold applies every ruled id and every failed-check id of the round, briefed with the report: `nextRound.refold` names its findings file, files and commands. It splits into more jobs only by `orchestration` §2's write-set map or the context bound (`orchestration` reference `drive_dispatch.md`), and those jobs stay one round. A fix applied outside a fold records with `post_check.py review --findings <file> --applied <ids> --files <paths> --stage <stage> --subject <subject> --roster <roster>`. When the same check fails again on the same id after a refold, read the failure and repair an in-scope cause; otherwise record its blocking fact and use ASK only when the missing input is owner-held.

Findings from a source with no review engine (an audit report, a lens run on `dispatch.js`) and `unfolded` holds take the standalone fold. Apply an edit smaller than its handoff inline and record it as Step 4 states above (`orchestration` §0). Otherwise snapshot the subject per *Fold subject* below, then dispatch one `dispatch.js` author job briefed with `review_fold.md` and its `Root`, `Snapshot`, `Files`, findings, verdicts and digest paths. The job declares `fold: {findings, digest, snapshot}` and `ledger: {stage, subject, roster}` with the originating stage, subject and roster (and task when named); the engine's close stage runs review mode. Consume its result per Step 1.5. A finding held for `path-scope` widens `files` with its listed hits, re-snapshots and folds again over those ids.

#### Fold subject

Name the subject `{root, files, snapshot}` before a fold: `python3 .claude/tools/post_check.py snapshot --root <root> --files <paths> [--base <sha>] --out <manifest>` (`--base` makes a rev subject, for a worktree). Every fold subject over code declares its tests at the snapshot, because the checker reads only the declared paths: each code path's existing test file (its suite under `adaptation.json` `tests_root`, or the proof `harness_tests.py` binds to a harness path) in `--files`, and a test file the fold may create in `--new-paths`. On `review_fanout.js`, the workflow guard fills `args.fold.repoRoot` from the session's working directory (a caller passes it only to override): a seat's absolute citation of `<repoRoot>/<root>/<file>` routes to that file, and a citation of any other absolute path, a worktree copy included, never routes.

- ASK: obtain the real owner decision; do not infer an ambiguous answer. An ASK whose `question` names no owner-held input is a mislabeled FIX or PLAN: rule it from the option whose `wins when` holds, else its first option, and write "mislabeled ASK" in its disposition. Keep it an ASK when that option narrows, defers or declines work the plan or the owner named, or rests on a claim about the owner's intent, authored content or directory history that no cited source states. Under `/overnight`, make defensible reversible choices and park actual blockers.
- PLAN: ratify a resolved PLAN against the subject's constraints, checking its alternative's `wins when` first, and resolve an open PLAN first. Then follow the planning contract. Do not quietly defer an in-scope, addressable item.
- `new-mechanism`: rule as the design owner. Prefer a remedy the existing design carries, or decline one whose requirement no plan or owner states; adopt the mechanism only when the requirement needs it, and its units then draw their review.
- Every ruled id (answered ASK, mislabeled ASK, resolved PLAN) joins that refold.
- A non-empty `nextRound.uncovered` names coverage still missing; before acceptance the consumer checks those units by the route `orchestration` §2 *Later rounds* picks and records it.

Record each source finding as fixed, rejected with evidence, or blocked by a named fact. A grouped view or successful tool exit is not its disposition.

## Claims to Refuse

Do not predict passing tests, claim an unread artifact is correct, or use “should work” as verification. Cite actual results or state what remains to run. A completed decision is re-checked at its premise before a clean verdict; `tools/task_record.py check --rerun` names the stale and unrepeated ones. Keep observed, inferred and unknown separate.

## Verdict Logic

| Verdict | Criteria |
|---|---|
| APPROVE | No unresolved critical or scope gap; no remaining actionable findings |
| APPROVE WITH NOTES | No unresolved critical; accepted actionable noncritical notes have supported dispositions |
| REQUEST CHANGES | Unresolved critical, missing required coverage or a blocking owner/design decision |

The calling command may define a stricter gate. Preserve that contract without replacing it with an arbitrary score or a count of agreeing agents.
