---
paths:
  - ".claude/hooks/**/*.py"
  - ".claude/scripts/**/*.ps1"
  - ".claude/scripts/**/*.py"
  - ".claude/tools/**/*.py"
  - ".claude/scratch/**/*.ps1"
  - ".claude/scratch/**/*.py"
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

`instruction_quality` §14: registration proves wiring, not matching. A hook asserted to be "tested by
hand" reads in every later audit as enforcement that exists. Land a case list beside it under
`.claude/tests/` that feeds real PreToolUse payloads and asserts on the emitted channel
(`permissionDecision` vs `additionalContext` vs `{}`), and include the **negative** cases — the
read-only mention, the adjacent tool, the retired flag. The ad-hoc harness that missed the defect
above had 22 cases and not one of them read the file. A proof classifies an exit code outside
{0, 2}, or a traceback, as CRASH — never as allow: a hook that cannot import passes a proof that
only asks "was it denied?".

## Unknown values fail CLOSED, on the safe side of the comparison

A lookup with a permissive default (`WIDTH.get(tier, 2)` where 2 means "widest") turns an unrecognized
input into a silent pass of every check. Default to the value that keeps the guard binding, and prefer
a map lookup that raises or denies over one that shrugs.

## Shared per-session state is atomic or it bricks a session

Hooks fire concurrently on one tool call and share
`~/.claude/.routing_state/<sid8>.json`. `open(path,"w")` truncates first and fills after, so two
writers interleave into one complete document plus a fragment. Use `hooks/_hook_state.py`:
`write_json_atomic` (tempfile + `os.replace`) and `read_json_salvage` (recovers the leading document
from an already-torn file). Both are needed — atomicity stops new damage, salvage stops existing
damage from being permanent, which is what turned one torn write into a session that could not edit
harness files at all.

Append-only state also needs rotation, a stale-sweep, or a size cap (`instruction_quality` §15).

## PowerShell: a function's success stream IS its return value

`Write-Output` inside a value-returning function is folded into that value, so `$rc = Invoke-Thing ...`
becomes an array of banner strings plus the exit code and every `-ne 0` comparison is true. Use
`Write-Host` for anything a function prints for humans. Full case: `powershell_emit_pollutes_return_value.md`.

Two more that bite in the same files: `(if ...) + 1` parses clean and throws at runtime
(`powershell_statement_paren_gotcha.md`), and `-File` binding does **not** comma-split array
parameters — `-Scope A,B` arrives as one element, so split it yourself. Variables are
case-insensitive: `$WT` and `$wt` are ONE variable, so a loop that derives `$wt` from `$WT`
rewrites its own base on the second pass and every later path nests under the first.

## A detached job is verified by its own echo, and a wait by its terminal line

A returned launch proves nothing about what the job took. The script logs its resolved item set
(`prs=`, `trees=`) first; read that line against the request BEFORE arming a wait, then test the
filter against the literal terminal marker (`grep -E '<pattern>' <<< 'CHAIN_DONE'`) — a miss makes a
finished job look like a running one. Case: `gotcha_detached_job_wrong_args_silent_wait.md`.
A waiter keyed on a marker in an APPEND-ONLY log fires on the previous run's marker — key it on a
line count or timestamp captured at launch, or truncate the log first.

## Deleting a flag is not done until its stale invocations name their replacement

Docs, plans and memory files carry the old command text. A removed parameter yields a PowerShell
binding error that names the parameter, not the migration. Have the guard recognize retired flags and
print the replacement; keep archived plan files as they are — they are history, not instructions.
