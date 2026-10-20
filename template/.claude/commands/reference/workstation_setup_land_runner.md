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
3. **The land worktree.** `git worktree list` shows `<land worktree>` locked, and, when the repo has
   submodules, `git -C <land worktree> submodule status` shows no `-` prefix. Absent:
   `git worktree add --detach <land worktree> origin/main`,
   `git worktree lock <land worktree> --reason "land runner"`, then
   `submodule update --init --recursive` inside it. The workflow hard-codes this path under
   the owner's home folder; another user or repo root edits that file.
