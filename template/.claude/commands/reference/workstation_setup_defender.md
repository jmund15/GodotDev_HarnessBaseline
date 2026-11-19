---
disable-model-invocation: true
---

# `/workstation_setup` — Defender exclusions (Windows)

Read at `/workstation_setup` Phase 1 on Windows. Entrypoint: [`../workstation_setup.md`](../workstation_setup.md).

Defender scans a file each time a process opens it, and the harness opens two data folders many times a
second: the machine-slot locks every proof run polls (`tools/machine_slots.py`) and the per-event captures
`scripts/codex_proxy_sidecar.sh` writes. Exclude those two folders and nothing wider: the repo, Temp and
every process hold code that runs.

1. `~/.claude/defender_exclusions.json` from an earlier run maps both paths to `true` → PROVISIONED, no prompt.
2. Otherwise tell the owner a UAC prompt is coming, then run, with `<home>` and `<repo>` as absolute Windows paths:
   `powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','<repo>\.claude\scripts\defender_exclusions.ps1','-Result','<home>\.claude\defender_exclusions.json','-Paths','<home>\.claude\.harness_slots;<home>\.local\state\claude-code-proxy\traffic'"`.
   Both paths `true` → PROVISIONED. A declined prompt, or another antivirus in Defender's place → DEGRADED,
   naming it. An `error` key → FAILED, quoting it.
