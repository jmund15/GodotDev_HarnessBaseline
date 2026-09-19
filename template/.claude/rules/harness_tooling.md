---
paths:
  - ".claude/hooks/**/*.py"
  - ".claude/scripts/**/*.ps1"
  - ".claude/scripts/**/*.py"
  - ".claude/tools/**/*.py"
---

# Harness Tooling (fires when authoring a hook or a harness script)

The harness has no compiler and no regression gate. These are the failure modes that ship silently
because nothing else checks them.

## A guard matches the ACTION, never the noun

A hook regex written against a *path* matches every command that merely NAMES the file — `grep`,
`cat`, `wc -l`, `git show`, `sed -i` on its docs. The guard then denies reading the very thing it
guards, and the denial text talks about a rule the user was not breaking.

Measured 2026-08-20: `GATE_RE = re.compile(r"regression_gate\.ps1")` denied `grep -n` and `wc -l` on
the gate script. Tightening it to require `pwsh` was not enough either — `pwsh -Command "...names the
path..."` (a syntax check) still matched. The binding shape is the **invocation**:
`(pwsh|powershell) ... -File <path>`.

*Litmus:* write down three commands that mention the target without executing it. If the regex matches
any of them, it matches the noun.

## Every guard has a re-runnable proof, or it is a hope

`.claude/tests/` is gitignored, so `Glob` and semantic search find nothing there — enumerate existing
proofs with `git ls-files --others --ignored --exclude-standard .claude/tests` (or `ls .claude/tests`)
before assuming one does not exist.

`instruction_quality` §14: registration proves wiring, not matching. A hook asserted to be "tested by
hand" reads in every later audit as enforcement that exists. Order: write the case, run it, watch it
fail, then edit the hook. A proof that has only ever passed is untested; a self-report of "RED observed"
is not the transcript; and a RED rebuilt after the fix from a `git show` copy of the old hook proves
nothing the proof's negative cases do not and costs 8–15 turns (2026-09-15, session 3259384b: 4 of 4
new-guidance arms edited first, then rebuilt a RED). If the order was missed, say so in the result and
stop. Land a case list
beside it under `.claude/tests/` that feeds real PreToolUse payloads on stdin against a planted
environment (a planted process table, a planted file, a planted liveness answer) and asserts on the
emitted channel (`permissionDecision` vs `additionalContext` vs `{}`), and include the **negative**
cases — the read-only mention, the adjacent tool, the retired flag. Spawn a live process only when
the mechanism cannot be planted; a proof that fights the OS to stage its fixture is measuring the OS
(2026-09-15, session 3259384b: 40 turns on one live-process proof, 19 for the planted one, same fix). The ad-hoc
harness that missed the defect above had 22 cases and not one of them read the file. A proof
classifies an exit code outside {0, 2}, or a traceback, as CRASH — never as allow: a hook that cannot
import passes a proof that only asks "was it denied?". A fixture must reach the guarded condition: a
case that passes on a path the guard never inspects is untested (2026-09-15: the escape sweep's own-input
cases passed vacuously because the fixture path never carried the vault marker; three real cells parked).

The commit guard wants a per-file stamp: `python3 .claude/scripts/harness_tests.py --staged` runs the
proofs bound to the staged files (by name, by mention, plus the dir-scanning proofs) and refreshes
only their entries; the full battery certifies everything at session close. Run it once at commit
time and never for a task that does not commit (CLAUDE.md §Build & Test Commands).
`git_guardrails.py` denies: a commit touching
`.claude/{hooks,tools,scripts,workflows,tests}` or `.claude/settings.json` without a fresh stamp; a
staged `hooks/`/`tools/` `.py` without a git-TRACKED `tests/test_<name>*.py` proof (`.claude/tests/`
is gitignored — `git add -f` it); a `merge`/`cherry-pick`/`revert` bringing harness content in
without `--no-commit`. `HARNESS_ALLOW_UNSTAMPED_HARNESS=1 git commit …` (inline prefix) bypasses the
stamp — for a commit that repairs the runner itself, never for a hurry.

**Every commit guard parses the command through `hooks/_git_commit.py`** (its docstring lists the
bypass shapes it closes). A `"git commit" in cmd` check re-opens every one of them. Order: `git add` new files
(`.claude/tests/` is gitignored — `-f`), run the stamp, then commit in a SEPARATE Bash call — the hash
covers the tracked set, and the guard reads the command text before a chained stamp can run.

## Unknown values fail CLOSED, on the safe side of the comparison

A lookup with a permissive default (`WIDTH.get(tier, 2)` where 2 means "widest") turns an unrecognized
input into a silent pass of every check. Default to the value that keeps the guard binding, and prefer
a map lookup that raises or denies over one that shrugs.

## Shared per-session state is atomic or it bricks a session

Hooks fire concurrently on one tool call and share
`~/.claude/.routing_state/<sid8>.json`. `open(path,"w")` truncates first and fills after, so two
writers interleave into one complete document plus a fragment. Use `hooks/_hook_state.py`:
`update_json_locked(path, updater)` for every read-modify-write (per-file lock, salvage read, atomic
replace), and `write_json_atomic` only for a whole-file write that reads nothing back. A read, change
and plain write, even an atomic one, drops fields a sibling hook saved in between. Salvage stops
existing damage from being permanent, which is what turned one torn write into a session that could
not edit harness files at all. `tests/test_hook_state.py` scans every hook for an unlocked writer.

Append-only state also needs rotation, a stale-sweep, or a size cap (`instruction_quality` §15).

## PowerShell: a function's success stream IS its return value

`Write-Output` inside a value-returning function is folded into that value, so `$rc = Invoke-Thing ...`
becomes an array of banner strings plus the exit code and every `-ne 0` comparison is true. Use
`Write-Host` for anything a function prints for humans. Full case: `powershell_emit_pollutes_return_value.md`.

Two more that bite in the same files: `(if ...) + 1` parses clean and throws at runtime
(`powershell_statement_paren_gotcha.md`), and `-File` binding does **not** comma-split array
parameters — `-Scope A,B` arrives as one element, so split it yourself.

## A guard that blocks justified work is a guard defect

When a guard denies work the owner clearly wants done (regenerable, created by this work,
untracked, not a peer's checkout or evidence), make its match precise in the owner file with
RED-first allow and block arms, then run the work through it. Never route around the denial with
another tool, and never hand the chore to the owner. Evidence:
`feedback-guard-denial-of-justified-work-fix-the-guard`.

## Deleting a flag is not done until its stale invocations name their replacement

Docs, plans and memory files carry the old command text. A removed parameter yields a PowerShell
binding error that names the parameter, not the migration. Have the guard recognize retired flags and
print the replacement; keep archived plan files as they are — they are history, not instructions.

<!-- retire-when: review-by: 2027-03-14 -->
