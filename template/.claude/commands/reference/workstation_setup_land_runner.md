---
disable-model-invocation: true
---

# `/workstation_setup` — Land runner steps

Read at `/workstation_setup` Phase 7 on a machine the user lands from. Entrypoint: [`../workstation_setup.md`](../workstation_setup.md).

`<label>` is the self-hosted runner label in the `runs-on` list of the workflow that runs on pushes to
`land_prefix` branches (`git ls-files .github/workflows` lists the workflows). `<land worktree>` is the
worktree path that workflow checks out.

1. **Registered and online.** `gh api repos/:owner/:repo/actions/runners --jq '.runners[]|{name,status,labels:[.labels[].name]}'`
   lists a runner with status `online` and labels `self-hosted`, `Windows`, `X64`, `<label>`.
   Absent → BLOCKED-ON-USER. A registration token is a secret, so give the user these commands
   and run none of them: `gh api -X POST repos/:owner/:repo/actions/runners/registration-token --jq .token`,
   then, in the `<label>` runner directory (the runner version `reference/project_stack.md` pins),
   `.\config.cmd --url https://github.com/<owner>/<repo> --token <token> --name <label> --labels <label> --unattended`.
2. **Starts at logon, never times out.** `Get-ScheduledTask '<label> runner'` exists and is Running. Its
   action is `pwsh.exe -NoProfile -WindowStyle Hidden -Command .\run.cmd` in the runner directory, its
   trigger is `MSFT_TaskLogonTrigger`, and `Settings.ExecutionTimeLimit` is `PT0S` (Task Scheduler's
   default limit would end a long land). Absent or wrong → replace it with `-Force`:
   `$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`,
   then `Register-ScheduledTask -Force -TaskName '<label> runner' -Action $a -Trigger $t -Settings $s -User '<DOMAIN\user>' -RunLevel Limited`.
   The runner directory's `.env` holds `GITHUB_ACTIONS_RUNNER_CHANNEL_TIMEOUT=300`, the listener's maximum: the
   listener kills a job whose worker has not taken it within that many seconds (default 30), and under load a
   worker starts slower than that. The listener reads `.env` only at start, so restart the task after writing
   it, while no land job runs: stop the task's `pwsh.exe` with
   `python3 .claude/tools/reap.py --why "restart the <label> runner" <its pid>` (its tree dies with it), then
   `Start-ScheduledTask '<label> runner'`. `Stop-ScheduledTask` alone ends only that `pwsh.exe` and leaves
   `Runner.Listener.exe` running without its restart loop.
3. **The land worktree.** `git worktree list` shows `<land worktree>` locked, and, when the repo has
   submodules, `git -C <land worktree> submodule status` shows no `-` prefix. Absent:
   `git worktree add --detach <land worktree> origin/main`,
   `git worktree lock <land worktree> --reason "land runner"`, then
   `submodule update --init --recursive` inside it. The workflow hard-codes this path under
   the owner's home folder; another user or repo root edits that file.
4. **The main check worktree** (when `adaptation.json` `main_check` is non-empty). `git worktree list` shows
   `.claude/worktrees/main-check` locked. Absent: create it as step 3 does, with `--reason "main check"`, then run
   the project's prep for a fresh test checkout, which its `workstation_setup_toolchain.md` names.
5. **The main check state.** `git ls-remote origin main-check-state` prints a sha, and
   `git show origin/main-check-state:main_check.json` holds a `checkedTo` that is an ancestor of `origin/main`
   (`git merge-base --is-ancestor`). Absent (a new repo only; every other machine fetches it): seed a state file in
   `main_check.py`'s format whose `checkedTo` is the sha the newest green land run put on `main`. Read it from
   that run's log (`gh run view <id> --log`): the `LAND_RESULT=green` line's `landed=` sha, else its `sha=`; skip
   results already on main or superseded. Build the commit with `main_check.py`'s `git` helper (`hash-object`,
   `mktree`, `commit-tree`), which sends bytes: a text-mode pipe on Windows puts `` in the file name. Push it to
   `refs/heads/main-check-state`; `main_check.load_state` reading it back is the check.
