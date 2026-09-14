---
name: framework-boundary-rule
description: Reusable framework roots never import a consuming project's namespace.
metadata:
  type: feedback
---

Code under a reusable framework root must not reference the consuming project's namespace.

**How to apply:**
1. Put shared contracts and defaults behind a framework-owned interface, configuration object, or
   explicit `Configure(...)` seam.
2. Let project startup provide project Resources and defaults.
3. Let framework tests configure and reset the seam without loading project startup code.
4. Keep code project-local until every dependency needed by the framework side crosses that seam.

There is no temporary-import exception. A planned later cleanup does not make a project namespace
reference portable. Move only the independent part now, or keep the whole feature on the project side.
