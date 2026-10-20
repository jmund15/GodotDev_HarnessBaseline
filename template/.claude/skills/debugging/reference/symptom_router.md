# Phase 0 — Symptom triage router

Read at Phase 0 of [`../SKILL.md`](../SKILL.md). Check the symptom here before building a loop. The Route column owns the depth; don't re-derive it.

When the godot layer is installed, the companion of this file (same name plus `_godot`) holds the engine rows. Read it too; without it, only the rows here apply.

| Symptom | Likely cause | Discriminating check | Route |
|---|---|---|---|
| Bash test command hangs forever | Output piped through `\| tail`/`\| head` (buffers the whole stream), or the default 120s timeout orphaned a child process holding the pipe | Did the call set an explicit timeout and avoid pipes? | `CLAUDE.core.md` §Shell Discipline |
