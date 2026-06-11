# Known Failure Modes — Case-History Catalog

<!-- SEED TEMPLATE — this checklist is project-owned. It accrues YOUR project's
     regression history; entries migrate here from auto-memory when a failure mode
     has bitten twice or cost a session. Consumed by /plan_check and /session_audit
     (conditional lens). An empty catalog is fine at project start. -->

Purpose: a "have we hit this before?" lookup. Each entry is a **class of failure**
(not a one-off bug), with the project incident as evidence.

Entry format:

```
## <failure-class name>
- **Class:** <general rule — what shape of change walks into this>
- **Evidence:** <project incident(s): what happened, cost>
- **Detection:** <how to notice you're about to repeat it>
- **Prevention:** <the gate/pattern that avoids it>
- **Memory ref:** <auto-memory file, if one exists>
```

<!-- Entries below this line. -->
