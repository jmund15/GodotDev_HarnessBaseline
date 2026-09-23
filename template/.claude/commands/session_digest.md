---
description: Rebuild a session digest and choose brief, handoff, or offline full presentation.
---

# Session Digest

Rebuilds a session from its live transcript (`~/.claude/projects/<project>/<id>.jsonl`, append-only across
compactions). The full JSON and evidence index remain the exact retrieval source. Tool:
`.claude/tools/session_digest.py`.

Presentation modes are distinct:

- **Default / `--handoff`** — a bounded resume packet for `/clear`, parallel-session pickup and compaction
  recovery. It keeps the task anchor, latest directive, priority corrections and answers, recent timeline,
  friction, files, status evidence and retrieval IDs. The 16,384-byte cap includes the active task record.
- **`--brief`** — a 2,048-byte identity card for locating or comparing sessions. It is not enough to resume
  non-trivial work.
- **`--full`** — an offline Markdown projection written atomically to `logs/session_digest_<sid8>.full.md`.
  Stdout prints only a bounded receipt. It is human-readable evidence, not raw transcript/JSON parity and
  not model context.

## Arguments

- **The prompt quotes a session's message** → write the quote to `.claude/scratch/digest_paste.txt` (quoted
  heredoc) and run `--match-file`: word overlap over assistant messages, prints `MATCH <id>=<score> ...`.
  Not `--previous`: with concurrent sessions, write time does not order them.
- none and nothing quoted → `--previous`: the transcript written just before this one.
- `<id-prefix>` → that session (`--session`). No match → run `--list 8` and pick by first prompt and time.
- `list [N]` → newest N transcripts (id, time, size, first prompt) and stop.

Use `--select` or `--evidence-page` for exact machine evidence. `--tools` adds the tool census to the `--full`
export only.

```bash
python3 .claude/tools/session_digest.py --match-file .claude/scratch/digest_paste.txt --handoff
python3 .claude/tools/session_digest.py --previous --handoff
python3 .claude/tools/session_digest.py --session <id-prefix> --brief
python3 .claude/tools/session_digest.py --session <id-prefix> --handoff
python3 .claude/tools/session_digest.py --session <id-prefix> --full
python3 .claude/tools/session_digest.py --list 8
```

## Report, then continue

1. **What it was doing** — the task anchor, then one line per shown prompt; the latest owner directive is the
   live intent. Page omitted prompt IDs before claiming the full sequence.
2. **Where it stopped** — the last message's Done/Left bullets.
3. **Unfinished** — every job that message left running (background shells, workflows, campaigns), each
   verified on disk or in the process list before it is called hung: a finished job nobody consumed looks the same.
4. **Friction to avoid** — denials and errors that recur on the same path.

Full record when a prompt was clipped: `logs/session_digest_<sid8>.json`. For complete prompt or friction
analysis, page the saved evidence index until its `pages` count is exhausted; handoff is intentionally bounded.

## Caveats

- Concurrent sessions share the transcript dir; `--previous` orders by write time, so with two live sessions
  it can name the other live one — confirm with the `--list` first-prompt column, or use `--match-file`
  when you hold any paragraph the target session wrote.
- `--prompt-tail <text>` (`/session_end`, `/autolearn`) digests the session's OWN transcript; it is not a pickup.
- Recovered command-argument rows are retained and labeled `unattributed`; they are not used as the first task anchor.
