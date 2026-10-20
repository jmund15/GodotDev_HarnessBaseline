---
name: framework-defaults-seam
description: Framework defaults enter through a framework-owned configuration seam populated by the consumer.
metadata:
  type: reference
---

Reusable framework code must not import a consuming project's namespace to fetch defaults.

When a framework service needs project-wide defaults:
- define a framework-owned configuration object or `Configure(...)` seam;
- let project startup populate it after the project's registry is ready;
- let tests reset it through a framework-owned reset path;
- keep per-instance overrides ahead of configured defaults.

Required values fail during setup. Optional values may use a documented disabled behavior when both the
instance override and configured default are absent. Do not hide a required dependency behind a
nullable global fallback.
