---
description: Check that a removed or rewritten surface keeps its behaviour
disable-model-invocation: true
---

# Parity Checklist

`/review` diff mode and the PR commands' review step read this file. A change that only adds code skips it.

## Mandate

Compare OLD and NEW for each changed function and each deleted file, reading both. Flag as critical every dropped or weakened branch, removed validation and silently changed default, and every stub on an added line of shipped code: a `TODO`, `FIXME`, `stub`, `placeholder` or "deferred" marker that leaves wiring unfinished. Quote old and new verbatim with `file:line`. A critical parity finding is fixed before the change lands, or the owner accepts the dropped behaviour in words.

## Retired-code procedure

For each changed file in a directory where the reviewed range deleted a file (`git diff <range> --diff-filter=D --name-only`):

1. Read the deleted file with `git show <base>:<path>`.
2. List its public surface: exported fields and settings, public functions, lifecycle hooks, event subscriptions, and side effects (state it writes, events it emits).
3. For each item, point at the new line that reproduces it, or at the comment, docstring or PR description that records its removal.

An item with neither is a critical finding. Examples:
- The old menu state's enter hook started a slow-motion controller; the new menu state's enter hook does not.
- The old state resolved its references on enter; the new one resolves them at init, a silent change of timing.
