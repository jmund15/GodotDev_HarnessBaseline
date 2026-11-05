# Drive close

`drive_spine.md` §Stages routes the Review and Close stages here. This file holds the close sequence those stages run.

## Close

1. Verify by chain position per `change_control` §Gate cadence, which also owns fix re-verification. At each Part's
   close, run its plan's root-acceptance rows (`plan_file_format.md` §Root acceptance) on the Part's candidate through
   the project's agent playtest command; at drive close, before the close gate, run the rows of every plan serving the goal on their
   merged candidate the same way. Record each requirement met, partial, not met or owner-judges with its run
   evidence; a requirement no row maps is not met, and only taste is owner-judges (`appetite_invariant.md`
   *Usability is analytical*). A partial or not-met row is repaired as a slice and re-run before the gate, and no
   push precedes this verification, even after a green gate. Step 6 writes the record into the Part's roadmap row
   (`roadmap_triggers_mvp.md` §6.10, the `complete` row). The owner's play checklist is built from these rows, and
   an owner-deferred, partial or unprobed row is never reported met.
2. Candidate review (the Candidate review row), run only at the drive's close (`change_control`
   §Gate cadence). Any other close runs step 1, and the seam review when its row admits it. The chain
   position places this step, not the commit: a plan that commits per job runs it over the landed commits.
   When the Plan file row admitted none, first write the conversation's short plan to
   `.claude/scratch/<task>/short_plan.md` and pass that path. Run
   `/session_audit --files <the drive's files> --plan <plan path> --task <task id>` and read its report: the audit's fold transcribes the literal text fixes and the drive applies and rules every other finding, the report's `held` ids and failed checks included, per `orchestrator_action_protocol.md` Step 1.5 and Step 4. The drive's files are
   `git diff --name-only <base>..HEAD` plus the session files the Session File Identification Procedure reconciles
   (its Step 4) that lie in the plan's write set, or in the short plan's file list when no plan file exists. The
   report holds an `sa-architecture-sweep` verdict or the ledger row that covers it. It reads the review ledger
   (`tools/review_ledger.py`, `orchestration` §2).
   Every finding is dispositioned (applied by the fold, with its `applied` entry; fix now by the drive or an executor job; worklog under a `worklog_reference` §Do-now gate reason, or refuse with
   evidence; or below bar: noted); an ASK goes to the adapter's owner stop, or halts the drive under H3 when there is none. A fix-now disposition routes per `orchestration` §2 *Later rounds*. Close is done when every finding has a disposition; an undispositioned finding stops the commit sequence.
   The close report lists each seam review the drive's boundaries admitted: the runs from
   `review_ledger.py rows --task <task id>`, the skips from the task record's decisions.
3. When preparing a branch for a PR or merge, run `/pr_ready` over the readiness checks the diff admits, reusing
   current coverage. A clean battery means every admitted lens returned and none reported a critical finding; one is H4.
4. Close out dispatch ratings (`/orchestration_metrics` §Incremental rating, *Phase close*) when consumed dispatches
   remain unrated.
5. Commit per the project's commit rules (`change_control` §Commits). The drive does not push; a later push follows
   `CLAUDE.core.md` §Git, and one the Owner approval row admits waits for the owner.
6. The adapter's write-back, which its header's owner stop or close clause names: the roadmap flip for a Part,
   `/worklog complete` with the commit ref per item in the session it lands, or none.
