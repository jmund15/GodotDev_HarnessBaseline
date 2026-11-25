---
disable-model-invocation: true
---

# /codify Step 4: the routing rows

Read at [`codify.md`](../codify.md) Step 4, after the layer is chosen. Moved verbatim from `codify.md`.

| Fires when… | Surface | Owning home | Passive cost | Firing proof |
|---|---|---|---|---|
| a file of a **prefix-anchored class** (below the repo root) is touched | `rules/<name>.md` + `paths:` | `instruction_quality` §5 | zero until matched | the glob string is prefix-anchored below the repo root — decidable by reading it, no tooling |
| an agent **edits** a specific artifact (an editing rule no user of the file needs) | the artifact's maintaining command (`/ladder_ingest`, `/worklog`, `/sync_baseline`, or the project's roadmap command); a judgment rule for the file also takes an `OWNED_NOTES` entry in `hooks/harness_edit_skill_reminder.py` naming the command's `## ` section, which the hook reads into an Edit/Write made outside the command, since the command does not load on a direct edit; with no command, a check that decides it (Step 3), else the `EDIT_NOTES` entry alone; `instruction_quality` only for a rule covering a class of files, never one file | `instruction_quality` §6 | zero until that edit | the rule appears in no file a reader loads to *use* the artifact, and an edit of the artifact receives it |
| an agent deliberately enters a domain | `auto-memory/archive/` (cold) | `instruction_quality` §5 | zero | the NL paraphrase an agent would search returns it top-ranked |
| the user types it | `commands/` | `instruction_quality` §5 | zero until invoked | a loaded surface names it at signal-time |
| a tool call matches a condition | `hooks/` **+ documented home** | `instruction_quality` §3 | one interpreter spawn per matched call | pass → deliberate violation → revert; the home loads before the blocked action |
| a gate runs over an artifact (diff, plan, PR body, repo state) | lens or lint inside that gate's command (Step 3) | `instruction_quality` §12 | zero until the gate runs | a planted violation yields the finding; a clean input yields none; a lens runs at the pin its gate runs at |
| a named task-shape begins | skill | `instruction_quality` §5, §7 | description bytes, every session | a fresh agent given only the description picks it |
| a decision every agent faces, delegates included, and no trigger exists | the project `CLAUDE.md` chain | `instruction_quality` §5 | every byte, every agent | Step 5 |
| a decision only a session the owner works with faces, and no trigger exists | `CLAUDE.interactive.md` | `instruction_quality` §5 | every byte, every interactive session | Step 5 |
| a decision every delegate faces and no session does | `guards/common.md`, or its kind file | `instruction_quality` §5 | every byte, every delegate | Step 5 |

A `paths:` rule loads on every read of a matching file, and a harness `.md` is read to be executed, so an editing rule never lands there. `rules/` + `paths:` leads **on a prefix-anchored glob** — the only surface both guaranteed-to-load and genuinely free until matched. On a broad glob (`**/*.cs`, `**/*.tscn`, `**/*.md`) the cost is deferred, not avoided, and the row re-ranks below cold memory; width is about session shape, not file count (`Tests/**/*.cs` stays narrow because a gameplay session never opens `Tests/`). Hooks sit mid-table despite the strongest enforcement: they cost a spawn per matched call and can never stand alone.
