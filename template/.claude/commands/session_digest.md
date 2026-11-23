---
description: Recover a session from its transcript for resume, handoff or inspection
---

# /session_digest — recover a session

Rebuilds any session from its transcript (`~/.claude/projects/<project>/<id>.jsonl`, append-only across compactions) through `.claude/tools/session_digest.py`. Use it for a session with no state file: small work, a crash or `/clear` before `/close`, a peer's session, one found by a pasted quote. An initiative with a state file resumes with `/drive <slug>` instead. The tool owns the packet's contents and its byte cap.

## Arguments

- The prompt quotes a session's message: write the quote to `.claude/scratch/digest_paste.txt` with a quoted heredoc and run `--match-file`. It prints `MATCH <id>=<score> ...`; digest the best match.
- `<id-prefix>`: that session (`--session`). No match: list recent transcripts and pick by first prompt and time, widening N until the target is identifiable.
- `list [N]`: print the newest N transcripts (id, time, size, first prompt) and stop.
- `full <id-prefix>`: write the whole session as Markdown (`--full`). Stdout prints only the file path; read the part you need from it.
- `workflow <dir>`: read a fan-out run's journal in that directory (`--workflow-dir`), for `/salvage_fanout`.
- None and nothing quoted: `--previous`, the transcript written just before this one.

```bash
python3 .claude/tools/session_digest.py --match-file .claude/scratch/digest_paste.txt
python3 .claude/tools/session_digest.py --previous
python3 .claude/tools/session_digest.py --session <id-prefix>
python3 .claude/tools/session_digest.py --session <id-prefix> --full
python3 .claude/tools/session_digest.py --list 8
python3 .claude/tools/session_digest.py --workflow-dir <run dir>
```

`--previous` orders transcripts by write time, so with two live sessions it can name the other live one. Confirm with the `--list` first-prompt column, or use `--match-file` when you hold a paragraph the target session wrote.

## Report, then continue

1. **What it was doing:** the task anchor, then one line per shown prompt. The latest owner directive is the live intent.
2. **What it did:** its commits, pushes, dispatches and their returns, and the files it wrote. The commit list covers the repo's local branches in the session's time window, so a peer's commits appear in it.
3. **Where it stopped:** its last message's done and left lines, marked as its words. Read any state file or Close doc it names before acting on a decision there.
4. **Unfinished:** each job with no return (a dispatch with no output, a shell with no end notice) and the checkout's dirty and ahead counts. Verify each on disk or in the process list before calling it hung: a finished job nobody read looks the same.
5. **Friction to avoid:** denials and errors that recur on the same path.

Then continue the work from step 3.
