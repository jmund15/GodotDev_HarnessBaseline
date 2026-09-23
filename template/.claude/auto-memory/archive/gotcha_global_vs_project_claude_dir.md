---
name: gotcha-global-vs-project-claude-dir
description: "This project's tooling (.claude/scripts, .claude/tools, .claude/commands) lives in the repo, not ~/.claude/ — only runtime/session state lives globally"
metadata: 
  node_type: memory
  type: project
  originSessionId: dfe017b3-b84e-41bf-8517-359f6b15b971
  modified: 2026-09-18T01:01:12.319Z
---

Two directories share the name `.claude` and hold different things. `<repo>/.claude/` holds this
project's authored tooling: `scripts/`, `tools/`, `commands/`, `skills/`, `hooks/`. `~/.claude/`
(global, machine-wide) holds runtime/session state only: session transcripts, `sidecar_ledger.jsonl`,
`stats-cache.json`, `settings.json`. A path like `.claude/scripts/foo.sh` that a Grep result shows
relative to the project root is a project file — reading it under `~/.claude/scripts/foo.sh` fails
with "File does not exist" even though the search that found it succeeded.

**Why:** confused the two 4 times in one session (four failed Read/`ls` calls) chasing scripts that
grep had already found relative to the project root, before checking which root the grep search
itself used.

**How to apply:** before reading a `.claude/...` path from a search result, check which root
(project vs. `~/.claude`) the search actually scanned — a `Grep(path: <project-dir>/.claude)` call
returns project-relative hits, not global ones.
