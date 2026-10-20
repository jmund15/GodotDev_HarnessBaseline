---
disable-model-invocation: true
---

# `/session_audit` — Phase 1.5 scan details

Read at `/session_audit` §1.5a for the stub-marker scan and at §1.5d for the shape of a MERGE-BLOCKER report. Entrypoint: [`../session_audit.md`](../session_audit.md).

## Stub-marker scan

Scan only added lines of session-changed `.cs` files, in code and comments. Use `git diff --unified=0 <base> <head> -- <subject .cs files>` for a range, or `git diff --unified=0 <session-base> -- <subject .cs files>` for working files; exclude diff headers. For a new untracked subject file, every line is added. Match these markers:

```
deferred|TODO|FIXME|follow-up|follow up|stub|not yet wired|no-op until|placeholder
```

Blocking examples:
- `// wiring deferred to the wheel-UI integration follow-up`
- `// TODO: hook this up`
- `// stubbed for now`
- `JmoLogger.Info(this, "...stubbed (no provider wired)...")`

## Report shape

If 1.5a/b find issues:
```
╔══════════════════════════════════════════════════════╗
║   MERGE-BLOCKER: DELETED-SURFACE PARITY ISSUES       ║
╠══════════════════════════════════════════════════════╣
║ Stub markers: <count>   Parity drops: <count>        ║
╚══════════════════════════════════════════════════════╝
```
Then list each finding with file:line and "How old code did it" vs "How new code handles it".
