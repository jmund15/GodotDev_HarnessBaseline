---
paths:
  - ".claude/hooks/**/*.py"
  - ".claude/scripts/**/*.ps1"
  - ".claude/scripts/**/*.py"
  - ".claude/scripts/**/*.sh"
  - ".claude/tools/**/*.py"
  - ".claude/workflows/**"
  - ".claude/tests/**"
  - ".github/workflows/**"
  - ".claude/settings.json"
  - ".mcp.json"
---

# Harness Tooling (loads when you edit a hook, harness script, tool, workflow or test)

`instruction_quality` holds the code-facing rules: §3 sorts facts from judgment in any output an agent acts on, §13 the channel a hook's output reaches, §16 fail posture. `reference/mechanism_litmus.md` decides whether a test, check or hook should exist at all. Outline a Python file with `python3 .claude/tools/python_outline.py <path>`. Python file writes use LF (`newline="\n"` or bytes), since a CRLF script breaks Bash and dirties diffs.

## A change runs its unit's own test; CI runs the suite

Before committing a harness change, run the edited unit's own test file: `python -m pytest .claude/tests/<file> -q --durations=0`. The shared repo's CI runs the whole suite with `pytest -n auto` on every push, and a project's main check runs its local harness tests after the land; neither runs on the session's path. No pass cache and no proof selection exist: a suite runs whole, so a test that is slow is slow on every push. A harness test never takes a machine lease; `tools/machine_slots.py` admits product runs only (a Godot gate, a staging check, a browser-heavy run).

A commit message that claims a proof names a tracked test file changed or run in that commit. A scratch case that caught the defect is promoted into the unit's test file, or the claim is dropped: an untracked case catches no revert.

## How a test is shaped

- **Decide in process.** A decision case calls the unit directly: a hook's `main()` or decision function with a real payload (`tests/_hook_call.py`), a tool's function. Each hook or CLI entry point keeps one spawned case proving it imports, runs from its registered command and exits as its contract says. Spawn more only when the process is the subject (a launcher, a detach shape, a signal, a lock between processes). A suite past its time bound is diagnosed by cause (too many cases, redundant ones, over-computational ones) and fixed there, never trimmed to a number.
- **RED first on a verdict change.** Write the case, watch it fail, then change the unit. A case that has only ever passed is untested. A message or comment edit needs no new case.
- **Negative cases.** A guard's test carries the read-only mention, the adjacent tool and the retired flag, and a floor guard carries must-allow cases for the justified operations it sits in front of (a merge in a fresh worktree, `merge --continue`, a commit with gitignored inputs absent), built with `tests/_must_allow_cases.py`.
- **A crash is not an allow.** An exit code outside the unit's contract, or a traceback, fails the case: a hook that cannot import passes a test that only asks "was it denied?".
- **The fixture reaches the condition.** A case that passes on a path the unit never inspects is untested. A stubbed project seam returns the caller's assumption, so some case drives the real function. A hand-written copy of a tool's output holds its author's belief: cut the fixture from captured live output.
- **Assert the decision, never the wording.** Assert exit code, allow or deny, the output channel, the item picked, what was written and where; from a message, at most the one token a reader acts on (a path, a command, an id). A valid edit that changes no decision and turns a case red is a defect in the case.
- **Plant every ambient input.** Tests run in parallel, so a case writes only under `tmp_path` and plants what it reads (home, git config, a `CLAUDE_*` variable, a tool on `PATH`) with `monkeypatch`. A case that needs a git repository builds it in a fixture and copies it per case, so no ref or stash crosses cases.
- **Observe the real input before landing.** A change to a guard, gate or parser runs once on the real input it will meet (a guard over recent real commands with each deny read for a false block, a parser over a real file), and the commit message records the observation. A test that reads live repo state asserts an invariant, never a count the harness's growth changes.

## Authoring rules with a recorded failure behind them

- **Match the action, never the noun.** A hook regex written against a path matches every command that merely names the file (`grep`, `cat`, `git show`) and denies reading what it guards. Bind to the invocation shape. Litmus: three commands that mention the target without executing it must not match.
- **Unknown values fail closed.** A lookup with a permissive default (`WIDTH.get(tier, 2)` where 2 is widest) turns an unrecognized input into a pass. Default to the value that keeps the check binding, and give a fail-closed predicate a positive control for its healthy branch.
- **Shared state is written atomically.** Hooks fire concurrently on one tool call. A read-modify-write goes through `hooks/_hook_state.py` `update_json_locked`; a whole-file write through `write_json_atomic`; a rename onto a file another process may hold open through `replace_path`, since Windows refuses a bare rename over an open file. Append-only state rotates or caps (`instruction_quality` §15).
- **A spawned worker keeps its console hidden.** On Windows a child of a console-less process opens a visible terminal window. A worker that outlives its launcher spawns with `detach_options(env)` from `hooks/_bounded_run.py`; never `DETACHED_PROCESS`, `CREATE_NEW_CONSOLE`, Node `detached: true`, or `Start-Process` without `-WindowStyle Hidden` or `-NoNewWindow`.
- **PowerShell returns its success stream.** `Write-Output` inside a value-returning function joins the return value, so a `$rc` comparison breaks; print with `Write-Host`. `-File` binding does not comma-split array parameters, so split `-Scope A,B` yourself.
- **Bash reads a running script by offset.** A size-changing edit to a `.sh` launcher kills every run executing it. Edit a copy and point new launches at it, or stop the runs you own through `tools/reap.py` first.
- **A deleted flag names its replacement.** A guard or tool that drops a flag recognizes the retired spelling and prints the new one, since docs and memory keep the old text. A guard that newly requires a field defaults it from the data that owns it, so its first contact with a caller is not a denial.
- **A launcher stops paid work on observed waste, never an estimate.** A finished result about to be re-paid, an orphaned or duplicate job, or a provider's limit rejection stops a run; a cap from a spend estimate stops the largest legitimate run first. Whatever stops a run leaves it resumable.

## A guard that blocks justified work is a guard defect

A denial of work the owner clearly wants (regenerable, created by this work, untracked, not a peer's checkout) fails the guard's purpose. Never comply with busywork, route around the denial, or hand the chore to the owner: make the guard's match precise in its own file, with a RED-first allow case and a block case, and run the work through it. The precise match is a fact the guard observes, never a reason the agent writes into the call. A new deny is a floor module answering a loss case (`reference/mechanism_litmus.md`), and the instruction it backs reaches the agent before the call it denies.

## Land and main check

A project with a main check pushes to its land branch; the land rebases onto `main` and fast-forwards it, running no proof. The main check then proves `main` over the landed range: the project's gate and its local harness tests. A red there is a row for the next session, never a failed land. `change_control` owns who fixes it.
