---
description: Prune or archive stale plans, worktrees, scratch and project artifacts, and rule mechanism-census items
disable-model-invocation: false
---

# /harness_prune — classify and archive prunable harness surfaces

Sibling of `worktree_prune.md`'s classify-report-recommend shape, with automatic actions limited to the Plans, Scratch and project-step procedures below.

## Argument

`/harness_prune` — classify all live plans, then run the Worktrees, Scratch, project, Strays and Census steps. `/harness_prune <name>` — classify one plan and skip those steps; no file at `.claude/plans/<name>` → report `No plan named <name> in .claude/plans/.` and stop without changes.

## Scope

Surfaces: `.claude/plans/*.md` (top-level, excludes `archive/`), the top-level entries of `.claude/scratch/` (the Scratch step) and the surfaces each project step names (step 6). Each surface has a proactive owner that archives at the moment its lifecycle ends (`/session_end` for plans; a project step's own commands for its files); this command is the retroactive sweep for what they missed. The Strays step lists unreferenced tracked entries at the `.claude/` root for the owner to judge. The Census step rules the mechanisms the monthly census shows changed since their last ruling. Composes `/worktree_prune` for `.claude/worktrees/` as a sub-step — that file is baseline-tracked verbatim and is invoked unchanged, never reimplemented inline. `.claude/reference/` is out of scope.

## Procedure

### 1. Enumerate

Enumerate top-level plans as two sets:

```bash
git ls-files -- ':(top,glob).claude/plans/*.md'
git ls-files --others --exclude-standard -- ':(top,glob).claude/plans/*.md'
```

Label each row `tracked` or `untracked`. Both sets empty means the Plans section says `No live plans`; still run the Worktrees and Scratch steps and write the report artifact.

### 2. Classify each — read the artifact, never the filename

Classify each file from the plan's own claims: its header (title, `**Roadmap:**` path and Part, `Constraints`/`Out of scope`), its closing Definition-of-Done or status section, recent history on its path (`git log --oneline -- <path>` for completion banners), the status row of any Roadmap Part it cites, and one targeted Grep/Glob per row of its `Surfaces touched` table against the current tree. Read each field until the verdict is evidenced; that table is the canonical list of what the plan claims to have built.

Classify lifecycle:
- **SHIPPED** — the named deliverable is verified present, or the file carries an explicit completion banner, or the cited Roadmap Part reads `complete`.
- **STALE-SUPERSEDED** — a later commit or newer plan explicitly supersedes it. Name that artifact.
- **ACTIVE** — partial or no implementation.
- **UNCLEAR** — evidence does not reach another verdict. Name the gap.

Snapshot the active owner goal, `Claude/Meta/Development-Focus.md`, authoritative Worklog priority rows, and live session edit paths before classifying, and reuse that snapshot for every plan. Classify pertinence in this order:
- **STALE** — SHIPPED or STALE-SUPERSEDED.
- **BLOCKED** — UNCLEAR, or ACTIVE with a named unsatisfied dependency/owner fork.
- **CURRENT** — remaining ACTIVE work named by any snapshot source.
- **FUTURE** — remaining evidenced ACTIVE work.

Each plan row cites the matching evidence and one next action or evidence gap.

### 3. Auto-archive vs. ask

- **Tracked, clean SHIPPED or STALE-SUPERSEDED with surviving evidence** → prepend `**Archived:** <date> /harness_prune — <evidence clause>` and `git mv` into `archive/`. The move is reversible and needs no prompt.
- **Untracked or dirty** → never move, regardless of verdict. List the state and next action. Exception: a plan the invoking `/session_end`'s Phase 0 found this session executed, whose `**Roadmap:**` header is absent or `ad-hoc` (Phase 5.5 never reaches it), is this session's; when every slice is delivered or a newer plan replaces it, move it into `archive/` like a tracked one (a plain move when untracked).
- **UNCLEAR or unsupported terminal claim** → do not move; list the exact evidence gap.
- **ACTIVE** → no move; report pertinence and next action.

Never archive on filename/title alone (`feedback_classify_by_the_artifact_not_its_label`). Plans are never deleted; archiving only ever moves a plan (`feedback_dead_config_triage_not_delete`). Scratch deletion happens only through step 5, Scratch.

### 4. Compose the worktrees surface

Invoke `/worktree_prune` unmodified via the Skill tool and fold its own table into this command's report under a second heading. Its no-auto-remove behavior is untouched — `/harness_prune` never removes a worktree on its own authority; that stays `/worktree_prune`'s call.

### 5. Scratch

Scratch is gitignored, so an entry exists only on this machine. Owner policy: keep what is still locally relevant, archive to the vault what a later session could need, delete the rest. `scratch_prune.py` makes the deterministic calls (git checkouts, references, recency, vault reads); a model judges recall value.

Before step 1, run `python3 .claude/tools/scratch_prune.py trim`: it deletes old bulk files inside cited folders and needs no verdict. Its docstring owns the thresholds. A non-zero exit blocks the step; report its `trimmed=` line.

1. Run `python3 .claude/tools/scratch_prune.py inventory --out .claude/scratch/scratch_inventory_<sid8>.json`. A non-zero exit (buckets do not reconcile) blocks the step. Rows marked `unverified` had a vault input unread; give them no verdict, because `apply` refuses them.
2. Verified CANDIDATE rows with no `proseFiles` get DELETE with reason `no prose` and need no dispatch. Send the rows with `proseFiles` to the `dispatch` workflow at the fanout tier, resolved through the ladder. Size each batch so its rows and prose excerpts fit the worker's input, with enough of each prose file to judge its findings, decisions or measurements. Pass each batch's rows as a brief file; the job returns `{name: {verdict, reason}}`. A row's `referrers.prose` lists the notes, plans and logs that mention it: they show what a later session may look for, and they never hold a row (only `referrers.code` does). Verdicts:
   - ARCHIVE when the prose holds findings, decisions or measurements a later session could need, and no tracked doc already carries them. When the keepable files are a small part of a large entry, add `archiveFiles` (paths relative to the entry): they reach the vault and the rest goes;
   - DELETE for logs, scripts, JSON, dumps, briefs and superseded drafts;
   - KEEP only with a stated reason.
3. Merge every verdict into `.claude/scratch/scratch_verdicts_<sid8>.json`. The step is done when every verified CANDIDATE row has one verdict. Recover a missing or failed batch from its spill first; re-dispatch it only with narrowed input or after a transient failure. If a verdict is still missing, the step is blocked and nothing goes to `apply`.
4. Run `python3 .claude/tools/scratch_prune.py apply --inventory <inventory path> --verdicts <verdicts path>`. A verdict may name any inventory row, a KEEP-* row included, except that `apply` never removes a row holding `gitWork`: that row stays for the owner (KEEP-GIT in the report). It aborts before any action on an invalid or missing verdict, skips a row whose mtime, `gitWork` or code referrers changed since the inventory, removes a registered worktree through git, and exits 1 when any row failed.
5. Write the `### Scratch` report section from the inventory and `apply` output.

### 6. Project steps

A project adds a sweep of its own surfaces as a `.claude/commands/reference/prune_project_<name>.md` file. Follow each one present, in name order, and write the report section it names. With none: write `### Project steps` as `None.` and continue.

### 7. Strays

Run `python3 .claude/tools/root_strays.py list`. It names tracked top-level `.claude/` entries that no other tracked file mentions and that sit past the tool's recency floor. This step only reports: an unreferenced entry may still be read by a person or loaded implicitly, so the owner decides each one, and a removal takes `git rm`, plus `baseline_sync.py forget` when the file has a lock row. Write the `### Strays` section from the output.

### 8. Census rulings

Run after Strays. Follow [`reference/harness_prune_census.md`](reference/harness_prune_census.md), then write the
report's `### Census` section from its outcome.

### 9. Write and report

Write the complete report to `.claude/scratch/harness_prune_<sid8>.md`, using the current session id. A `/worktree_prune`, Scratch, project-step, Strays or Census failure still writes every heading, records the exact failure under its heading, and makes the phase blocked rather than completed.

```markdown
## /harness_prune — <date>

### Plans
| File | Git | Lifecycle | Pertinence | Evidence | Next action |
|---|---|---|---|---|---|
| ... | tracked/untracked | ... | STALE/BLOCKED/CURRENT/FUTURE | ... | ... |

Archived automatically (N): <file> — <evidence clause> ...

### Worktrees
<worktree_prune's own table and recommendations, verbatim; or its exact failure>

### Scratch
| Bucket | Count | Bytes |
|---|---|---|
| KEEP-WORKTREE / KEEP-GIT / KEEP-REFERENCED / KEEP-RECENT / CANDIDATE / TOTAL | ... | ... |

Vault inputs: read N, unread N (unreadable N, unattempted N), absent yes/no; unverified rows N.
Apply: archived N, deleted N, kept N, skipped N, failed N (<failure list>).
KEEP-GIT (owner decides): <entry> — <bytes>, <age>, <gitWork> ...
<or the step's exact failure>

<each project step's section, or `### Project steps` with `None.`>

### Strays
<entry> — <files> files, last touched <age> days ago (owner decides) ...
<or `None`, or the step's exact failure>

### Census
Ruled N items (fix N, cut N, keep N). Cuts carried out: <item> — <callers, rows and registrations removed> ...
<or `census: never published`, or `None` when no item changed, or the step's exact failure>
```

Print the artifact path, tracked/untracked totals, archive count, worktree result, scratch totals, each project step's counts and census items ruled. This artifact is the `harness_prune` session-end receipt evidence.

## Don'ts

- Don't hand-edit `.claude/commands/worktree_prune.md` from this command; [`CLAUDE.core.md`](../CLAUDE.core.md) §10 owns baseline-tracked files.
- Don't invent a "last swept" state file — archiving removes files from `.claude/plans/` directly, so re-runs only ever see what's left.
