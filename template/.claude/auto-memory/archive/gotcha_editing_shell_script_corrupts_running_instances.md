---
name: gotcha_editing_shell_script_corrupts_running_instances
description: editing a .sh/.ps1 while it has a live process corrupts the running instance
metadata:
  type: feedback
---

`bash` reads a script lazily, byte-offset by byte-offset, as it executes — not loaded
whole into memory up front. Editing a running `.sh` (or a `.ps1` under PowerShell's
equivalent lazy-read behavior) shifts every byte offset after the edit point, so the live
instance resumes mid-token and executes garbage instead of erroring cleanly. Observed
corrupting two in-flight `deepseek_sidecar.sh` dispatches from a mid-run edit to that
same script.

**Why:** the failure is silent-ish (garbled execution, not a clean crash), and the fix is
cheap enough that skipping it is pure risk with no upside.

**How to apply:** before editing any `.claude/scripts/*.sh`/`.ps1`, check for a live
process referencing it (`ps aux | grep <script>` / `Get-Process`) — or just trust
[`hooks/running_script_edit_guard.py`](../../hooks/running_script_edit_guard.py), which
does this automatically on every `Write`/`Edit` to that path and warns with PIDs if it
finds one. If a run is live: kill it first, or copy-then-edit (edit a copy, swap it in
once the run finishes) — never edit the file a live process is reading.
