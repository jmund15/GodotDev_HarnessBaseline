#!/usr/bin/env python3
"""Recover one session from its append-only transcript, or read one explore Workflow's journal.

Usage:
  session_digest.py                      the transcript written just before the active session's (--previous)
  session_digest.py --session <id>       the session with that id or a unique prefix, in this checkout or a worktree
  session_digest.py --match-file PATH    the session whose assistant wrote the text in PATH (a pasted message)
  session_digest.py --list N             the newest N transcripts, one line each
  session_digest.py ... --full           write the whole session as Markdown to logs/ and print its path
  session_digest.py --workflow-dir DIR   one explore Workflow's journal as JSON (hash, lenses, claims, gaps)

The packet is the resume packet: the task anchor, the owner's latest directive, the agent's own work (commits,
pushes, Workflow dispatches and returns, open shells, handoff files), the prompt timeline, friction and files.
It stays within HANDOFF_MAX_BYTES by shedding optional rows in the order of LEVELS.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
from _hook_state import replace_path  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cli_usage  # noqa: E402
from _transcript_summary import TranscriptSummaryBuilder, owner_line  # noqa: E402
from _owner_text import classify, meta_arguments  # noqa: E402
import _background_tasks  # noqa: E402
import _owner_text  # noqa: E402
import _transcript_scan  # noqa: E402
import _transcript_summary  # noqa: E402

HANDOFF_MAX_BYTES = 16_384
MATCH_MIN = 0.6  # share of the pasted text's distinctive words one assistant block must hold
MATCH_SCAN = 40  # newest transcripts searched for a pasted message
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{4,}")
# The packet sheds optional rows in this order until it fits HANDOFF_MAX_BYTES; "clip" scales the text clips.
LEVELS = (
    {"timeline": "all", "friction": 8, "files": 16, "agent": 6, "clip": 1.0},
    {"timeline": "priority", "friction": 1, "files": 0, "agent": 3, "clip": 0.5},
    {"timeline": "priority", "friction": 0, "files": 0, "agent": 1, "clip": 0.25},
)

_SUBSTANTIVE_CONTROL_COMMANDS = frozenset({
    "/model", "/effort", "/fast", "/compact", "/context", "/clear", "/cost",
    "/status", "/config", "/help", "/resume", "/exit",
})
_SUBSTANTIVE_ROUTING_COMMANDS = frozenset({"/effort"})
_TASK_ANCHOR_WRAPPER_COMMANDS = frozenset({
    "/overnight", "/session_end", "/commit_push", "/clean_push", "/create_pr", "/pr_ready",
})
_SUBSTANTIVE_SLASH_RE = re.compile(r"/[\w:.-]+$")


def clip(text: str, n: int) -> str:
    text = text or ""
    if n and len(text) > n:
        return text[:n].rstrip() + f" …[+{len(text) - n} chars]"
    return text


def _excerpt(text: str, n: int) -> str:
    return clip(" ".join((text or "").split()), n)


def _active_session() -> str:
    return (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()


def is_substantive_owner_prompt(message: dict) -> bool:
    """The owner-row policy shared by digest selection and compact recovery.

    Interrupts, bare slash commands, client controls and the arguments of a model-issued `Skill`
    call (`issuer: model`) are not owner prompts. Argument-bearing feature commands and
    `/effort <level>` remain owner evidence.
    """
    content = (message.get("content") or "").strip() if isinstance(message, dict) else ""
    if not content or "interrupt" in (message.get("signals") or []) or message.get("issuer") == "model":
        return False
    if content.startswith("(command) "):
        content = content[len("(command) "):].lstrip()
    if not content.startswith("/"):
        return True
    words = content.split()
    if words[0] in _SUBSTANTIVE_CONTROL_COMMANDS:
        return words[0] in _SUBSTANTIVE_ROUTING_COMMANDS and len(words) > 1
    return not (len(words) == 1 and _SUBSTANTIVE_SLASH_RE.fullmatch(words[0]))


def projects_dir(cwd: str) -> Path:
    """The transcript folder for `cwd` under the session's config dir (`CLAUDE_CONFIG_DIR`, default `~/.claude`)."""
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(os.path.expanduser(config)) if config else Path.home() / ".claude"
    return root / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)


def project_dirs(cwd: str) -> list[Path]:
    """Transcript folders of `cwd`'s checkout and of its linked worktrees, the checkout first."""
    roots = [cwd]
    try:
        listing = subprocess.run(["git", "-C", cwd, "worktree", "list", "--porcelain"], stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, encoding="utf-8", timeout=15, check=True).stdout
        roots += [line[len("worktree "):].strip() for line in listing.splitlines() if line.startswith("worktree ")]
    except (OSError, subprocess.SubprocessError):
        pass
    return list(dict.fromkeys(projects_dir(root) for root in roots))


def _transcripts(stores: list[Path]) -> list[Path]:
    """Every transcript in `stores`, newest first."""
    files = {path for store in stores if store.is_dir() for path in store.glob("*.jsonl")}
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def store_transcripts(stores: list[Path], session: str, prefix: bool = False) -> list[Path]:
    """The transcripts in `stores` whose id is `session` exactly, else (with `prefix`) starts with it."""
    files = _transcripts(stores)
    exact = [path for path in files if path.stem == session]
    if exact or not prefix:
        return exact
    return [path for path in files if path.stem.startswith(session)]


def session_store(cwd: str, session: str | None = None) -> Path:
    """The folder holding `session` (default: this session) among `cwd`'s checkouts; `cwd`'s own when none or two hold it."""
    stores = project_dirs(cwd)
    hits = store_transcripts(stores, session or _active_session())
    return hits[0].parent if len(hits) == 1 else stores[0]


def previous_transcript(pdir: Path) -> Path:
    """The nearest transcript written before the active session's that holds a user prompt."""
    files = _transcripts([pdir])
    active = _active_session()
    anchor = next((path for path in files if active and path.stem == active), None)
    if anchor is None:
        sys.exit("active session is missing or unknown; pass --session <id>")
    for path in files[files.index(anchor) + 1:]:
        if last_prompt(path):
            return path
    sys.exit("no previous transcript with a user prompt; pass --session <id>")


def _tokens(text: str) -> set[str]:
    """Distinctive words (5+ chars, case-folded). Markdown and table pipes fall away, which is what survives a
    terminal re-rendering of a pasted message."""
    return {w.lower() for w in _WORD.findall(text or "")}


def match_transcript(stores: list[Path], text: str) -> Path:
    """The transcript whose assistant wrote `text`: the best single assistant block by token overlap across the
    newest MATCH_SCAN transcripts. Assistant blocks only, so the session that received the paste never matches itself."""
    want = _tokens(text)
    if len(want) < 8:
        sys.exit(f"match text has {len(want)} distinctive words; paste at least a paragraph")
    scored = []
    for path in _transcripts(stores)[:MATCH_SCAN]:
        best = 0.0
        for block in assistant_blocks(path):
            best = max(best, len(want & _tokens(block)) / len(want))
        scored.append((best, path))
    scored.sort(key=lambda pair: -pair[0])
    if not scored or scored[0][0] < MATCH_MIN:
        nearest = "\n".join(f"  {path.stem[:8]}  {score:.2f}" for score, path in scored[:3])
        sys.exit(f"no assistant message overlaps the paste at >= {MATCH_MIN:.0%} of its words; nearest:\n{nearest}\n"
                 "pass --session <id>")
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        sys.exit("ambiguous assistant-message match; pass an exact --session <id>")
    print("MATCH " + "  ".join(f"{path.stem[:8]}={score:.2f}" for score, path in scored[:3]), file=sys.stderr)
    return scored[0][1]


def assistant_blocks(path: Path, min_len: int = 200):
    for row in _rows(path, "assistant"):
        content = (row.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = (block.get("text") or "").strip()
                if len(text) >= min_len:
                    yield text


def list_sessions(pdir: Path, n: int) -> str:
    """The newest N transcripts, one line each: prefix, last write, size, first real prompt."""
    out = []
    for path in _transcripts([pdir])[:n]:
        st = path.stat()
        when = datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")
        first = ""
        for row in _rows(path, "user"):
            content = (row.get("message") or {}).get("content") or []
            text = content if isinstance(content, str) else " ".join(x.get("text", "") for x in content if isinstance(x, dict))
            if text and not text.lstrip().startswith("<"):
                first = clip(" ".join(text.split()), 110)
                break
        tag = "  (active session)" if path.stem == _active_session() else ""
        out.append(f"{path.stem[:8]}  {when}  {st.st_size // 1024:>6} KB  {first}{tag}")
    return "\n".join(out)


def _rows(path: Path, kind: str, include_sidechain: bool = False):
    """Parse complete JSON rows before filtering by type; JSON whitespace is not identity.

    Sidechain rows are a subagent's turns. Excluded by default, because in a SESSION transcript
    they are delegated work the session digest should not read as the session's own. Pass
    `include_sidechain=True` when the file IS a subagent transcript — every row there is a
    sidechain, so the default yields nothing and the file reads as empty rather than as a subagent.
    """
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("type") == kind and (include_sidechain or not row.get("isSidechain")):
                if not isinstance(row.get('message'), dict):
                    row = dict(row, message={'content': row.get('content', '')})
                yield row


def last_prompt(path: Path) -> str:
    """Last real user prompt (string content) in a transcript, or ''."""
    last = ""
    for row in _rows(path, "user"):
        c = (row.get("message") or {}).get("content")
        # Hook/slash-command echoes (`<user-prompt-submit-hook>`, `<command-name>`, `<local-command-…>`)
        # are user rows too; a /clear stub is nothing but them.
        if isinstance(c, str) and not c.lstrip().startswith(("<", "[Request")):
            last = c
    return last


def last_assistant_text(path: Path, min_len: int = 40) -> str:
    """The assistant's last substantive text block — the message the session ended on, which is
    where a handoff, a recap or an open question lives."""
    last = ""
    for row in _rows(path, "assistant"):
        content = (row.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = (block.get("text") or "").strip()
                if len(t) >= min_len:
                    last = t
    return last


def recover_meta_command_prompts(path: Path) -> list[dict]:
    """Slash-command turns `_transcript_summary.py` drops as `isMeta` that still carry text after
    `ARGUMENTS:`.

    `TranscriptSummaryBuilder._process_user_message` returns on `isMeta` before ever checking for
    that trailing argument text, so a command invoked WITH arguments is as invisible as the static
    skill/command body every re-invocation re-injects. Observed 2026-09-14, session
    `417af437-e526-4a44-bc5c-af551368213a`: three `/overnight <goal>` turns dropped this way, the
    scan reporting 6 prompts where the owner typed 7. Recovered here rather than in
    `_transcript_summary.py`, which `kill_guard.py`/`transcript_backup.py`/`ladder_ingest.py` also
    read unchanged.

    `issuer` is `model` when the row carries `sourceToolUseID` (the body a model `Skill` call
    leaves), else `owner`: a typed command's body row carries none.
    """
    recovered = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            recovered += _meta_command_rows(line, i)
    return recovered


def _meta_command_rows(line: str, index: int) -> list[dict]:
    """`recover_meta_command_prompts` for one transcript line. Only a `meta_arguments` row recovers: a compaction
    summary is `meta` too, and one that quotes an `ARGUMENTS:` line is the client's recap, not a command."""
    if "ARGUMENTS:" not in line:  # `meta_arguments` needs that literal; JSON never escapes it
        return []
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return []
    row = classify(entry, index, raw=line)
    if row is None or row.kind != "meta_arguments" or row.sidechain:
        return []
    recovered = []
    for text in row.blocks:
        found = meta_arguments(text)
        if not found:
            continue  # a static skill/command body with no typed argument stays excluded
        label, goal = found
        recovered.append({
            "index": index, "timestamp": entry.get("timestamp"),
            "content": f"{label} {goal}", "signals": [], "matched_patterns": [],
            "recovered": "command_arguments",
            "issuer": "model" if entry.get("sourceToolUseID") else "owner",
        })
    return recovered


def recover_question_answers(path: Path) -> list[dict]:
    """The owner's AskUserQuestion answers, which the builder files as tool results, not prompts.

    `_process_user_message` routes every tool_result row to friction capture, so a decision the
    owner gives through a question never reached the prompt list (observed 2026-09-14, session
    3259384b: the closeout decisions arrived only as answers). Errored and sidechain rows are not
    answers.
    """
    asked, recovered = {}, []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            recovered += _answer_rows(line, i, asked)
    return recovered


def _answer_rows(line: str, index: int, asked: dict) -> list[dict]:
    """`recover_question_answers` for one transcript line; `asked` holds the AskUserQuestion tool_use
    ids seen so far (as keys) and gains this line's."""
    # A row matters when it asks (names the tool) or answers (names an asked tool_use id).
    if "AskUserQuestion" not in line and not any(tool_id in line for tool_id in asked):
        return []
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(entry, dict) or entry.get("isSidechain"):
        return []
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    for block in content:
        if not isinstance(block, dict):
            continue
        if entry.get("type") == "assistant" and block.get("type") == "tool_use" \
                and block.get("name") == "AskUserQuestion" and block.get("id"):
            asked[block["id"]] = True
    if entry.get("type") != "user":
        return []
    row = classify(entry, index, raw=line)
    return [{"index": index, "timestamp": entry.get("timestamp"),
             "content": f"(answer) {text.strip()}", "signals": [], "matched_patterns": [],
             "recovered": "question_answer"}
            for tool_use_id, text, is_error in (row.tool_results if row else ())
            if tool_use_id and tool_use_id in asked and not is_error and text.strip()]


def merge_recovered_prompts(user_messages: list[dict], recovered: list[dict]) -> list[dict]:
    """Fold recovered prompts into the builder's list, in transcript order. Rows that share a
    line index share one evidence ID and keep both bodies."""
    if not recovered:
        return user_messages
    merged = [dict(row) for row in user_messages]
    by_index = {row.get("index"): row for row in merged}
    seen = {(row.get("index"), row.get("content")) for row in merged}
    for row in recovered:
        key = (row["index"], row.get("content"))
        if key in seen:
            continue
        existing = by_index.get(row["index"])
        if existing is None:
            added = dict(row)
            merged.append(added)
            by_index[row["index"]] = added
        else:
            existing["content"] = "\n".join(
                text for text in (existing.get("content"), row.get("content")) if text)
            if row.get("recovered") == "command_arguments":
                existing["recovered"] = "command_arguments"
        seen.add(key)
    merged.sort(key=lambda m: m.get("index") or 0)
    return merged


def _owner_feed(state: dict, index: int, line: str) -> None:
    found = owner_line(line, index, full_evidence=True)
    if found is not None and found[0] != "compaction":
        state["messages"].append(found[1])
    state["meta"] += _meta_command_rows(line, index)
    state["answers"] += _answer_rows(line, index, state["asked"])


OWNER_SCAN = _transcript_scan.Scanner(
    "owner_prompts", lambda: {"messages": [], "meta": [], "answers": [], "asked": {}}, _owner_feed,
    sources=[__file__, _transcript_summary.__file__, _owner_text.__file__])


def owner_prompts(path: Path) -> list[dict]:
    """Every owner message in transcript order: typed prompts, command arguments and question
    answers. The one definition this digest and hooks/compact_directive_anchor.py both read. One pass
    folds the builder's user messages and both recover passes, resumed from the transcript scan cache
    (hooks/_transcript_scan.py); the result equals `merge_recovered_prompts` over the three cold passes."""
    state = _transcript_scan.scan(path, OWNER_SCAN)
    return merge_recovered_prompts(state["messages"], state["meta"] + state["answers"])


def last_owner_timestamp(messages: list[dict]) -> str | None:
    """The timestamp of the owner's last substantive prompt: the line the agent's own work is counted from."""
    stamps = [m["timestamp"] for m in messages if is_substantive_owner_prompt(m) and m.get("timestamp")]
    return max(stamps, key=lambda stamp: _aw_epoch(stamp) or 0) if stamps else None
# ---- Agent work: what the agent itself did, for the handoff packet -------------------------------------
# The owner's prompts say what was asked. A session that ran unattended for hours holds most of its state in
# what the agent did after them: commits, pushes, dispatched jobs and their returns, background jobs still
# open, the state and Close files it wrote, and what it told the owner it left undone or needs decided.
# `agent_work(path, last_owner_ts, cwd)` reads the transcript once and git once and returns one JSON-native
# dict, stored as `agent_work` in the digest and rendered by `agent_work_lines`. Every fact names its source:
#   transcript - a tool call the hooks did not deny, paired with its result; a `<task-notification>` naming a
#                background task (`hooks/_background_tasks.py` owns that notice shape);
#   disk       - a deliverable, state or Close file the transcript names, checked for existence and size;
#   git        - the commits in the session's time window (`git log --branches`, every session's) and the
#                checkout's state now. A commit result in a transcript is not evidence (`-q`, `| tail`).
# Nothing here says work is complete. The agent's own closing words are rendered apart and labeled. A file the
# session wrote is pointed at, never copied: its owner (the Close doc, a task record, a state file) holds it.

AW_GIT_TIMEOUT_S = 8  # bounds one git call; a timeout reports git unavailable rather than stalling the packet
AW_GIT_MAX_COMMITS = 500  # bounds `git log`; the count line says when the window held more
AW_KEEP = {"commits": 40, "dispatches": 200, "shells": 100, "files": 60, "pushes": 20}  # rows kept per list; the packet shows fewer
AW_ITEM_CLIP = 170  # one packet line stays one screen line
AW_STATED_MAX = 8  # the not-done and decision lines kept from the agent's last message

_AW_PUSH_RE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?(?:\s+-c\s+\S+)*\s+push\b")
_AW_COMMIT_RE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?(?:\s+-c\s+\S+)*\s+commit\b")
_AW_REF_UPDATE_RE = re.compile(r"[0-9a-f]{7,40}\.\.\.?[0-9a-f]{7,40}\s+\S+\s+->\s+\S+|\[new branch\]\s+\S+\s+->\s+\S+")
_AW_SHORT_HASH_RE = re.compile(r"[0-9a-f]{12,40}")
_AW_RUN_ID_RE = re.compile(r"Run ID:\s*(\S+)")
_AW_TASK_ID_RE = re.compile(r"Task ID:\s*(\w+)|background with ID:\s*(\w+)")
# A file a later session reads to resume: a state, handoff, resume or Close doc, the vault's Overnight
# Close docs, and the plan files.
_AW_HANDOFF_FILE_RE = re.compile(r"(?i)(?:^|[\\/])(?:state|handoff|resume|status|close)[\w.-]*\.md$"
                              r"|[\\/]Overnight[\\/][^\\/]+\.md$|[\\/]\.claude[\\/]plans[\\/][^\\/]+\.md$")
_AW_CLOSE_DOC_RE = re.compile(r"[\\/]Overnight[\\/][^\\/]+\.md$")  # `commands/overnight.md` names where the Close doc lives
_AW_MD_TOKEN_RE =re.compile(r"""[^\s"'<>|;&()=]+\.md\b""")
_AW_BASH_WRITE_WORDS = ("tee ", "open(", "write_text(", "Set-Content", "Out-File", "Add-Content", "shutil.copy")
_AW_HEADING_RE = re.compile(r"(?i)\b(?:not done|left undone|undone|remaining|unfinished|still open|"
                         r"decisions?(?: for you)?|open decisions?|questions?|blockers?|blocked|parked)\b")
_AW_CODE_RE = re.compile(r"^[\s>*_`-]*(?:\*\*)?\s*(?:Q|D)\d+\b")


def _aw_epoch(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _aw_hhmm(stamp):
    text = str(stamp or "")
    return text[11:16] if len(text) >= 16 else "--:--"


def _aw_clip(text, n=AW_ITEM_CLIP):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _aw_result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ""


def _aw_rel(path, cwd):
    """`path` relative to `cwd` when it sits under it, else as written: a packet line a reader opens."""
    try:
        rel = os.path.relpath(path, cwd) if cwd and os.path.isabs(path) else path
    except ValueError:
        return path
    return path if rel.startswith("..") else rel.replace("\\", "/")


def _aw_size(n):
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def _aw_stat(path, cwd):
    for candidate in (path, os.path.join(cwd, path) if cwd else path):
        try:
            info = os.stat(candidate)
        except (OSError, ValueError):
            continue
        if os.path.isfile(candidate):
            return {"path": candidate, "bytes": info.st_size, "mtime": info.st_mtime}
    return None


def _aw_git(cwd, *args):
    done = subprocess.run(["git", "-C", cwd, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=AW_GIT_TIMEOUT_S)
    if done.returncode != 0:
        raise OSError((done.stderr or "git failed").strip().splitlines()[0] if (done.stderr or "").strip() else "git failed")
    return done.stdout


def agent_work_git(cwd, start, end, owner_epoch):
    """The commits in `[start, end]` (every session's, on every local branch) and the checkout's state now.

    `{"unavailable": <reason>}` when `cwd` is missing, outside git, or git does not answer in time."""
    if not cwd or not os.path.isdir(cwd):
        return {"unavailable": "the session's working directory is gone"}
    try:
        status = _aw_git(cwd, "status", "--porcelain=v1", "-b").splitlines()
        head = _aw_git(cwd, "log", "-1", "--format=%h%x09%s").strip().split("\t", 1)
        since = start
        until = datetime.fromtimestamp((_aw_epoch(end) or 0) + 60).astimezone().isoformat() if end else ""
        window = [f"--since={since}", *([f"--until={until}"] if until else []), f"--max-count={AW_GIT_MAX_COMMITS}"] if since else []
        log = _aw_git(cwd, "log", "--branches", *window, "--format=%H%x09%h%x09%ct%x09%s") if since else ""
    except (OSError, subprocess.SubprocessError) as exc:
        return {"unavailable": _aw_clip(str(exc), 120)}
    header = status[0][3:] if status and status[0].startswith("## ") else ""
    branch = header.split("...")[0].split(" ")[0]
    ahead = re.search(r"ahead (\d+)", header)
    behind = re.search(r"behind (\d+)", header)
    rows = []
    for line in log.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            when = datetime.fromtimestamp(int(parts[2]), timezone.utc) if parts[2].isdigit() else None
            rows.append({"full": parts[0], "hash": parts[1], "time": when.strftime("%Y-%m-%dT%H:%M:%SZ") if when else "",
                         "subject": parts[3]})
    commits = rows  # git log order: newest first, one row per commit on any local branch
    after = sum(1 for c in commits if owner_epoch is not None and (_aw_epoch(c["time"]) or 0) > owner_epoch)
    return {"branch": branch, "head": head[0] if head else "", "head_subject": head[1] if len(head) > 1 else "",
            "upstream": "..." in header, "ahead": int(ahead.group(1)) if ahead else 0,
            "behind": int(behind.group(1)) if behind else 0, "dirty": max(len(status) - 1, 0),
            "commits": {"total": len(commits), "after_owner": after, "newest": commits[:AW_KEEP["commits"]]}}


def agent_work(path, last_owner_ts=None, fallback_cwd=None, with_git=True):
    """The agent-work record of one transcript. `last_owner_ts` is the timestamp of the owner's last prompt.
    Git and relative paths use the working directory the transcript last ran in, `fallback_cwd` when it is gone."""
    owner_epoch = _aw_epoch(last_owner_ts) if last_owner_ts else None
    uses = {}
    ended = {}
    first_ts = last_ts = last_cwd = None
    total_calls = calls_after = turns_after = 0
    last_text = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or row.get("isSidechain"):
                continue
            ts = row.get("timestamp")
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
            last_cwd = row.get("cwd") or last_cwd
            after = owner_epoch is not None and (_aw_epoch(ts) or 0) > owner_epoch
            message = row.get("message") if isinstance(row.get("message"), dict) else {}
            content = message.get("content")
            kind = row.get("type")
            if kind == "assistant" and isinstance(content, list):
                used = False
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        used = True
                        total_calls += 1
                        calls_after += after
                        uses[block.get("id")] = {"name": block.get("name"), "input": block.get("input") or {},
                                                 "ts": ts, "cwd": row.get("cwd") or last_cwd}
                    elif block.get("type") == "text" and len((block.get("text") or "").strip()) >= 40:
                        last_text = {"timestamp": ts, "after_owner": after}
                turns_after += after and used
            elif kind == "user":
                texts = [content] if isinstance(content, str) else []
                for block in content if isinstance(content, list) else ():
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        entry = uses.get(block.get("tool_use_id"))
                        if entry is not None:
                            entry["result"] = _aw_result_text(block)
                            entry["error"] = bool(block.get("is_error"))
                            entry["task_id"] = _background_tasks.launched_task_id(row)
                    elif isinstance(block, dict) and block.get("type") == "text":
                        texts.append(block.get("text") or "")
                for text in texts:
                    for notice in _background_tasks.ended_notices({"content": text}):
                        ended[notice["task_id"]] = {"status": notice.get("status"), "time": ts,
                                                    "exit_code": notice.get("exit_code")}
            elif kind == "queue-operation":
                for notice in _background_tasks.ended_notices(row):
                    ended[notice["task_id"]] = {"status": notice.get("status"), "time": ts,
                                                "exit_code": notice.get("exit_code")}
            elif kind == "attachment":
                attachment = row.get("attachment") if isinstance(row.get("attachment"), dict) else {}
                if attachment.get("type") == "queued_command":
                    for notice in _background_tasks.ended_notices({"content": attachment.get("prompt")}):
                        ended[notice["task_id"]] = {"status": notice.get("status"), "time": ts,
                                                    "exit_code": notice.get("exit_code")}
    work = {"window": {"start": first_ts, "end": last_ts, "last_owner": last_owner_ts, "cwd": last_cwd},
            "tool_calls": {"session": total_calls, "since_owner": calls_after},
            "turns_since_owner": turns_after, "last_text": last_text,
            "commit_calls": 0, "push_calls": 0, "pushes": [], "dispatches": [], "shells": [], "files": []}
    cwd = last_cwd if last_cwd and os.path.isdir(last_cwd) else (fallback_cwd or "")
    files = {}
    for entry in uses.values():
        if entry.get("error") or "result" not in entry:
            continue
        _aw_classify(entry, work, files, ended, cwd)
    work["files"] = sorted(files.values(), key=lambda f: f["last_ts"] or "", reverse=True)[:AW_KEEP["files"]]
    for key in ("pushes", "dispatches", "shells"):
        work[key] = work[key][-AW_KEEP[key]:]
    work["git"] = (agent_work_git(cwd, first_ts, last_ts, owner_epoch) if with_git and first_ts
                   else {"unavailable": "not requested" if not with_git else "no timestamps in the transcript"})
    return work


def _aw_record_file(files, path, ts, cwd):
    info = _aw_stat(path, cwd)
    key = os.path.normcase(os.path.abspath(os.path.join(cwd, path))) if cwd else os.path.normcase(os.path.normpath(path))
    row = files.setdefault(key, {"path": path, "writes": 0, "last_ts": ts, "bytes": None, "on_disk": False})
    if os.path.isabs(path):
        row["path"] = path
    row["writes"] += 1
    row["last_ts"] = ts
    row["on_disk"] = info is not None
    row["bytes"] = info["bytes"] if info else None


def _aw_classify(entry, work, files, ended, cwd):
    name, inputs, result, ts = entry["name"], entry["input"], entry["result"], entry["ts"]
    if name in ("Write", "Edit"):
        path = str(inputs.get("file_path") or "")
        if path and _AW_HANDOFF_FILE_RE.search(path):
            _aw_record_file(files, path, ts, cwd)
        return
    if name == "Workflow":
        run = _AW_RUN_ID_RE.search(result)
        task = _AW_TASK_ID_RE.search(result)
        if run:
            task_id = (task.group(1) or task.group(2)) if task else None
            end = ended.get(task_id) if task_id else None
            work["dispatches"].append({
                "kind": "workflow", "label": Path(str(inputs.get("scriptPath") or "workflow")).stem + " " + run.group(1),
                "model": None, "effort": None, "time": ts, "return": (
                    {"how": "notice", "status": end["status"], "time": end["time"]} if end else None)})
        return
    if name != "Bash":
        return
    command = str(inputs.get("command") or "")
    if _AW_PUSH_RE.search(command):
        work["push_calls"] += 1
        refs = _AW_REF_UPDATE_RE.findall(result)
        if refs:
            work["pushes"].append({"time": ts, "refs": [_AW_SHORT_HASH_RE.sub(lambda m: m.group(0)[:9], r) for r in refs][:3]})
    if _AW_COMMIT_RE.search(command):
        work["commit_calls"] += 1
    if inputs.get("run_in_background") and entry.get("task_id"):
        end = ended.get(entry["task_id"])
        work["shells"].append({"id": entry["task_id"], "time": ts, "end": end,
                               "label": _aw_clip(inputs.get("description") or command, 90)})
    if any(word in command for word in _AW_BASH_WRITE_WORDS) or re.search(r"(?<![0-9&>])>{1,2}\s*[\"']?[^\s\"'|;&]", command):
        for token in _AW_MD_TOKEN_RE.findall(command):
            if _AW_HANDOFF_FILE_RE.search(token.replace("\\", "/")) or _AW_HANDOFF_FILE_RE.search(token):
                _aw_record_file(files, token, ts, cwd)


def _aw_title(text):
    """The first clause of a row: up to its first `?`, `;` or sentence end when that is short, else a clip."""
    found = re.search(r"\?|;|\. ", text)
    return text[:found.end()].rstrip(" ;.") if found and found.end() <= 110 else _aw_clip(text, 90)


def agent_work_stated(text, limit=AW_STATED_MAX, title_only=False):
    """Lines the agent's own message puts under a not-done or decision heading, and every `Q<n>`/`D<n>` line.

    Words of the agent, unverified: the packet labels them so. `title_only` keeps each row's first clause:
    the Close doc the session wrote owns the rest of the row (options, recommendation, default)."""
    items = []
    under = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") or re.fullmatch(r"\*\*[^*]+:?\*\*:?", line):
            heading = line.lstrip("# ").strip("*: ")
            under = heading if _AW_HEADING_RE.search(heading) else None
            continue
        if _AW_CODE_RE.match(line) or (under and re.match(r"^[-*\d]", line)):
            body = re.sub(r"\*\*", "", re.sub(r"^[-*\d.)\s>]+", "", line))
            row = ((under + ": ") if under and not _AW_CODE_RE.match(line) else "") + body
            items.append(_aw_title(row) if title_only else _aw_clip(row))
    return items[:limit]


def agent_work_lines(work, outcome="", limit=6):
    """The packet's lines for one `agent_work` record. `limit` caps each list (None: every row; 0: counts only)."""
    def newest(rows):
        return list(reversed(rows))[:limit]

    win = work.get("window") or {}
    calls = work.get("tool_calls") or {}
    git = work.get("git") or {}
    owner = _aw_hhmm(win.get("last_owner")) if win.get("last_owner") else None
    out = ["## Agent work — transcript, disk and git evidence (UTC), not a completion verdict"]
    if owner:
        minutes = max(int(((_aw_epoch(win.get("end")) or 0) - (_aw_epoch(win.get("last_owner")) or 0)) // 60), 0)
        out.append(f"Since the last owner prompt ({owner}): {minutes} min · {work.get('turns_since_owner', 0)} assistant turns "
                   f"with tool calls · {calls.get('since_owner', 0)} tool calls. Whole session: {calls.get('session', 0)} tool calls.")
    else:
        out.append(f"No owner prompt found. Whole session: {calls.get('session', 0)} tool calls.")

    if "unavailable" in git:
        out.append(f"Commits — git unavailable ({git['unavailable']}); transcript: {work.get('commit_calls', 0)} `git commit` calls not denied.")
    else:
        c = git["commits"]
        out.append(f"Commits — git, every session on this repo's local branches, {_aw_hhmm(win.get('start'))}–{_aw_hhmm(win.get('end'))}: "
                   f"{c['total']} total, {c['after_owner']} since the last owner prompt "
                   f"(transcript: {work.get('commit_calls', 0)} `git commit` calls not denied).")
        out += [f"- {x['hash']} {_aw_hhmm(x['time'])} {_aw_clip(x['subject'], 110)}" for x in c["newest"][:limit]]
        out.append(f"Git now — {git['branch'] or 'detached'} @{git['head']} · ahead {git['ahead']} / behind {git['behind']}"
                   f"{'' if git['upstream'] else ' (no upstream)'} · {git['dirty']} dirty path(s) in {_aw_clip(win.get('cwd'), 60)}")
    pushes = work.get("pushes") or []
    out.append(f"Pushes — transcript: {work.get('push_calls', 0)} `git push` call(s) not denied, {len(pushes)} printed a ref update."
               + (f" Last: {pushes[-1]['refs'][0]} ({_aw_hhmm(pushes[-1]['time'])})" if pushes else ""))

    dispatches = work.get("dispatches") or []
    if dispatches:
        back = [d for d in dispatches if d.get("return")]
        open_ = [d["label"] for d in dispatches if not d.get("return")]
        out.append(f"Dispatches — transcript and disk: {len(dispatches)} launched, {len(back)} with a return on disk or an end notice"
                   + (f"; no return: {', '.join(open_[:8])}{f' …[+{len(open_) - 8}]' if len(open_) > 8 else ''}." if open_ else "."))
        unreturned = [d for d in dispatches if not d.get("return")]
        for d in (newest(unreturned) + newest(back))[:limit]:
            pin = f" ({d['model']} {d['effort']})" if d.get("model") else ""
            ret = d.get("return")
            if not ret:
                tail = "no end notice"
            elif ret["how"] == "notice":
                tail = f"returned: {ret['status']} notice {_aw_hhmm(ret['time'])}"
            else:
                tail = f"returned: {_aw_rel(str(ret['path']), win.get('cwd'))} {_aw_size(ret['bytes'])}"
            out.append(f"- {d['label']}{pin} {d['kind']} {_aw_hhmm(d['time'])} → {tail}")
    shells = [s for s in work.get("shells") or [] if not s.get("end")]
    gone = len(work.get("shells") or []) - len(shells)
    out.append(f"Background shells — {len(shells)} with no end notice, {gone} ended. Verify each on disk or in the process list "
               f"before calling it hung." if shells or gone else "Background shells — none launched.")
    out += [f"- {s['label']} ({_aw_hhmm(s['time'])}, task {s['id']})" for s in newest(shells)]
    files = work.get("files") or []
    if files:
        out.append("Handoff files this session wrote — pointers; read the file, the packet does not copy it:")
        out += [f"- {_aw_rel(f['path'], win.get('cwd'))} · last write {_aw_hhmm(f['last_ts'])} · "
                + (f"{_aw_size(f['bytes'])} on disk" if f["on_disk"] else "not on disk")
                for f in files[:limit]]
    close = next((f for f in files if f["on_disk"] and _AW_CLOSE_DOC_RE.search(f["path"])), None)
    said = agent_work_stated(outcome, title_only=bool(close))
    stamp = (work.get("last_text") or {})
    when = f" ({_aw_hhmm(stamp.get('timestamp'))}, {'after' if stamp.get('after_owner') else 'before'} the last owner prompt)" if stamp else ""
    if said:
        owner = (f"; first clauses only, the rows are in the Close doc {_aw_rel(close['path'], win.get('cwd'))}" if close else "")
        out.append(f"Said in the last assistant message{when} — the agent's words, unverified{owner}:")
        out += [f"- {line}" for line in (said if limit is None else said[:max(limit, 3)])]
    return out



# ---- Explore Workflow journal: the one result contract v2 keeps --------------------------------------------
# A native explore Workflow writes `journal.jsonl` in its transcriptDir: a `started` row per agent, then one
# `result` or `failed` row for that agent. The reader pairs them, checks each result's shape, and returns the
# valid claims and gaps with the coverage of every lens. The review kind went with `review_fanout.js`.

WORKFLOW_CONTRACT = "native-workflow-journal/v1"
EXPLORE_BEARINGS = ("premise-contradiction", "reuse-candidate", "constraint", "blast-radius", "context")


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _claim_ok(claim: object) -> bool:
    if not isinstance(claim, dict):
        return False
    if not (_text(claim.get("subject")) and claim.get("polarity") in ("exists", "absent", "partial", "unclear")
            and _text(claim.get("claim")) and claim.get("bearing") in EXPLORE_BEARINGS):
        return False
    if claim.get("file") is not None and not isinstance(claim["file"], str):
        return False
    if claim.get("evidence") is not None and not isinstance(claim["evidence"], (str, list)):
        return False
    return claim.get("confidence", "verified") in ("verified", "unverified")


def _checked_ok(checked: object) -> bool:
    return (isinstance(checked, dict) and isinstance(checked.get("toolsUsed"), list)
            and all(isinstance(tool, str) for tool in checked["toolsUsed"])
            and checked.get("stoppedAt") in ("nothing-in-scope", "exhausted-leads", "trigger-not-met", "blocked")
            and isinstance(checked.get("basis"), str))


def _journal_events(raw: bytes) -> tuple[list[dict], dict]:
    """The start row each agent key settles on (its last successful attempt, else its last), and the terminal row
    of every (agentId, key) pair. Raises ValueError on a journal whose events do not pair."""
    attempts: dict[str, list[dict]] = {}
    starts_by_pair: dict[tuple[str, str], dict] = {}
    starts_by_agent: dict[str, tuple[str, str]] = {}
    open_by_key: dict[str, tuple[str, str]] = {}
    terminals: dict[tuple[str, str], dict] = {}
    for line_number, raw_line in enumerate(raw.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed journal JSON at line {line_number}: {exc}") from exc
        if not isinstance(row, dict) or row.get("type") not in ("started", "result", "failed"):
            continue
        kind, agent_id, key = row["type"], row.get("agentId"), row.get("key")
        if not _text(agent_id) or not _text(key):
            raise ValueError(f"malformed {kind} identity at journal line {line_number}")
        pair = (agent_id, key)
        if kind == "started":
            if not _text(row.get("label")) or not _text(row.get("phase")):
                raise ValueError(f"malformed started metadata at journal line {line_number}")
            if pair in starts_by_pair or agent_id in starts_by_agent or key in open_by_key:
                raise ValueError(f"duplicate started event at journal line {line_number}")
            start = dict(row, _line=line_number)
            starts_by_pair[pair], starts_by_agent[agent_id], open_by_key[key] = start, pair, pair
            attempts.setdefault(key, []).append(start)
            continue
        if pair in terminals or pair not in starts_by_pair or open_by_key.get(key) != pair:
            raise ValueError(f"terminal event at journal line {line_number} does not pair with an active start")
        terminals[pair] = dict(row, _line=line_number)
        del open_by_key[key]
    if not attempts:
        raise ValueError("workflow journal has no started agent events")
    starts = []
    for key, tries in attempts.items():
        done = [start for start in tries if terminals.get((start["agentId"], key), {}).get("type") == "result"]
        starts.append(done[-1] if done else tries[-1])
    return starts, terminals


def _lens_status(lenses: list[dict]) -> str:
    seats = [row for row in lenses if row["role"] == "source"]
    unavailable = sum(row["status"] in ("failed", "uncovered") for row in seats)
    if unavailable == len(seats):
        return "failed"
    if unavailable:
        return "uncovered"
    return "partial" if any(row["status"] == "partial" for row in seats) else "completed"


def read_explore_journal(run_dir: Path) -> dict:
    """One explore run: its journal's SHA-256, the coverage of every lens, and the valid claims and gaps.
    Entry ids (C<n>) count every returned entry in journal order, rejected or not, so an id names a place."""
    journal = Path(run_dir) / "journal.jsonl"
    try:
        raw = journal.read_bytes()
    except OSError as exc:
        raise ValueError(f"workflow journal is unavailable: {journal}: {exc}") from exc
    starts, terminals = _journal_events(raw)
    lenses: list[dict] = []
    claims: list[dict] = []
    gaps: list[dict] = []
    entries = 0
    for start in starts:
        terminal = terminals.get((start["agentId"], start["key"]))
        label = start["label"]
        lens = label.split(":", 1)[1] if ":" in label else label
        row = {"agentId": start["agentId"], "key": start["key"], "label": label, "phase": start["phase"],
               "lens": lens, "role": "stage" if start["phase"] == "Verify" else "source", "status": "failed",
               "terminal": "missing", "observed": None, "valid": None, "rejected": 0, "issues": []}
        lenses.append(row)
        if terminal is None:
            row["issues"].append("missing terminal event")
            continue
        row["terminal"] = terminal["type"]
        if terminal["type"] == "failed":
            row["issues"].append("workflow recorded a failed terminal event")
            continue
        value = terminal.get("result")
        if not isinstance(value, dict):
            row.update(status="uncovered", rejected=1)
            row["issues"].append("terminal result is not an object")
            continue
        if row["role"] == "stage":
            row["status"] = "completed"
            continue
        rows, checked, gap_rows = value.get("claims"), value.get("checked"), value.get("gaps", [])
        if not isinstance(rows, list) or not _checked_ok(checked):
            row.update(status="uncovered", rejected=1)
            row["issues"].append("result claims/checked shape is malformed")
            continue
        row["checked"] = checked
        claim_bad = 0
        for index, claim in enumerate(rows):
            entries += 1
            if _claim_ok(claim):
                claims.append({"id": f"C{entries}", "lens": lens, "label": label, "index": index, "claim": claim})
            else:
                claim_bad += 1
        gap_bad = 0
        if not isinstance(gap_rows, list):
            gap_bad = 1
            row["issues"].append("result gaps is not an array")
        else:
            for index, gap in enumerate(gap_rows):
                if _text(gap):
                    gaps.append({"lens": lens, "label": label, "index": index, "gap": gap})
                else:
                    gap_bad += 1
        if claim_bad:
            row["issues"].append("malformed claim entries were rejected")
        if gap_bad and isinstance(gap_rows, list):
            row["issues"].append("malformed gap entries were rejected")
        status = "partial" if claim_bad or gap_bad else "completed"
        if not rows and checked["stoppedAt"] != "trigger-not-met" and (
                checked["stoppedAt"] == "blocked" or not checked["toolsUsed"]):
            status = "uncovered"
            row["issues"].append("empty result has no completed search provenance")
        row.update(observed=len(rows), valid=len(rows) - claim_bad, rejected=claim_bad + gap_bad, status=status)
    return {"contract": WORKFLOW_CONTRACT, "journal_sha256": hashlib.sha256(raw).hexdigest(),
            "status": _lens_status(lenses), "lenses": lenses, "claims": claims, "gaps": gaps}


# ---- Selection and the digest ------------------------------------------------------------------------------


def locate(cwd: str, a: argparse.Namespace) -> Path:
    stores = project_dirs(cwd)
    if a.match_file:
        return match_transcript(stores, Path(a.match_file).read_text(encoding="utf-8", errors="replace"))
    if a.session:
        hits = store_transcripts(stores, a.session, prefix=True)
        if len(hits) == 1:
            return hits[0]
        sys.exit(f"{len(hits)} transcripts match {a.session!r}; pass an exact session id"
                 if hits else f"no transcript matches {a.session!r} under {', '.join(map(str, stores))}")
    return previous_transcript(session_store(cwd))


def digest(path: Path, cwd: str) -> dict:
    """The builder's record of one transcript, with the owner's prompts, the last assistant text and the agent's work."""
    builder = TranscriptSummaryBuilder(path.stem, str(path), full_evidence=True)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            builder.process_line(line)
    d = builder.finalize(backup_limits=False)
    d["user_messages"] = owner_prompts(path)
    d["outcome_last_assistant_text"] = last_assistant_text(path)
    d["agent_work"] = agent_work(path, last_owner_timestamp(d["user_messages"]), cwd)
    return d


def _clip_bytes(text: str, max_bytes: int) -> str:
    """Clip to a UTF-8 byte budget on a character boundary, with the omitted count named."""
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    kept, used = [], 0
    for ch in text:
        size = len(ch.encode("utf-8"))
        if used + size > max_bytes - 32:
            break
        kept.append(ch)
        used += size
    return "".join(kept).rstrip() + f" …[+{len(text) - len(kept)} chars]"


# ---- Rendering ---------------------------------------------------------------------------------------------


def _stamp(row: dict) -> str:
    timestamp = row.get("timestamp") or ""
    return timestamp[11:16] if len(timestamp) >= 16 else "--:--"


def _attribution(row: dict) -> str:
    if row.get("recovered") != "command_arguments":
        return "owner"
    return row.get("issuer") or "unattributed"


def _prompt_line(row: dict, limit: int) -> str:
    signals = ",".join(row.get("signals") or []) or "-"
    return f"- {_stamp(row)} {signals} {_attribution(row)}: {_excerpt(row.get('content') or '', limit)}"


def _friction_line(row: dict, limit: int) -> str:
    kind = "DENIED" if row.get("denied") else "error"
    detail = (f"error: {row.get('error') or '(none)'} | "
              f"next: {row.get('response') or '(no text before the next prompt)'}")
    return f"- {kind} {row.get('tool') or 'unknown'}: {_excerpt(detail, limit)}"


def _is_task_anchor(row: dict) -> bool:
    content = (row.get("content") or "").strip()
    if content.startswith("(command) "):
        content = content[len("(command) "):].lstrip()
    if content.startswith("/") and content.split()[0] in _SUBSTANTIVE_CONTROL_COMMANDS | _TASK_ANCHOR_WRAPPER_COMMANDS:
        return False
    return True


def _task_anchor(rows: list[dict]) -> dict | None:
    owned = [row for row in rows if row.get("recovered") != "command_arguments"]
    return next((row for row in owned if _is_task_anchor(row)), owned[0] if owned else None)


def _priority_rows(rows: list[dict], anchor: dict | None) -> list[dict]:
    """The anchor, every row carrying a signal or an answer, and the last twelve rows, in transcript order."""
    picked = ([anchor] if anchor else []) + [row for row in rows if row.get("signals") or row.get("recovered") == "question_answer"]
    picked += rows[-12:]
    return sorted({row.get("index"): row for row in picked}.values(), key=lambda row: row.get("index", -1))


def _file_rows(d: dict, cwd: str) -> list[tuple[str, int]]:
    files = (d.get("files_modified_counts") or {}).items()
    return sorted(((_aw_rel(path, cwd), count) for path, count in files), key=lambda pair: (-pair[1], pair[0]))


def _head(d: dict, path: Path) -> list[str]:
    meta = d["metadata"]
    msgs = d["user_messages"]
    model = sum(1 for row in msgs if row.get("issuer") == "model")
    recovered = sum(1 for row in msgs if row.get("recovered")) - model
    typed = len(msgs) - recovered - model
    extra = ([f"+{recovered} from command arguments and answers"] if recovered else []) + \
        ([f"+{model} model-issued commands"] if model else [])
    prompts = f"{typed} ({', '.join(extra)})" if extra else str(typed)
    return [f"# Session digest — {str(d.get('session_id') or 'unknown')[:8]}  ({path.name})",
            f"messages {meta['total_messages']} · tool calls {meta['total_tool_calls']} · "
            f"duration {(meta['duration_seconds'] or 0) // 60} min · compactions {d['compactions']['count']} · "
            f"user prompts {prompts} · friction rows {len(d.get('friction') or [])} · "
            f"files modified {len(d.get('files_modified_counts') or {})}"]


def _agent_block(d: dict, outcome: str, limit: int | None) -> list[str]:
    work = d.get("agent_work")
    return [""] + agent_work_lines(work, outcome, limit) if work else []


def _packet(d: dict, path: Path, cwd: str, level: dict) -> str:
    rows = [row for row in d["user_messages"] if is_substantive_owner_prompt(row)]
    anchor = _task_anchor(rows)
    latest = next((row for row in reversed(rows) if row.get("recovered") != "command_arguments"), None)
    latest_cmd = next((row for row in reversed(rows) if row.get("recovered") == "command_arguments"), None)
    outcome = d["outcome_last_assistant_text"]
    scale = level["clip"]
    timeline = rows if level["timeline"] == "all" else _priority_rows(rows, anchor)
    friction = list(d.get("friction") or [])
    shown_friction = friction[-level["friction"]:] if level["friction"] else []
    files = _file_rows(d, cwd)
    shown_files = files[:level["files"]]
    row_clip, anchor_clip = int(500 * scale), int(500 * scale)
    out = _head(d, path)
    out += ["", "## Task anchor", _prompt_line(anchor, anchor_clip) if anchor else "(none recorded)",
            "", "## Latest owner input/directive", _prompt_line(latest, row_clip) if latest else "(none recorded)"]
    if latest_cmd is not None:
        out += ["", f"## Latest command request ({_attribution(latest_cmd)})", _prompt_line(latest_cmd, row_clip)]
    out += ["", "## Status evidence — last substantive assistant text (not a completion verdict)",
            _excerpt(outcome, int(1800 * scale)) or "(no assistant text)"]
    out += _agent_block(d, outcome, level["agent"])
    out += ["", f"## Prompt timeline (shown/total: {len(timeline)}/{len(rows)})"]
    out += [_prompt_line(row, row_clip) for row in timeline] or ["(none recorded)"]
    out += ["", f"## Recent friction (shown/total: {len(shown_friction)}/{len(friction)})"]
    out += [_friction_line(row, int(260 * scale)) for row in shown_friction] or ["(none shown)"]
    out += ["", f"## Files modified (shown/total: {len(shown_files)}/{len(files)})"]
    out += [f"- {name} ×{count}" for name, count in shown_files] or ["(none shown)"]
    if len(shown_friction) < len(friction) or len(shown_files) < len(files):
        out.append("Optional rows omitted; `--full` writes every friction row and file.")
    return "\n".join(out)


def render_packet(d: dict, path: Path, cwd: str) -> str:
    """The first level of LEVELS whose packet fits HANDOFF_MAX_BYTES; the last level, byte-clipped, when none does."""
    for level in LEVELS:
        text = _packet(d, path, cwd, level)
        if len(text.encode("utf-8")) <= HANDOFF_MAX_BYTES:
            return text
    return _clip_bytes(text, HANDOFF_MAX_BYTES)


def render_full(d: dict, path: Path, cwd: str) -> str:
    """Every prompt, friction row, file and the agent's work, uncapped. Read it with the editor, not the console."""
    prompts, friction = d["user_messages"], d.get("friction") or []
    files = _file_rows(d, cwd)
    outcome = d["outcome_last_assistant_text"]
    out = _head(d, path) + ["", f"## User prompts ({len(prompts)})"]
    out += [_prompt_line(row, 0) for row in prompts] or ["(none)"]
    out += ["", f"## Friction ({len(friction)})"]
    out += [_friction_line(row, 0) for row in friction] or ["(none)"]
    out += ["", f"## Files modified ({len(files)})"]
    out += [f"- {name} ×{count}" for name, count in files] or ["(none)"]
    out += _agent_block(d, outcome, None)
    out += ["", "## Status evidence — last substantive assistant text (not a completion verdict)",
            outcome or "(no assistant text)"]
    return "\n".join(out)


def write_text_atomic(path: Path, text: str) -> None:
    """Publish a UTF-8 file without exposing a partial target."""
    fd, temp_name = tempfile.mkstemp(prefix=".session-digest-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        replace_path(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def main() -> None:
    ap = argparse.ArgumentParser(description=cli_usage.usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    pick = ap.add_mutually_exclusive_group()
    pick.add_argument("--session", metavar="ID", help="session id or unique prefix")
    pick.add_argument("--previous", action="store_true",
                      help="the transcript written just before the active session's (the default)")
    pick.add_argument("--match-file", metavar="PATH",
                      help="the session whose assistant wrote the text in PATH (a pasted message)")
    ap.add_argument("--list", type=int, metavar="N", help="list the newest N transcripts and exit")
    ap.add_argument("--full", action="store_true", help="write the whole session as Markdown; print its path only")
    ap.add_argument("--workflow-dir", metavar="DIR",
                    help="print one explore Workflow's journal as JSON (the run's transcriptDir)")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    if a.workflow_dir:
        if a.session or a.previous or a.match_file or a.list is not None or a.full:
            ap.error("--workflow-dir reads a journal alone")
        try:
            value = read_explore_journal(Path(a.workflow_dir))
        except ValueError as exc:
            sys.exit(f"session_digest: {exc}")
        print(json.dumps(value, indent=1, ensure_ascii=True))
        return
    cwd = os.getcwd()
    if a.list is not None:
        print(list_sessions(session_store(cwd), a.list))
        return
    path = locate(cwd, a)
    d = digest(path, cwd)
    if a.full:
        export = Path(cwd) / "logs" / f"session_digest_{path.stem[:8]}.full.md"
        export.parent.mkdir(exist_ok=True)
        write_text_atomic(export, render_full(d, path, cwd) + "\n")
        print(export)
        return
    sys.stdout.write(render_packet(d, path, cwd) + "\n")


if __name__ == "__main__":
    main()
