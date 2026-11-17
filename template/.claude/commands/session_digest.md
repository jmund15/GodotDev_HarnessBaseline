---
description: Recover session context for resume, handoff or inspection
---

# Session Digest

Rebuilds a session from its live transcript (`~/.claude/projects/<project>/<id>.jsonl`, append-only across
compactions). The full JSON and evidence index remain the exact retrieval source. Tool:
`.claude/tools/session_digest.py`.

Presentation modes are distinct:

- **Default / `--handoff`** — a bounded resume packet for `/clear`, parallel-session pickup and compaction
  recovery. It keeps the task anchor, latest directive, priority corrections and answers, status evidence,
  the agent's own work, recent timeline, friction, files and retrieval IDs. `.claude/tools/session_digest.py`
  owns the byte cap, which includes the active task record. The **Agent work** section is evidence from the
  transcript, disk and git: commits, pushes, dispatches and their returns, jobs with no end, the state and Close
  files the session wrote, and the not-done and decision lines the agent's last message states, marked as its
  words. It points to those files and does not copy them.
- **`--brief`** — a bounded identity card for locating or comparing sessions; `.claude/tools/session_digest.py`
  owns the cap. It is not enough to resume non-trivial work.
- **`--full`** — an offline Markdown projection written atomically to `logs/session_digest_<sid8>.full.md`.
  Stdout prints only a bounded receipt. It is human-readable evidence, not raw transcript/JSON parity and
  not model context.

## Arguments

- **The prompt quotes a session's message** → write the quote to `.claude/scratch/digest_paste.txt` (quoted
  heredoc) and run `--match-file`: word overlap over assistant messages, prints `MATCH <id>=<score> ...`.
- none and nothing quoted → `--previous`: the transcript written just before this one.
- `<id-prefix>` → that session (`--session`). No match → `--list <N>` recent transcripts and pick by first
  prompt and time; widen N until the target is identifiable.
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
2. **What it did** — the Agent work section. Its commit list is every session's on the repo's local branches in
   this session's time window, so a peer's commits appear in it.
3. **Where it stopped** — the last message's Done/Left bullets and the section's `Said` lines. Read the Close doc
   or state file the section lists before acting on a decision it names.
4. **Unfinished** — every job the section lists without a return (a dispatch with no deliverable or exit file, a
   shell with no end notice, a detached job) and the checkout's dirty and ahead counts, each
   verified on disk or in the process list before it is called hung: a finished job nobody consumed looks the same.
   The default and `--handoff` runs move the running sidecar jobs of a session with no live host into this
   session's hook state and print `Adopted <n> running job(s)` with their labels. The session watcher
   (`watch_records.py --session --wake`) then wakes this session when each one ends. A session with a live host
   keeps its jobs, and a job that already ended stays with the old session: read its result from its record.
5. **Friction to avoid** — denials and errors that recur on the same path.

Full record when a prompt was clipped: `logs/session_digest_<sid8>.json`. For complete prompt or friction
analysis, page the saved evidence index until its `pages` count is exhausted; handoff is intentionally bounded.

## Caveats

- Concurrent sessions share the transcript dir; `--previous` orders by write time, so with two live sessions
  it can name the other live one — confirm with the `--list` first-prompt column, or use `--match-file`
  when you hold any paragraph the target session wrote.
- `--prompt-tail <text>` (`/session_end`, `/autolearn`) digests the session's OWN transcript; it is not a pickup.
- Recovered command-argument rows are retained and labeled `unattributed`; they are not used as the first task anchor.
