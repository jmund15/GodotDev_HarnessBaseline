---
description: Digest another session's transcript (prompts, friction, files touched, last message; --tools for the per-tool call census incl. subagents) to pick up its work after /clear, a handoff, or to compare how two sessions did one task.
---

# Session Digest

Rebuilds a session from its live transcript (`~/.claude/projects/<project>/<id>.jsonl`, append-only across
compactions): every real user prompt verbatim, every tool error / denial with the assistant's next move, the
files it modified, its last message. Tool: `.claude/tools/session_digest.py`.

## Arguments

- **The prompt quotes a session's message** → write the quote to `.claude/scratch/digest_paste.txt` (quoted
  heredoc) and run `--match-file`: word overlap over assistant messages, prints `MATCH <id>=<score> ...`.
  Not `--previous`: with concurrent sessions, write time does not order them.
- none and nothing quoted → `--previous`: the transcript written just before this one.
- `<id-prefix>` → that session (`--session`). No match → run `--list 8` and pick by first prompt and time.
- `list [N]` → newest N transcripts (id, time, size, first prompt) and stop.

Pass `--brief` unless the user asks for full prompt text.

```bash
python3 .claude/tools/session_digest.py --match-file .claude/scratch/digest_paste.txt --brief
python3 .claude/tools/session_digest.py --previous --brief
python3 .claude/tools/session_digest.py --session <id-prefix> --brief
python3 .claude/tools/session_digest.py --list 8
```

## Report, then continue

1. **What it was doing** — one line per user prompt; the last one is the live intent.
2. **Where it stopped** — the last message's Done/Left bullets.
3. **Unfinished** — every job that message left running (background shells, workflows, campaigns), each
   verified on disk or in the process list before it is called hung: a finished job nobody consumed looks the same.
4. **Friction to avoid** — denials and errors that recur on the same path.

Full record when a prompt was clipped: `logs/session_digest_<sid8>.json`.

## Caveats

- Concurrent sessions share the transcript dir; `--previous` orders by write time, so with two live sessions
  it can name the other live one — confirm with the `--list` first-prompt column, or use `--match-file`
  when you hold any paragraph the target session wrote.
- `--prompt-tail <text>` (`/session_end`, `/autolearn`) digests the session's OWN transcript; it is not a pickup.
