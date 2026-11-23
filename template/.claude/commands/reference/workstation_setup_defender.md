---
disable-model-invocation: true
---

# `/workstation_setup` — Defender exclusions (Windows)

Read at `/workstation_setup` Phase 1 on Windows. Entrypoint: [`../workstation_setup.md`](../workstation_setup.md).

Defender scans a file each time a process opens it, and every proof run polls the machine-slot locks
(`tools/machine_slots.py`) many times a second. Exclude that one folder and nothing wider: the repo, Temp
and every process hold code that runs.

1. `~/.claude/defender_exclusions.json` from an earlier run maps the path to `true` → PROVISIONED, no prompt.
2. Otherwise tell the owner a UAC prompt is coming, then run, with `<home>` and `<repo>` as absolute Windows paths:
   `powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','<repo>\.claude\scripts\defender_exclusions.ps1','-Result','<home>\.claude\defender_exclusions.json','-Paths','<home>\.claude\.harness_slots'"`.
   The path `true` → PROVISIONED. A declined prompt, or another antivirus in Defender's place → DEGRADED,
   naming it. An `error` key → FAILED, quoting it.
