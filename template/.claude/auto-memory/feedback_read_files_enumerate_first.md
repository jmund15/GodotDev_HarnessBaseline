---
name: read-files-enumerate-first
description: Enumerate source files before a bulk read so scope, omissions, and caps stay visible.
metadata:
  type: feedback
---

Enumerate concrete file paths before calling a bulk reader. Use `Glob`, LSP, or semantic search to
build the list, then pass the relevant files rather than a directory.

**Why:** Directory expansion can be capped, skip unsupported files, or hide a wrong root. An answer
from a partial input can still sound complete.

**How to apply:**
- Check the path count and expected extensions before dispatch.
- Require one result per input when the task needs full coverage.
- Treat directory-empty, expansion-capped, read-error, and missing-file markers as incomplete input.
- Pass a directory only when the task truly needs every supported file and the expansion limit is known.
