---
paths:
  - ".claude/hooks/**/*.py"
  - ".claude/scripts/**/*.ps1"
  - ".claude/scripts/**/*.py"
  - ".claude/scripts/**/*.sh"
  - ".claude/tools/**/*.py"
  - ".claude/workflows/**"
  - ".claude/tests/**"
  - ".claude/settings.json"
  - ".claude/settings.base.json"
  - ".claude/settings.project.json"
  - ".mcp.json"
  - ".claude/cloud-setup.sh"
  - ".claude/reference/project_stack.md"
---

# Harness Tooling (fires when authoring a hook or a harness script)

Follow the Writing bullet of `CLAUDE.core.md` §9. Code tips in `instruction_quality`: §3 the
facts-versus-judgment sort for any output an agent acts on, §13 the channel a hook's output reaches, §14 a
RED-first proof against a planted violation, §16 fail posture and docstring parity.

A shell a program spawns differs from an interactive one (WSL Bash versus Git Bash, no profile `PATH`): `environment_bootstrap` §This-machine ground truth owns the details.

`settings.json` is composed: register a hook or change a setting in `settings.base.json` (shared),
`settings.<layer>.json` (shared, one layer's stack) or `settings.project.json` (this project), then run `python3 .claude/tools/baseline_sync.py compose`.

## A guard matches the ACTION, never the noun

A hook regex written against a *path* matches every command that merely NAMES the file — `grep`,
`cat`, `wc -l`, `git show`, `sed -i` on its docs. The guard then denies reading the very thing it
guards, and the denial text talks about a rule the user was not breaking.

The binding shape is the **invocation**:
`(pwsh|powershell) ... -File <path>`.

*Litmus:* write down three commands that mention the target without executing it. If the regex matches
any of them, it matches the noun.

## Every guard has a re-runnable proof, or it is a hope

Enumerate existing proofs explicitly with `git ls-files .claude/tests` plus
`git ls-files --others --ignored --exclude-standard .claude/tests` (or `ls .claude/tests`)
before assuming one does not exist; Glob results are not an exhaustive census, and semantic-search discovery is unverified.

`instruction_quality` §14: registration proves wiring, not matching. RED-first is owed when a hook or tool's matched set or verdict changes. A message or comment edit needs none. For an admitted proof, a hook asserted to be "tested by
hand" reads in every later audit as enforcement that exists. Order: write the case, run it, watch it
fail, then edit the hook. A proof that has only ever passed is untested; a self-report of "RED observed"
is not the transcript; and a RED rebuilt after the fix from a `git show` copy of the old hook adds no coverage beyond the proof's negative cases. If the order was missed, say so in the result and
stop. Land a case list
beside it under `.claude/tests/` that feeds real PreToolUse payloads on stdin against a planted
environment (a planted process table, a planted file, a planted liveness answer) and asserts on the
emitted channel (`permissionDecision` vs `additionalContext` vs `{}`), and include the **negative**
cases — the read-only mention, the adjacent tool, the retired flag. Spawn a live process only when
the mechanism cannot be planted; a proof that fights the OS to stage its fixture is measuring the OS. A proof
classifies an exit code outside {0, 2}, or a traceback, as CRASH — never as allow: a hook that cannot
import passes a proof that only asks "was it denied?". A fixture must reach the guarded condition: a
case that passes on a path the guard never inspects is untested. A plant proves only the mutation it plants, and an expected value derived from production's own expression proves nothing (an identity). A stub of a project function returns the caller's assumption about it, so a contract mismatch passes green: drive every stubbed project seam with the real function in some proof. A hand-written copy of what a tool, engine or run writes (a journal, transcript, result or record) holds the author's belief about its format: cut the fixture from a captured live output, trimmed, before the proof counts.

**Planted cases prove the logic, never the feature.** Before a change that alters a harness tool, guard, gate or cache's verdict or output counts as done, run it on the real input it will meet, and put the observation in the commit message: a gate's verdict on the current HEAD, a guard's verdict on the repo's recent real commits with every deny read for a false block, a cache's reuse on a second real run, a parser over a real file it reads. Use the existing state as it is, never a built replica of it. This is an observation at landing, not a proof: a proof that reads live repo state asserts its invariant, never a value the harness's growth will change (a count, a row, a file's text). Litmus: name the real input no case of yours has met (the repo at its real size, the platform's bytes, the producer's actual output, the gate's checkout); when one exists, run it.

The proof of a guard that can deny carries must-allow cases for the justified operations it sits in front
of: a merge in a fresh worktree, `merge --continue`, and a commit with gitignored inputs absent. A floor
guard carries them from its first commit; a process guard carries them before it turns blocking. Build
them with the shared must-allow helper (`tests/_must_allow_cases.py`),
not a per-guard copy. A tooling contract lists its must-allow fixtures before code, including a consumer
that still holds a file upstream deleted.

`harness_tests.py` runs proofs in parallel, so a proof writes only under its own temp root (`TMP`),
never a fixed shared path. A proof that builds a child's own `PYTHONPATH` puts `HARNESS_READ_RECORDER`, when set, first on it: a child that drops the read recorder costs the proof its observed pass-cache key, and every edit to a file in its static reach reruns it. A proof that must own the machine (a wall-clock deadline, a live
singleton) goes in `adaptation.json` `proof_serial`, and independent cases inside one slow proof
use `tests/_case_pool.py`.

**A commit's proof claim names a TRACKED proof, or it claims nothing.** "Four defects, each proven red
first" over an unmodified test file describes a scratch suite: nothing tracked would catch a revert, and
every later audit reads the claim as coverage. Name the file and confirm it is tracked and changed in the
same commit. Promote the scratch case, or drop the claim.

Harness commits run quick checks plus an advisory run of the proofs bound to every authored `.claude/` path, docs included, within the runner's recorded per-proof and total commit budgets. The advisory never denies: a proof it did not pass (red, incomplete, could not run, or left to the close) leaves the commit unproven, and the advisory prints the command that runs it now.
A session that will not push, or pushes to a land branch, proves only its own commits: `--for <the paths its
commits changed> --base <the revision before its first commit>`, then reports each proof green or red. That
result never authorizes a push. A land push (`/commit_push` step 6) needs no local close:
the land runner runs it before the default branch moves. Before any other push, after the final local commit, run
`python3 .claude/scripts/harness_tests.py --close` in the background. It selects the proofs bound to
all harness paths changed since the push upstream (`--base <rev>` overrides it), including docs,
deletions and paths later reverted, except what the push remote's default branch already carries. A
branch's close proves what the branch changes, so a sync merge adds only its conflict resolutions; a red the
merged-in commits bring is the default branch's and reads as pre-existing. The runner tests committed HEAD in a private checkout, not a
peer's dirty working tree; selected proofs absent at that revision and never committed run their working copies against those inputs. Push requires a green `close` result for the pushed commit and that upstream: push the commit it proved (`git push origin <sha>:<branch>`, printed on green); commits landed since go out with the next close.
A missing or stale result, a new red or unavailable coverage blocks push, not local commits.

A failure whose marked lines also appear at the upstream base, or at the default branch's merge-base when a sync merged in commits that base lacks, is pre-existing: printed and logged
to `harness_known_red.jsonl`, not new. An untracked, never-committed proof runs its working copy
against the base's inputs. A new red is bisected through that proof's input-changing commits and
prints `FIRST RED <proof> at <sha> <subject>`. A red that passes in the shared checkout is rerun at committed HEAD with its dirty, untracked or ignored inputs planted. When planting makes it pass, the close prints `NEEDS UNCOMMITTED <proof>` naming those inputs, skips the bisect, counts it apart in the summary and still blocks push: commit an input that belongs to the change, and make the proof plant any input it reads from scratch or another session's work. When planting does not make it pass, the close prints `PASSES ONLY IN THE WORKING TREE <proof>` with an unknown cause (the checkout's location, or an input outside its reach) and bisects it like any other red. `/session_end` §7b
leaves another session's in-progress red to its owner. A timeout or output without a
failure-marked line never excuses a red. A close started while this session's author or fold dispatch writes in its checkout waits for it and names it; it then proves the committed HEAD of that moment once: a later commit never restarts it, and a newer `--close` supersedes it at once, waiting or running, so a commit never needs the close stopped. Before dispatching a fix for reds, run `--close --now`, which skips that wait, so each red arrives classified (pre-existing, or its FIRST RED commit and owner); map the new ones to fix jobs by cause and write set (`orchestration` §2).

`--for <paths>` and `--proofs-for <paths>` run those paths' bound proofs on demand; they do not
satisfy push. `--for` excuses a red that also fails at HEAD while a given path or its proof differs from HEAD;
after the change is committed (a worktree branch, a delegate's slice), pass `--base <the revision
before the change>`, or no red is excused. Use the edited unit's proof file directly for RED-first work, not the bound suite.
Passes are reused while reached inputs are unchanged, whichever checkout of the repository recorded them. `--all` runs every proof on demand;
`/sync_baseline` publish keeps its template battery.

`git_guardrails.py` keeps quick checks (skill load, density, git/index scope and tracked-proof
presence) and makes the advisory proof run above. An authored `hooks/` or `tools/` `.py` needs a git-TRACKED `tests/test_<name>*.py` or
`tests/<name>_test.*`; underscore helpers, deletions and edits `_behavior_unchanged` proves comment/docstring-only are exempt
(`git add -f`, since `.claude/tests/` is gitignored). Staged-blob peer-revert checks stay;
proof-byte freshness moves to committed HEAD at close. The inline prefix
`HARNESS_ALLOW_UNSTAMPED_HARNESS=1` is only for repairing the guard or runner, never a hurry.

**Every commit guard parses the command through `hooks/_git_commit.py`** (its docstring lists the
bypass shapes it closes). A `"git commit" in cmd` check re-opens every one of them. Whether an invocation
commits (`merge`/`cherry-pick`/`revert --continue` included) and which paths a merge authors are
`hooks/_commit_scope.py`'s `publishes_index` and `authored_paths`; a guard never tests `sub == "commit"` itself.

## A spawn on Windows keeps a console its children share

A child of a console-less process allocates its own console, which Windows hands to the default terminal: a visible window per child, and a persistent error window when the child dies before the handoff finishes. A worker that outlives its launcher takes `detach_options()` (`hooks/_bounded_run.py`; `bench_lib.py` carries its twin, held equal by `test_spawn_console_shape.py`). Never `DETACHED_PROCESS`, `CREATE_NEW_CONSOLE`, Node `detached: true` or a `Start-Process` with neither `-WindowStyle Hidden` nor `-NoNewWindow`; the same proof scans for each. A new spawn shape proves itself the way the shape was found: spawn a console child, then count new terminal windows.

## Unknown values fail CLOSED, on the safe side of the comparison

A lookup with a permissive default (`WIDTH.get(tier, 2)` where 2 means "widest") turns an unrecognized
input into a silent pass of every check. Default to the value that keeps the guard binding, and prefer
a map lookup that raises or denies over one that shrugs.

A fail-closed predicate reads its own breakage as the alarming answer, so a positive control proves its
healthy branch. A script that exports `MSYS_NO_PATHCONV=1` resolves helper paths from `__file__` or
`pwd -W`, never from a `BASH_SOURCE`-derived dirname: Windows Python cannot open the `/c/...` spelling.

## A reducer types every row

A reducer in `tools/*.py` follows `instruction_quality` §3a, with absent, unparseable and unattempted rows kept as distinct outcomes.

## Shared per-session state is atomic or it bricks a session

Hooks fire concurrently on one tool call and share
`~/.claude/.routing_state/<sid8>.json`. `open(path,"w")` truncates first and fills after, so two
writers interleave into one complete document plus a fragment. Use `hooks/_hook_state.py`:
`update_json_locked(path, updater)` for every read-modify-write (per-file lock, salvage read, atomic
replace), and `write_json_atomic` only for a whole-file write that reads nothing back. A read, change
and plain write, even an atomic one, drops fields a sibling hook saved in between. Salvage stops existing damage from being permanent. `tests/test_hook_state.py` scans every hook for an unlocked writer.

A content id a later step resolves (a review ledger blob, a fold snapshot) is stored when computed: `git hash-object -w`. An unwritten id strands its record when the file moves on; the project's frozen-blob proof lists any compare-only exception.

Append-only state also needs rotation, a stale-sweep, or a size cap (`instruction_quality` §15). The exemption is an append-only log whose rows a reader must see in full: `escape_log.jsonl` stays unrotated because a dropped row would silently restore the coverage it revoked. Any other append-only state still rotates.

## PowerShell: a function's success stream IS its return value

`Write-Output` inside a value-returning function is folded into that value, so `$rc = Invoke-Thing ...`
becomes an array of banner strings plus the exit code and every `-ne 0` comparison is true. Use
`Write-Host` for anything a function prints for humans. Full case: `powershell_emit_pollutes_return_value.md`.

Two more that bite in the same files: `(if ...) + 1` parses clean and throws at runtime
(`powershell_statement_paren_gotcha.md`), and `-File` binding does **not** comma-split array
parameters — `-Scope A,B` arrives as one element, so split it yourself.

## A guard that blocks justified work is a guard defect

A denial fails its guard's purpose when it blocks work the owner clearly wants done (regenerable,
created by this work, untracked, not a peer's checkout or evidence) or demands an artifact nothing
needs (a proof for an edit that changes no behavior). Never comply with the busywork, route around
the denial with another tool, or hand the chore to the owner. When the task cannot finish without
the blocked step, make the guard's match precise in its owner file now, with RED-first allow and
block arms, and run the work through it. The precise match is a fact the guard observes, never a
reason, class or quote field the agent fills in, an owner quote included (`instruction_quality` §3); a
deny that protects another session's process or work serves its purpose. Otherwise report the step as blocked and continue the task;
`/session_end` friction dispositioning fixes the guard. A denial that serves its purpose stands.
Evidence: `feedback-guard-denial-of-justified-work-fix-the-guard`.

**A new guard starts advisory unless it guards the floor.** A process guard lands advisory
(`instruction_quality` §13) and turns blocking only after its proof and a live session that exercised its
matcher on a justified operation show no false denial; the commit that turns it blocking cites that
session. A guard protecting the floor (peer work, owner authority, the gate) blocks from its first
commit. The class follows what the guard protects, not its hook event, and sets only how a new guard
starts: once blocking, it denies only an action that is wrong in every case its predicate matches (`instruction_quality` §3). A new
deny-capable hook's docstring names its class, process or floor. Its proof follows §Every guard has a
re-runnable proof, or it is a hope. An explicit owner request for a blocking guard overrides the advisory
start, and the commit quotes that request.

A guard backs up an instruction (`instruction_quality` §3). Before adding or keeping a deny, name that
instruction and where the agent reads it before this call: CLAUDE.md, the command or skill step that
governs the action, a rail, or an injected line; for live state (quota, a peer's lock, a running gate),
the injected line or a named pre-check step. When none reaches the agent at decision time, land it
there first. The deny cites that instruction and names a next step that serves the guard's purpose
(`instruction_quality` §3), and its proof asserts both.

An auto-mode classifier denial of a harness action the owner authorizes by class gets the same
fix: a contract in `hooks/approving_gates.py` with its proof, never a static `permissions.allow`
entry. A contract approves one command in canonical shape (a tracked project tool, a read-only git search piped through text filters, `git commit`, or a Jmodot PR merge),
with allowlisted flags and in-project paths; anything else falls through to the classifier. Hooks and narrow `permissions.allow` rules decide before the classifier; a `.claude/` write reaches it, and only a user-settings `autoMode.allow` entry changes its verdict. An action that grants
persistence, spend, detachment or writes becomes a contract only after the owner names that class.

## A launcher script is not edited in place while detached runs execute it

Bash reads a running script by byte offset, so a size-changing edit makes every in-flight run resume mid-token and die, losing the output its shell held. Before editing a `.sh` launcher with live detached instances, edit a copy and point new dispatches at it, or stop the instances this session owns through `python3 .claude/tools/reap.py` and edit once they are gone. Evidence: `gotcha_editing_shell_script_corrupts_running_instances`.

## Deleting a flag is not done until its stale invocations name their replacement

Docs, plans and memory files carry the old command text. A removed parameter yields a PowerShell
binding error that names the parameter, not the migration. Have the guard recognize retired flags and
print the replacement; keep archived plan files as they are — they are history, not instructions.

The mirror case: a guard that newly requires a field on a call is not done until the field defaults from
the data that owns it (a lens or job registry) or every command example that shows the call carries it. A
guard whose first contact with each caller is a denial taxes every run without improving any choice.

<!-- retire-when: review-by: 2027-03-14 -->
