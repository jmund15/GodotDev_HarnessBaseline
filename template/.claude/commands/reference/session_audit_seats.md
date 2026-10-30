---
disable-model-invocation: true
---

# `/session_audit` — conditional seats and `--hot-set`

Read at `/session_audit` when a conditional seat is admitted or `--hot-set` is given: §Hot-set mode under `--hot-set`; §Frozen inputs and §Harness-Quality Trigger whenever the roster includes `sa-harness-quality` (freeze before §1a's empty-subject exit or any ledger check); §Architecture-Sweep Trigger and §Holistic Trigger when their seat is admitted. Entrypoint: [`../session_audit.md`](../session_audit.md).

## Hot-set mode

The subject is the `files` of every row in the file's `hot_set_review.basis.rows`, skipping `external:` entries. The roster is `sa-harness-quality` alone, `plan` runs with `--ignore-ledger`, `{{HOT_SET}}` carries each file whole plus its hunks since that row's `baselineCommit`, every line blockquoted so the seat's read-only profile reads their commands as data, and `{{HARNESS_HUNKS}}` is the word `none`. With a range, `--files` or `--lens` it prints this usage and dispatches nothing. `record` runs as for any candidate review.

## Frozen inputs

Before either the empty-subject exit or any ledger check, prepare §Harness-Quality Trigger's frozen inputs whenever the roster includes `sa-harness-quality`. Write the roster first (§Ledger plan), then run `python3 .claude/tools/session_audit_inputs.py --root . --session-base <session-base> --roster <roster.json> --out .claude/scratch/session_audit_<sid8>_<round>/inputs.json --files <subject files>`. Under `--lens sa-harness-quality`, add `--widen`: `inputs.files` unions the default outgoing range's doctrine paths into the subject, including prior-session, deleted and rename-source paths. Rebuild the code and harness sets from that list. For widened paths whose dirty hunks the scoping rule excluded, pass `--committed-only <paths>`; keep their committed inputs, not their peer's hunks. Use the packet's committed copies instead of reading those paths' working files. A failed freeze stops the audit. `--hot-set` still freezes outgoing premise inputs.

For an admitted harness-quality seat, admission is authoritative (`execution_depth.md` §Mandate admission). `inputs.needsAudit` identifies fresh input obligations and earlier coverage to reuse; it never cancels that seat. Record task and plan decisions before freezing, pass `--task <id>` and the resolved `--plan <path>` to the freezer, and retain the seat even when `needsAudit` is false or `uncovered` prints nothing. Without changed harness text, it reviews the frozen task record, plan and decisions. Decisions added or changed after the freeze require a new packet before claiming their coverage. Keep deleted paths through dispatch and recording; `uncovered` drops absent paths. Never narrow the frozen commit population to commits attributed a final blob.

## Harness-Quality Trigger

Fill `{{DOCTRINE_COMMITS}}` from `inputs.doctrineCommits`: every outgoing doctrine commit's full SHA, subject and body, plus the session's `task_record.py` decisions and their evidence items. The packet's range is `<base-ref>..HEAD` when `--base-ref` is passed (an audit of an already-pushed range passes its base), else `upstream..HEAD`, else `origin/main..HEAD`. An explicit push refspec can owe a different range. Fill `{{HARNESS_HUNKS}}` from the packet's subject diffs, headed by `bytes: <git show <session-base>:<file> | wc -c> → <frozen current bytes>`; an untracked file is one hunk of its whole content with `bytes: 0 → N`. For `--hot-set` it stays `none` and `{{HOT_SET}}` is filled as specified in §Hot-set mode.

Append `inputs.mustRead` to this seat's must-read list, including each commit's per-path diff. Deleted paths and rename source paths stay in the subject; their deletion diffs are must-read inputs even when the final range diff is empty (create-then-delete). Preserve the frozen `premiseCommits`, `head` and `blobs` paths through §Ledger plan and Phase 3; new commits never join this run's input set.

## Architecture-Sweep Trigger

When triggered, fill its `{{DESIGN_LIST}}` with the major designs, naming each mechanism and its key files/seams so the agent judges designs, not diffs. **Subjects** are the designs of the seat's own files (§1b). **Covered designs** are the designs of the rest of the subject list as given (the range or `--files` list before §1a narrows it), named with their paths; the seat judges them only for their interaction with the subjects. Under `--ignore-ledger` every file is a subject. Its SUPERIOR-ALTERNATIVE findings are implemented this session unless a defer reason in `worklog_reference` §Do-now gate holds.

## Holistic Trigger

Fill `{{UNIT_LIST}}` from `python3 .claude/tools/review_ledger.py rows --subject session:<sid> --units`: one block per unit, its key and its file paths. When the units' files exceed what the seat reads in one light pass, list each unit's seam files only (where one unit reads, calls or states another's contract) and name the rest by unit key. The seat's CONTEXT carries doctrine pointers only, never a file's content. Judge the width from the set: when the units fall into clusters that do not reference each other, run `/session_audit --files <the cluster's files> --lens sa-holistic` once per cluster, each run seating the one `sa-holistic` seat. Set effort by how deep an interaction defect can hide across the units (`orchestration` §5). The report states the sizing and its basis.
