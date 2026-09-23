---
name: powershell_emit_pollutes_return_value
description: "A PowerShell function that both logs via Write-Output and returns a value returns BOTH — the caller gets an array of log strings plus the value, and property lookups on it silently yield defaults instead of throwing."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 9549ad2e-b562-41d6-9cdd-31c53f695de0
  modified: 2026-08-19T16:30:29.734Z
---

# Logging inside a value-returning PowerShell function corrupts its return value

PowerShell folds **every uncaptured output-stream write** into a function's return value. A helper
that logs progress with `Write-Output` (or any wrapper over it — this harness's `Emit`) and also
`return`s a record hands the caller `@(<log strings...>, <record>)`, not the record.

The failure is silent and reaches production data. Property reads on the array do not throw; they
return the caller's defaults:

```powershell
$res = Wait-QueueResult -Id $id -Seconds 420     # emits a heartbeat every 60s
$status = [string](Get-Prop $res 'status' 'UNKNOWN')   # -> 'UNKNOWN', not the real verdict
```

Measured 2026-08-19 in the project's regression gate script: a queue waiter emitting 60s heartbeats made the gate
adopt and report a fabricated `VERDICT=UNKNOWN exit=7`. Two properties of the bug make it nasty —
the heartbeats never appeared in stdout (they were captured, not printed), and `$res` was *truthy*,
so an `if ($res)` guard passed.

**Rule: pick one channel per function.** A function that logs must return its value out of band —
a `$script:` variable, a `[ref]` parameter, or an object the caller reads afterwards. Do not rely
on "I only log on the error path": the day someone adds a progress line, every caller silently
starts reading defaults.

Diagnostic smell: a caller receiving *only* default values from `Get-Prop`/property reads, while
the log lines you expected are missing from stdout. That pairing is this bug, nowhere else.

Sibling: [[powershell_statement_paren_gotcha]] — same class (parses clean, misbehaves at runtime).
