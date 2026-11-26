#!/usr/bin/env python3
"""Rebuild and present one session from its append-only live transcript.

Presentation modes are deliberately separate:
  --brief is a bounded identity card (2,048 UTF-8 bytes) for locating a session. It is not a
      resume packet.
  --handoff, and the no-mode default, are a bounded resume packet (16,384 UTF-8 bytes) that keeps
      the task anchor, priority corrections/answers, recent owner input, the agent's own work
      (commits, pushes, dispatches and returns, open jobs, state files; `agent_work`), friction and files.
      16,384 stays the bound: a packet is read once. The agent-work section sheds its lists
      (6, 3, 1, 0 rows) after files, friction and the timeline, so its counts always survive.
      A handoff run also adopts the running sidecars of a session with no live host (`adoption_note`).
  --full is an offline human-readable Markdown projection. It writes atomically to
      logs/session_digest_<sid8>.full.md and prints only a bounded receipt. It is not raw JSON or
      transcript parity; use --select/--evidence-page for exact machine evidence.

All modes preserve the full JSON and evidence index before presentation. Workflow --workflow-full
is a separate, hash-checked result operation and cannot be mixed with transcript presentation. It
prints the run's full processed result from the local replay (tools/workflow_replay.mjs, `source`
"replay"); when the replay cannot reproduce the run it prints the archive pages instead (`source`
"archive") with `replayGap` naming why, since the pages hold raw sources but no engine-derived field.
"""

import sys as _cli_usage_sys
from pathlib import Path as _cli_usage_path
_cli_usage_sys.path.insert(0, str(_cli_usage_path(__file__).resolve().parent))
from cli_usage import usage
from model_registry import session_project_roots, session_transcript_hits
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "hooks"))
from _checkout_root import harness_root  # noqa: E402
from _git_run import run as git_run  # noqa: E402
from _hook_state import by_mtime, replace_path, stat_present  # noqa: E402
from _transcript_summary import TranscriptSummaryBuilder, owner_line  # noqa: E402
from _owner_text import classify, meta_arguments  # noqa: E402
import _owner_text  # noqa: E402
import _transcript_scan  # noqa: E402
import _transcript_summary  # noqa: E402
import review_result  # noqa: E402  -- the one `path:line` rule for a journal finding
import _background_tasks  # noqa: E402  -- the one `<task-notification>` shape
import dispatch_result  # noqa: E402  -- the one `<label>.md` deliverable path

BRIEF_MAX_BYTES = 2_048
HANDOFF_MAX_BYTES = 16_384
FULL_RECEIPT_MAX_BYTES = 2_048
BRIEF = {"mode": "brief"}
HANDOFF = {"mode": "handoff"}
SESSION = {"mode": "handoff"}
FULL = {"mode": "full"}

_SUBSTANTIVE_CONTROL_COMMANDS = frozenset({
    "/model", "/effort", "/fast", "/compact", "/context", "/clear", "/cost",
    "/status", "/config", "/help", "/resume", "/exit",
})
_SUBSTANTIVE_ROUTING_COMMANDS = frozenset({"/effort"})
_TASK_ANCHOR_WRAPPER_COMMANDS = frozenset({
    "/overnight", "/session_end", "/commit_push", "/clean_push", "/create_pr", "/pr_ready",
})
_SUBSTANTIVE_SLASH_RE = re.compile(r"/[\w:.-]+$")


def is_substantive_owner_prompt(message: dict) -> bool:
    """Return the shared owner-row policy used by digest selection and compact recovery.

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
    """The transcript folder for `cwd` under the session's config dir (`CLAUDE_CONFIG_DIR`, a second
    account's store; default `~/.claude`)."""
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    root = Path(os.path.expanduser(config)) if config else Path.home() / ".claude"
    return root / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)


def project_dirs(cwd: str) -> list[Path]:
    """Transcript folders for every checkout, grouped by login with the current login first.

    Within each login, `cwd` leads the main checkout and linked worktrees. Constructing these paths
    does not inspect another login's store; identity callers stop at their first matching login.
    A directory outside git contributes only its own checkout slug per login."""
    roots = [cwd]
    try:
        argv = ["worktree", "list", "--porcelain"]
        listing = git_run(argv, cwd=cwd, timeout=15).check(argv).text
        roots += [line[len("worktree "):].strip() for line in listing.splitlines() if line.startswith("worktree ")]
    except (OSError, subprocess.SubprocessError):
        pass
    stores = list(dict.fromkeys(projects_dir(root) for root in roots))
    login_roots = iter(session_project_roots())
    next(login_roots)
    names = list(dict.fromkeys(store.name for store in stores))
    return list(dict.fromkeys(stores + [Path(root) / name for root in login_roots for name in names]))


def sibling_stores(pdir: Path, cwd: str | None = None) -> list[Path]:
    """`pdir` and, when it is a store of the repo at `cwd` (default the harness root), the stores of that
    repo's other checkouts. A store of any other project stands alone."""
    stores = project_dirs(cwd or harness_root())
    return [pdir] + [store for store in stores if store != pdir] if pdir in stores else [pdir]


def searched_stores(stores: list[Path], logins: bool = False) -> list[Path]:
    """`stores` (the checkouts of one repository), then with `logins` the same project folders under each
    other login `session_project_roots` declares."""
    if not logins:
        return list(stores)
    names = list(dict.fromkeys(store.name for store in stores))
    return list(dict.fromkeys(list(stores) + [Path(root) / name for root in session_project_roots()
                                              for name in names]))


def store_transcripts(stores: list[Path], session: str, prefix: bool = False, logins: bool = False) -> list[Path]:
    """`model_registry.session_transcript_hits` over the roots holding `searched_stores(stores, logins)`,
    keeping only hits inside those stores: a session of another project is not this repository's. The login
    of `stores[0]` answers first and alone; the other logins are one pool, so a match in two of them is
    ambiguous. One path is the session's, two or more are ambiguous."""
    allowed = searched_stores(stores, logins)
    roots = list(dict.fromkeys(str(store.parent) for store in allowed))
    for roots in (roots[:1], roots[1:]):
        hits = sorted({path for root in roots for path in session_transcript_hits(session, [root], prefix)
                       if path.parent in allowed})
        if hits:
            return hits
    return []


def session_store(cwd: str, session: str | None = None) -> Path:
    """The folder holding `session` (default: this session, `CLAUDE_CODE_SESSION_ID`) among the stores
    of `cwd`'s checkouts; `cwd`'s own when no store holds it or two do."""
    session = session or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    stores = project_dirs(cwd)
    hits = store_transcripts(stores, session) if session else []
    return hits[0].parent if len(hits) == 1 else stores[0]


def _rows(path: Path, kind: str, include_sidechain: bool = False):
    """Parse complete JSON rows before filtering by type; JSON whitespace is not identity.

    Sidechain rows are a subagent's turns. Excluded by default, because in a SESSION transcript
    they are delegated work the session digest should not read as the session's own. Pass
    `include_sidechain=True` when the file IS a subagent transcript — every row there is a
    sidechain, so the default yields nothing and the file reads as empty rather than as a subagent.
    """
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            row = _message_row(line, kind, include_sidechain)
            if row is not None:
                yield row


def _message_row(line: str, kind: str, include_sidechain: bool = False):
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict) or row.get("type") != kind or (row.get("isSidechain") and not include_sidechain):
        return None
    if not isinstance(row.get("message"), dict):
        row = dict(row, message={"content": row.get("content", "")})
    return row


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


def _digest_dict(path: Path) -> dict:
    """The full builder dictionary for one transcript (`full_evidence`, so no collection is cut to
    a preview). The digest header reads it."""
    b = TranscriptSummaryBuilder(path.stem, str(path), full_evidence=True)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            b.process_line(line)
    return b.finalize(backup_limits=False)


def _owner_feed(state: dict, index: int, line: str) -> None:
    found = owner_line(line, index, full_evidence=True)
    if found is not None and found[0] != "compaction":
        state["messages"].append(found[1])
    state["meta"] += _meta_command_rows(line, index)
    state["answers"] += _answer_rows(line, index, state["asked"])


OWNER_SCAN = _transcript_scan.Scanner(
    "owner_prompts", lambda: {"messages": [], "meta": [], "answers": [], "asked": {}}, _owner_feed,
    sources=[__file__, _transcript_summary.__file__, _owner_text.__file__])


def owner_prompts(path: Path, *, on_assistant=None) -> list[dict]:
    """Every owner message in transcript order: typed prompts, command arguments and question
    answers. The one definition this digest and hooks/compact_directive_anchor.py both read. One pass
    folds the builder's user messages and both recover passes, resumed from the transcript scan cache.
    A caller that also needs assistant rows can receive (line index, row) in the same cold pass.
    """
    if on_assistant is None:
        state = _transcript_scan.scan(path, OWNER_SCAN)
    else:
        state = OWNER_SCAN.initial()
        with open(path, encoding="utf-8") as fh:
            for index, line in enumerate(fh, 1):
                OWNER_SCAN.feed(state, index, line)
                row = _message_row(line, "assistant")
                if row is not None:
                    on_assistant(index, row)
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
AW_KEEP = {"commits": 40, "dispatches": 200, "detached": 100, "shells": 100, "files": 60, "pushes": 20}  # rows kept in the saved JSON; the packet shows fewer
AW_ITEM_CLIP = 170  # one packet line stays one screen line
AW_STATED_MAX = 8  # the not-done and decision lines kept from the agent's last message

_AW_PUSH_RE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?(?:\s+-c\s+\S+)*\s+push\b")
_AW_COMMIT_RE = re.compile(r"\bgit\b(?:\s+-C\s+\S+)?(?:\s+-c\s+\S+)*\s+commit\b")
_AW_REF_UPDATE_RE = re.compile(r"[0-9a-f]{7,40}\.\.\.?[0-9a-f]{7,40}\s+\S+\s+->\s+\S+|\[new branch\]\s+\S+\s+->\s+\S+")
_AW_FANOUT_JOB_RE = re.compile(r"^\[fanout\] (?P<label>\S+): admitted on (?P<plan>\S+) \((?P<model>[^,)]+), (?P<effort>[^)]+)\)",
                            re.MULTILINE)
_AW_FANOUT_DIR_RE = re.compile(r"summary goes to (?P<dir>\S+?)/fanout-driver\.log")
_AW_DETACHED_RE = re.compile(r"^DETACHED pid=\d+", re.MULTILINE)
_AW_SIDECAR_PATH_RE = re.compile(r"^(?P<key>OUT|EXIT)=(?P<path>\S+)", re.MULTILINE)
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
    done = git_run(args, cwd=cwd, timeout=AW_GIT_TIMEOUT_S)
    err = done.err_text
    if done.returncode != 0:
        raise OSError(err.strip().splitlines()[0] if err.strip() else "git failed")
    return done.text


def _aw_patch_ids(cwd, window):
    """Full commit hash -> patch-id for the window's commits on every local branch. A cherry-pick shares its
    source's patch-id; a commit with no diff has none."""
    diff = _aw_git(cwd, "log", "--branches", *window, "-p", "--no-color", "--no-decorate")
    done = git_run(["patch-id", "--stable"], cwd=cwd, input=diff.encode("utf-8"), timeout=AW_GIT_TIMEOUT_S)
    if done.returncode != 0:
        raise OSError(done.err_text.strip().splitlines()[0])
    return {parts[1]: parts[0] for parts in
            (line.split() for line in done.text.splitlines()) if len(parts) == 2}


def _aw_one_per_change(rows, ids, on_head):
    """One row per change, newest first. A change on several branches keeps the copy `HEAD` holds, else the newest."""
    best = {}
    for row in rows:  # git log order: newest first
        key = ids.get(row["full"]) or row["full"]
        kept = best.get(key)
        if kept is None or (row["full"] in on_head and kept["full"] not in on_head):
            best[key] = row
    return sorted(best.values(), key=lambda r: r["time"], reverse=True)


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
        on_head = set(_aw_git(cwd, "log", "HEAD", *window, "--format=%H").split()) if since else set()
        ids = _aw_patch_ids(cwd, window) if since else {}
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
    commits = _aw_one_per_change(rows, ids, on_head)
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
            "commit_calls": 0, "push_calls": 0, "pushes": [], "dispatches": [], "detached": [], "shells": [], "files": []}
    cwd = last_cwd if last_cwd and os.path.isdir(last_cwd) else (fallback_cwd or "")
    files = {}
    for entry in uses.values():
        if entry.get("error") or "result" not in entry:
            continue
        _aw_classify(entry, work, files, ended, cwd)
    work["files"] = sorted(files.values(), key=lambda f: f["last_ts"] or "", reverse=True)[:AW_KEEP["files"]]
    for key in ("pushes", "dispatches", "detached", "shells"):
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
    jobs = list(_AW_FANOUT_JOB_RE.finditer(result))
    if jobs:
        out_dir = _AW_FANOUT_DIR_RE.search(result)
        for job in jobs:
            deliverable = str(dispatch_result.deliverable_path(out_dir.group("dir"), job.group("label"))) if out_dir else None
            info = _aw_stat(deliverable, cwd) if deliverable else None
            launched = _aw_epoch(ts)
            returned = bool(info and (launched is None or info["mtime"] >= launched - 1))
            work["dispatches"].append({
                "kind": "fanout", "label": job.group("label"), "model": job.group("model"),
                "effort": job.group("effort").strip(), "time": ts, "deliverable": deliverable,
                "return": {"how": "deliverable", "path": deliverable, "bytes": info["bytes"], "mtime": info["mtime"]}
                if returned else None})
    elif _AW_DETACHED_RE.search(result):
        paths = {m.group("key"): m.group("path") for m in _AW_SIDECAR_PATH_RE.finditer(result)}
        out = paths.get("OUT", "")
        if not (out.endswith(".record.json.out") and "EXIT" in paths):
            info = _aw_stat(out, cwd) if out else None
            work["detached"].append({
                "label": os.path.splitext(os.path.basename(out))[0] or "detached", "time": ts, "out": out,
                "bytes": info["bytes"] if info else None,
                "mtime": datetime.fromtimestamp(info["mtime"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if info else None})
            return
        label = re.sub(r"\.record\.json\.out$", "", os.path.basename(out))
        exit_info = _aw_stat(paths["EXIT"], cwd) if "EXIT" in paths else None
        work["dispatches"].append({
            "kind": "sidecar", "label": label, "model": None, "effort": None, "time": ts,
            "deliverable": paths.get("EXIT"),
            "return": {"how": "exit file", "path": paths["EXIT"], "bytes": exit_info["bytes"], "mtime": exit_info["mtime"]}
            if exit_info else None})
    if inputs.get("run_in_background") and "watch_records" not in command and entry.get("task_id"):
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
                tail = {"fanout": "no deliverable on disk", "sidecar": "no exit file"}.get(d["kind"], "no end notice")
            elif ret["how"] == "notice":
                tail = f"returned: {ret['status']} notice {_aw_hhmm(ret['time'])}"
            else:
                tail = f"returned: {_aw_rel(str(ret['path']), win.get('cwd'))} {_aw_size(ret['bytes'])}"
            out.append(f"- {d['label']}{pin} {d['kind']} {_aw_hhmm(d['time'])} → {tail}")
    detached = work.get("detached") or []
    if detached:
        out.append(f"Detached jobs — {len(detached)} launched; each one's output file, size and last write "
                   f"(its own end marker, if any, is inside the output):")
        out += [f"- {d['label']} · {_aw_rel(d['out'], win.get('cwd'))} · "
                + (f"{_aw_size(d['bytes'])}, last write {_aw_hhmm(d['mtime'])}" if d["bytes"] is not None else "no output file on disk")
                for d in newest(detached)]
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


_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{4,}")
MATCH_MIN = 0.6
MATCH_SCAN = 40


def _tokens(text: str) -> set[str]:
    """Distinctive words (5+ chars, case-folded). Markdown, punctuation and table pipes fall away,
    which is what survives the terminal re-rendering a pasted message."""
    return {w.lower() for w in _WORD.findall(text or "")}


def assistant_blocks(path: Path, min_len: int = 200):
    for row in _rows(path, "assistant"):
        content = (row.get("message") or {}).get("content") or []
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = (block.get("text") or "").strip()
                if len(t) >= min_len:
                    yield t


def match_transcript(pdir: Path, text: str, scan: int = MATCH_SCAN) -> tuple[Path, list[tuple[float, Path]]]:
    """The transcript whose assistant wrote `text` (a pasted closing message): best single assistant
    block by token overlap across the newest `scan` transcripts. Assistant blocks only — the session
    that RECEIVED the paste holds it in a user row and must not match itself."""
    want = _tokens(text)
    if len(want) < 8:
        sys.exit(f"match text has {len(want)} distinctive words; paste at least a paragraph")
    files = by_mtime(pdir.glob("*.jsonl"), newest_first=True)[:scan]
    scored = []
    for p in files:
        best = 0.0
        for block in assistant_blocks(p):
            score = len(want & _tokens(block)) / len(want)
            if score > best:
                best = score
        scored.append((best, p))
    scored.sort(key=lambda s: -s[0])
    if not scored or scored[0][0] < MATCH_MIN:
        ranked = "\n".join(f"  {p.stem[:8]}  {s:.2f}" for s, p in scored[:3])
        sys.exit(f"no assistant message overlaps the paste at >= {MATCH_MIN:.0%} of its words "
                 f"(newest {len(scored)} transcripts); nearest:\n{ranked}\npass --session <id>")
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        sys.exit('ambiguous assistant-message match; pass an exact --session <id>')
    return scored[0][1], scored[:3]


def list_sessions(pdir: Path, n: int) -> str:
    """The newest N transcripts, one line each: prefix, last write, size, first real prompt. The
    listing order is modification time, not session ownership. --previous anchors to active identity."""
    import datetime
    files = sorted(stat_present(pdir.glob("*.jsonl")), key=lambda entry: entry[1].st_mtime, reverse=True)[:n]
    out = []
    for p, st in files:
        when = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")
        first = ""
        for row in _rows(p, "user"):
            c = (row.get("message") or {}).get("content") or []
            text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict))
            if text and not text.lstrip().startswith("<"):
                first = clip(" ".join(text.split()), 110)
                break
        tag = "  (active session)" if p.stem == os.environ.get("CLAUDE_CODE_SESSION_ID") else ""
        out.append(f"{p.stem[:8]}  {when}  {st.st_size // 1024:>6} KB  {first}{tag}")
    return "\n".join(out)


def _session_hits(files, session):
    exact = [path for path in files if path.stem == session]
    return exact or [path for path in files if path.stem.startswith(session)]


def pick_transcript(pdir: Path, session: str | None, prompt_tail: str | None, previous: bool = False,
                    cwd: str | None = None) -> Path:
    """Use exact ownership; discovery modes never silently resolve ambiguous matches. An explicit
    `session` with no match in `pdir` resolves in the same store of another login, or in the store of
    another checkout of the repo `pdir` belongs to (found from `cwd`, default the process cwd), when
    exactly one transcript there matches."""
    files = by_mtime(pdir.glob('*.jsonl'), newest_first=True)
    if not files and not session:
        sys.exit(f'no transcripts under {pdir}')
    if session:
        hits = _session_hits(files, session)
        if hits:
            if len(hits) != 1:
                sys.exit(f'{len(hits)} transcripts match {session!r}; pass an exact session id')
            return hits[0]
        stores = sibling_stores(pdir, cwd)
        hits = store_transcripts(stores, session, prefix=True, logins=True)
        if len(hits) == 1:
            return hits[0]
        searched = ", ".join(map(str, searched_stores(stores, logins=True)))
        sys.exit(f'{len(hits)} transcripts match {session!r} under {searched}; pass an exact session id')
    if previous:
        active = (os.environ.get('CLAUDE_CODE_SESSION_ID') or '').strip()
        anchor = next((path for path in files if path.stem == active), None)
        if anchor is None:
            sys.exit('active session is missing or unknown; pass --session <id>')
        for path in files[files.index(anchor) + 1:]:
            if last_prompt(path):
                return path
        sys.exit('no previous transcript with a user prompt; pass --session <id>')
    env_sid = (os.environ.get('CLAUDE_CODE_SESSION_ID') or '').strip()
    if env_sid:
        mine = [path for path in files if path.stem == env_sid]
        if len(mine) == 1:
            return mine[0]
        sys.exit(f'active session {env_sid!r} is missing under {pdir}; refusing peer fallback')
    if prompt_tail:
        hits = [path for path in files if prompt_tail in last_prompt(path)]
        if len(hits) == 1:
            return hits[0]
        sys.exit(f'{len(hits)} transcripts match the prompt selector; pass --session <id>')
    sys.exit('session identity is unknown; pass --session <id>')


def _tool_counts(path: Path, *, include_sidechain: bool = False) -> dict:
    """{tool name: calls} over one transcript's assistant tool_use blocks."""
    counts: dict = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "tool_use" not in line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (not isinstance(row, dict) or row.get("type") != "assistant" or row.get('isMeta')
                    or (row.get('isSidechain') and not include_sidechain)):
                continue
            message = row.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name") or "?"
                    counts[name] = counts.get(name, 0) + 1
    return counts


def tool_census(path: Path) -> dict:
    """Every tool call the session made, by name, split by WHO made it: the session model (main
    transcript) and its Workflow/Agent subagents (`<pdir>/<sid>/subagents/**/*.jsonl`).

    The main transcript alone under-counts: a session whose lenses did the reading shows zero
    worker calls at the top level while its subagents made a dozen. Sidecar children run outside
    this directory and are not counted here — their record is the `-R` file each dispatch wrote.
    """
    main = _tool_counts(path)
    sub: dict = {}
    sub_dir = path.parent / path.stem / "subagents"
    files = sorted(sub_dir.rglob("*.jsonl")) if sub_dir.is_dir() else []
    n = 0
    for p in files:
        c = _tool_counts(p, include_sidechain=True)
        if not c:
            continue  # workflow journals carry no tool_use rows
        n += 1
        for k, v in c.items():
            sub[k] = sub.get(k, 0) + v
    return {"main": main, "subagents": sub, "subagent_transcripts": n,
            "main_total": sum(main.values()), "subagent_total": sum(sub.values())}


def render_tools(census: dict) -> list:
    names = sorted(set(census["main"]) | set(census["subagents"]),
                   key=lambda k: -(census["main"].get(k, 0) + census["subagents"].get(k, 0)))
    out = ["", f"## Tool calls — main {census['main_total']} · subagents {census['subagent_total']} "
               f"in {census['subagent_transcripts']} transcript(s) (sidecar children not included)",
           "| tool | main | subagents |", "|---|---|---|"]
    out += [f"| {k} | {census['main'].get(k, 0)} | {census['subagents'].get(k, 0)} |" for k in names]
    return out


def clip(text: str, n: int) -> str:
    text = text or ""
    if n and len(text) > n:
        return text[:n].rstrip() + f" …[+{len(text) - n} chars]"
    return text


def _evidence_id(prefix: str, row: dict) -> str:
    return prefix + str(row.get("index"))


def build_evidence_index(d: dict) -> dict:
    """Compact, complete lookup index for the full durable digest."""
    prompts = []
    for row in d.get("user_messages") or []:
        content = row.get("content") or ""
        prompts.append({
            "id": _evidence_id("U", row), "timestamp": row.get("timestamp"),
            "signals": row.get("signals") or [], "chars": len(content),
            "excerpt": clip(" ".join(content.split()), 160),
            "attribution": _mode_row_attribution(row),
        })
    friction = []
    for row in d.get("friction") or []:
        error = row.get("error") or ""
        friction.append({
            "id": _evidence_id("F", row), "timestamp": row.get("timestamp"),
            "tool": row.get("tool") or "unknown", "denied": bool(row.get("denied")),
            "has_response": bool(row.get("response")), "error_chars": len(error),
            "excerpt": clip(" ".join(error.split()), 160),
        })
    files = sorted((d.get("files_modified_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
    return {"schema_version": "1.0", "session_id": d.get("session_id"),
            "prompts": prompts, "friction": friction,
            "files": [{"name": p, "edits": c} for p, c in files]}


def select_evidence(d: dict, ids: list[str]) -> list[dict]:
    """Return exact full rows by stable source-line ID; unknown IDs fail loudly. A friction ID
    selects every row on its line: parallel tool calls answer on one transcript line."""
    available = {}
    for kind, prefix, key in (("prompt", "U", "user_messages"), ("friction", "F", "friction")):
        for row in d.get(key) or []:
            evidence_id = _evidence_id(prefix, row)
            if evidence_id in available and kind == "prompt":
                raise ValueError(f"duplicate evidence id: {evidence_id}")
            available.setdefault(evidence_id, []).append({"id": evidence_id, "kind": kind, "row": row})
    missing = [evidence_id for evidence_id in ids if evidence_id not in available]
    if missing:
        raise ValueError("unknown evidence id(s): " + ", ".join(missing))
    return [entry for evidence_id in ids for entry in available[evidence_id]]


def evidence_page(index: dict, kind: str, page: int, page_size: int) -> dict:
    """Return one bounded page from the compact evidence index."""
    if kind not in ("prompts", "friction", "files"):
        raise ValueError("evidence kind must be prompts, friction or files")
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if not isinstance(page_size, int) or not 1 <= page_size <= 50:
        raise ValueError("page size must be between 1 and 50")
    rows = index.get(kind) or []
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    if page > pages:
        raise ValueError(f"page {page} exceeds {pages}")
    start = (page - 1) * page_size
    return {"kind": kind, "page": page, "pages": pages, "total": len(rows),
            "rows": rows[start:start + page_size]}


WORKFLOW_PAGE_KINDS = ("items", "lenses", "reports", "gaps", "merges")
WORKFLOW_RESULT_CONTRACT = "native-workflow-journal/v1"


def _workflow_record(value: object) -> bool:
    return isinstance(value, dict)


def _workflow_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_explore_claim(value: object) -> bool:
    if not _workflow_record(value):
        return False
    if not (_workflow_string(value.get("subject"))
            and value.get("polarity") in ("exists", "absent", "partial", "unclear")
            and _workflow_string(value.get("claim"))
            and value.get("bearing") in ("premise-contradiction", "reuse-candidate", "constraint",
                                          "blast-radius", "context")):
        return False
    if "file" in value and value["file"] is not None and not isinstance(value["file"], str):
        return False
    if "evidence" in value and value["evidence"] is not None and not isinstance(value["evidence"], (str, list)):
        return False
    return "confidence" not in value or value["confidence"] in ("verified", "unverified")


def _valid_explore_checked(value: object) -> bool:
    return (_workflow_record(value)
            and isinstance(value.get("toolsUsed"), list)
            and all(isinstance(item, str) for item in value["toolsUsed"])
            and value.get("stoppedAt") in ("nothing-in-scope", "exhausted-leads",
                                            "trigger-not-met", "blocked")
            and isinstance(value.get("basis"), str))


def _workflow_lens(label: str) -> str:
    return label.split(":", 1)[1] if ":" in label else label


def _workflow_status(lenses: list[dict]) -> str:
    seats = [row for row in lenses if row["role"] == "source"]
    unavailable = sum(row["status"] in ("failed", "uncovered") for row in seats)
    if unavailable == len(seats):
        return "failed"
    if unavailable:
        return "uncovered"
    if any(row["status"] == "partial" for row in seats):
        return "partial"
    return "completed"


def workflow_run_record(run_dir: Path) -> dict | None:
    """The host's run record for a Workflow transcript directory (`<session>/workflows/<run>.json`), else None."""
    try:
        record = json.loads((Path(run_dir).parents[2] / "workflows" / f"{Path(run_dir).name}.json")
                            .read_text(encoding="utf-8"))
    except (OSError, ValueError, IndexError):
        return None
    return record if isinstance(record, dict) else None


def _review_start_order(starts: list[dict], run_dir: Path, seat_order=None) -> tuple[list[dict], str]:
    """Review sources in the engine's `args.agents` order: the run record's, else `seat_order` (the
    launched seats the engine hands its in-run check, which runs before the record exists)."""
    source_slots = [index for index, row in enumerate(starts) if row["phase"] == "Review"]
    if not source_slots:
        return starts, "start-order"
    if len(source_slots) == 1:
        return starts, "engine"
    try:
        agents = workflow_run_record(run_dir)["args"]["agents"]
        keys = [row["key"] for row in agents]
    except (KeyError, TypeError):
        if not seat_order:
            return starts, "start-order"
        keys = list(seat_order)
    if (not all(_workflow_string(key) for key in keys) or len(keys) != len(set(keys))
            or len(keys) != len(source_slots)):
        return starts, "start-order"
    by_label = {starts[index]["label"]: starts[index] for index in source_slots}
    ordered = [by_label.get(f"review:{key}") for key in keys]
    if any(row is None for row in ordered) or len(by_label) != len(source_slots):
        return starts, "start-order"
    result = list(starts)
    for index, row in zip(source_slots, ordered):
        result[index] = row
    return result, "engine"


def _review_candidate_ids(items: list[dict]) -> set[str]:
    seats_by_file: dict[str, set[str]] = {}
    for item in items:
        finding = item["finding"]
        file_value = finding.get("file")
        if not isinstance(file_value, str):
            continue
        path = re.sub(r":[0-9]+$", "", file_value.strip())
        seats_by_file.setdefault(path, set()).add(item["label"])
    shared_files = {path for path, seats in seats_by_file.items() if len(seats) >= 2}
    return {
        item["id"] for item in items
        if isinstance(item["finding"].get("file"), str)
        and re.sub(r":[0-9]+$", "", item["finding"]["file"].strip()) in shared_files
    }


def build_workflow_result_archive(workflow_dir: Path, workflow_kind: str, *, root=None, verify=True, seat_order=None) -> dict:
    """Validate one exact Workflow journal and index its native result bodies."""
    if workflow_kind not in ("review", "explore"):
        raise ValueError("workflow kind must be review or explore")
    run_dir = Path(workflow_dir)
    journal_path = run_dir / "journal.jsonl"
    try:
        raw = journal_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"workflow journal is unavailable: {journal_path}: {exc}") from exc

    rows: list[tuple[int, dict]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"malformed journal JSON at line {line_number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"malformed journal row at line {line_number}: expected an object")
        rows.append((line_number, row))

    attempts_by_key: dict[str, list[dict]] = {}
    key_order: list[str] = []
    starts_by_pair: dict[tuple[str, str], dict] = {}
    starts_by_agent: dict[str, tuple[str, str]] = {}
    open_by_key: dict[str, tuple[str, str]] = {}
    terminals: dict[tuple[str, str], dict] = {}

    for line_number, row in rows:
        event_type = row.get("type")
        if event_type not in ("started", "result", "failed"):
            continue
        agent_id = row.get("agentId")
        key = row.get("key")
        if not _workflow_string(agent_id) or not _workflow_string(key):
            raise ValueError(f"malformed {event_type} identity at journal line {line_number}")
        pair = (agent_id, key)
        if event_type == "started":
            if not _workflow_string(row.get("label")) or not _workflow_string(row.get("phase")):
                raise ValueError(f"malformed started metadata at journal line {line_number}")
            if pair in starts_by_pair or agent_id in starts_by_agent or key in open_by_key:
                raise ValueError(f"duplicate started event at journal line {line_number}")
            start = dict(row, _line=line_number)
            starts_by_pair[pair] = start
            starts_by_agent[agent_id] = pair
            open_by_key[key] = pair
            if key not in attempts_by_key:
                attempts_by_key[key] = []
                key_order.append(key)
            attempts_by_key[key].append(start)
            continue
        if pair in terminals:
            raise ValueError(f"duplicate terminal event at journal line {line_number}")
        if pair not in starts_by_pair:
            if agent_id in starts_by_agent:
                expected = starts_by_agent[agent_id][1]
                raise ValueError(f"terminal key mismatch for agentId {agent_id!r}: {key!r} != {expected!r}")
            if key in attempts_by_key:
                expected = attempts_by_key[key][-1]["agentId"]
                raise ValueError(f"terminal agentId mismatch for key {key!r}: {agent_id!r} != {expected!r}")
            raise ValueError(f"orphan terminal event at journal line {line_number}")
        if open_by_key.get(key) != pair:
            raise ValueError(f"terminal does not match the active attempt at journal line {line_number}")
        terminals[pair] = dict(row, _line=line_number)
        del open_by_key[key]

    if not attempts_by_key:
        raise ValueError("workflow journal has no started agent events")

    starts: list[dict] = []
    for key in key_order:
        attempts = attempts_by_key[key]
        successful = [start for start in attempts
                      if (terminals.get((start["agentId"], key)) or {}).get("type") == "result"]
        starts.append(successful[-1] if successful else attempts[-1])

    id_order = "start-order"
    if workflow_kind == "review":
        starts, id_order = _review_start_order(starts, run_dir, seat_order)

    items: list[dict] = []
    entry_count = 0  # every returned entry takes an id by place, rejected or not, as review_fanout.js does
    lenses: list[dict] = []
    reports: list[dict] = []
    gaps: list[dict] = []
    merges: list[dict] = []
    auxiliary_groups: list[tuple[dict, list[dict]]] = []
    rejected_total = 0
    result_events = 0
    failed_events = 0
    missing_events = 0

    for start in starts:
        pair = (start["agentId"], start["key"])
        terminal = terminals.get(pair)
        label = start["label"]
        lens = _workflow_lens(label)
        role = ("auxiliary" if workflow_kind == "review" and start["phase"] == "Merge"
                else "stage" if start["phase"] == "Verify" or (
                    workflow_kind == "review" and start["phase"] == "Fold")
                else "source")
        coverage = {
            "agentId": start["agentId"], "key": start["key"], "label": label,
            "phase": start["phase"], "lens": lens, "role": role,
            "status": "failed", "observed": None, "valid": None, "rejected": 0,
            "terminal": "missing", "issues": [],
        }
        if terminal is None:
            missing_events += 1
            coverage["issues"].append("missing terminal event")
            lenses.append(coverage)
            continue
        coverage["terminal"] = terminal["type"]
        if terminal["type"] == "failed":
            failed_events += 1
            coverage["issues"].append("workflow recorded a failed terminal event")
            lenses.append(coverage)
            continue

        result_events += 1
        value = terminal.get("result")
        if not _workflow_record(value):
            coverage["status"] = "uncovered"
            coverage["issues"].append("terminal result is not an object")
            coverage["rejected"] = 1
            rejected_total += 1
            lenses.append(coverage)
            continue

        if role == "stage":
            coverage["status"] = "completed"
            lenses.append(coverage)
            continue

        if role == "auxiliary":
            groups = value.get("findings")
            if not isinstance(groups, list):
                coverage["status"] = "uncovered"
                coverage["issues"].append("auxiliary result findings is not an array")
                coverage["rejected"] = 1
                rejected_total += 1
            else:
                rejected = sum(not (_workflow_record(group)
                                   and isinstance(group.get("merged_from"), list)
                                   and bool(group["merged_from"])
                                   and all(_workflow_string(item) for item in group["merged_from"]))
                               for group in groups)
                valid_groups = [group for group in groups
                                if _workflow_record(group)
                                and isinstance(group.get("merged_from"), list)
                                and bool(group["merged_from"])
                                and all(_workflow_string(item) for item in group["merged_from"])]
                coverage.update(observed=len(groups), valid=len(groups) - rejected,
                                rejected=rejected, status="partial" if rejected else "completed")
                auxiliary_groups.append((coverage, valid_groups))
                rejected_total += rejected
                if rejected:
                    coverage["issues"].append("malformed auxiliary grouping entries were rejected")
            lenses.append(coverage)
            continue

        if workflow_kind == "review":
            source_rows = value.get("findings")
            if not isinstance(source_rows, list):
                coverage["status"] = "uncovered"
                coverage["issues"].append("result findings is not an array")
                coverage["rejected"] = 1
                rejected_total += 1
                lenses.append(coverage)
                continue
            item_rejected = 0
            for index, finding in enumerate(source_rows):
                entry_count += 1
                problem = review_result.finding_problem(finding)
                if problem:
                    item_rejected += 1
                    coverage["issues"].append(f"finding F{entry_count} (entry {index}) rejected: {problem}")
                    continue
                items.append({"id": f"F{entry_count}", "lens": lens, "label": label,
                              "index": index, "finding": finding})
            metadata_rejected = 0
            report = value.get("report")
            if isinstance(report, str) and report.strip():
                reports.append({"lens": lens, "label": label, "report": report})
            elif report is not None:
                metadata_rejected = 1
                coverage["issues"].append("malformed report was rejected")
            rejected = item_rejected + metadata_rejected
            coverage.update(observed=len(source_rows), valid=len(source_rows) - item_rejected,
                            rejected=rejected, status="partial" if rejected else "completed")
            rejected_total += rejected
            lenses.append(coverage)
            continue

        source_rows = value.get("claims")
        checked = value.get("checked")
        if not isinstance(source_rows, list) or not _valid_explore_checked(checked):
            coverage["status"] = "uncovered"
            coverage["issues"].append("result claims/checked shape is malformed")
            coverage["rejected"] = 1
            rejected_total += 1
            lenses.append(coverage)
            continue
        coverage["checked"] = checked
        claim_rejected = 0
        for index, claim in enumerate(source_rows):
            entry_count += 1
            if not _valid_explore_claim(claim):
                claim_rejected += 1
                continue
            items.append({"id": f"C{entry_count}", "lens": lens, "label": label,
                          "index": index, "claim": claim})
        gap_rejected = 0
        source_gaps = value.get("gaps", [])
        if not isinstance(source_gaps, list):
            gap_rejected = 1
            coverage["issues"].append("result gaps is not an array")
        else:
            for index, gap in enumerate(source_gaps):
                if not _workflow_string(gap):
                    gap_rejected += 1
                    continue
                gaps.append({"lens": lens, "label": label, "index": index, "gap": gap})
        rejected = claim_rejected + gap_rejected
        status = "partial" if rejected else "completed"
        if not source_rows and checked["stoppedAt"] != "trigger-not-met" and (
                checked["stoppedAt"] == "blocked" or not checked["toolsUsed"]):
            status = "uncovered"
            coverage["issues"].append("empty result has no completed search provenance")
        coverage.update(observed=len(source_rows), valid=len(source_rows) - claim_rejected,
                        rejected=rejected, status=status)
        if claim_rejected:
            coverage["issues"].append("malformed claim entries were rejected")
        if gap_rejected:
            coverage["issues"].append("malformed gap entries were rejected")
        rejected_total += rejected
        lenses.append(coverage)

    source_ids = [item["id"] for item in items]
    source_id_set = set(source_ids)
    candidate_ids = (_review_candidate_ids(items) if workflow_kind == "review" else source_id_set)
    for coverage, groups in auxiliary_groups:
        flattened = []
        invalid = None
        for group in groups:
            ids = group["merged_from"]
            if len(ids) != len(set(ids)):
                invalid = "auxiliary grouping repeats a source F-id"
                break
            unknown = [item for item in ids if item not in source_id_set]
            if unknown:
                invalid = "auxiliary grouping names unknown source F-id(s): " + ", ".join(unknown)
                break
            non_candidates = [item for item in ids if item not in candidate_ids]
            if non_candidates:
                invalid = "auxiliary grouping names non-candidate source F-id(s): " + ", ".join(non_candidates)
                break
            flattened.extend(ids)
        if invalid is None and (len(flattened) != len(candidate_ids)
                                or set(flattened) != candidate_ids):
            invalid = "auxiliary grouping does not partition candidate F-ids"
        if invalid is not None:
            coverage["status"] = "uncovered"
            coverage["issues"].append(invalid)
            coverage["rejected"] += 1
            rejected_total += 1
            continue
        for group in groups:
            ids = group["merged_from"]
            if len(ids) > 1:
                merges.append({"id": f"M{len(merges) + 1}", "merged_from": list(ids)})

    status = _workflow_status(lenses)
    coverage_counts = {name: sum(row["status"] == name for row in lenses)
                       for name in ("completed", "partial", "failed", "uncovered")}
    counts = {
        "starts": len(starts), "terminals": result_events + failed_events,
        "attempts": sum(len(attempts) for attempts in attempts_by_key.values()),
        "attemptTerminals": len(terminals), "results": result_events,
        "failedTerminals": failed_events, "missingTerminals": missing_events,
        "items": len(items), "reports": len(reports), "gaps": len(gaps), "merges": len(merges),
        "rejected": rejected_total,
        "sourceLenses": sum(row["role"] == "source" for row in lenses),
        "auxiliaries": sum(row["role"] == "auxiliary" for row in lenses),
        **coverage_counts,
    }
    verification = None
    if verify:
        import verify_claims
        record = workflow_run_record(run_dir) or {}
        args = record.get('args') if isinstance(record.get('args'), dict) else {}
        subject_root = root or args.get('root') or verify_claims.REPO
        member = 'finding' if workflow_kind == 'review' else 'claim'
        checker_rows = [dict(id=item['id'], evidence=verify_claims.items_of(item[member]),
                             polarity=item[member].get('polarity')) for item in items]
        roots, unresolved_roots = {}, set()
        if workflow_kind == 'review' and root is None and isinstance(args.get('fold'), dict):
            import fold_check
            fold = args['fold']
            specs = fold.get('subjects') or ([fold] if fold.get('root') else [])
            # Routing uses roots and files only; the archive checks current bytes, not fold snapshots.
            subjects = [fold_check.Subject(spec['root'], spec['files'],
                        {'kind': 'blobs', 'files': dict.fromkeys(spec['files'])}) for spec in specs
                        if isinstance(spec, dict) and spec.get('root') and isinstance(spec.get('files'), list)]
            routed, _unrouted, _ambiguous = fold_check.route([
                dict(item[member], id=item['id']) for item in items], subjects)
            roots = {key: subjects[index].root for key, index in routed.items()}
            if specs:
                unresolved_roots = set(_unrouted + _ambiguous)
        verification = verify_claims.check_claims([])
        for row in checker_rows:
            if row['id'] in unresolved_roots:
                verification['errors'].append(f"{row['id']}: review subject root is unrouted or ambiguous")
                row = dict(row, evidence=[])
            checked = verify_claims.check_claims([row], root=roots.get(row['id'], subject_root),
                session_id=run_dir.parents[2].name if len(run_dir.parents) > 2 else None)
            verification['verdicts'].update(checked['verdicts'])
            verification['errors'].extend(checked['errors'])
            for verdict, count in checked['counts'].items():
                verification['counts'][verdict] += count
        if verification['counts']['UNVERIFIED'] or verification['counts']['UNCHECKED'] or verification['errors']:
            verification['status'] = 'fail'
        for item in items:
            verdict = verification['verdicts'][item['id']]['verdict']
            item[member] = dict(item[member], verdict=verdict,
                                confidence='verified' if verdict == 'VERIFIED' else 'unverified')
        if status != 'completed':
            verification['status'] = 'fail'
            verification['errors'].append(f'workflow coverage is {status}')
    return {
        "verification": verification,
        "contract": WORKFLOW_RESULT_CONTRACT,
        "workflowKind": workflow_kind,
        "workflowDir": str(run_dir),
        "idOrder": id_order,
        "status": status,
        "delivered": status == "completed",
        "journal": {
            "relativePath": "journal.jsonl", "path": str(journal_path),
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "lines": len(raw.splitlines()),
        },
        "counts": counts,
        "items": items,
        "lenses": lenses,
        "reports": reports,
        "gaps": gaps,
        "merges": merges,
    }


def _workflow_commands(workflow_kind: str) -> dict:
    base = ("python3 .claude/tools/session_digest.py --workflow-dir "
            '"<transcriptDir-from-Workflow-result>" '
            f"--workflow-kind {workflow_kind}")
    expected = "--expect-journal-sha256 <sha256-from-manifest>"
    page_kinds = "|".join(WORKFLOW_PAGE_KINDS)
    return {
        "manifest": base + " --workflow-manifest",
        "select": base + " --workflow-select <ID> " + expected,
        "page": base + f" --workflow-page {page_kinds} --page <N> --page-size <N> " + expected,
        "full": base + " --workflow-full " + expected,
    }


def workflow_result_manifest(archive: dict) -> dict:
    counts = dict(archive["counts"])
    counts["omittedFromManifest"] = sum(len(archive[kind]) for kind in WORKFLOW_PAGE_KINDS)
    return {
        "contract": archive["contract"], "workflowKind": archive["workflowKind"],
        "workflowDir": archive["workflowDir"], "status": archive["status"],
        "delivered": archive["delivered"], "journal": dict(archive["journal"]),
        "counts": counts,
        "coverage": {
            "requested": counts["starts"], "completed": counts["completed"],
            "partial": counts["partial"], "failed": counts["failed"],
            "uncovered": counts["uncovered"],
        },
        "selectors": {
            "ids": "F<number>" if archive["workflowKind"] == "review" else "C<number>",
            "sourceOrder": ("run-log args.agents order, then item-array order"
                            if archive.get("idOrder") == "engine"
                            else "started-agent order, then item-array order"),
            "idOrder": archive.get("idOrder", "start-order"),
            "pages": list(WORKFLOW_PAGE_KINDS), "hashRequiredForEvidence": True,
        },
        "commands": _workflow_commands(archive["workflowKind"]),
    }


def verify_workflow_journal_hash(archive: dict, expected: str) -> None:
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9A-Fa-f]{64}", expected):
        raise ValueError("expected journal SHA256 must be exactly 64 hexadecimal characters")
    actual = archive["journal"]["sha256"]
    if actual.lower() != expected.lower():
        raise ValueError(f"workflow journal hash mismatch: expected {expected.lower()}, got {actual}")


def _workflow_envelope(archive: dict) -> dict:
    return {
        "contract": archive["contract"], "workflowKind": archive["workflowKind"],
        "workflowDir": archive["workflowDir"],
        "status": archive["status"], "delivered": archive["delivered"],
        "journal": dict(archive["journal"]), "counts": dict(archive["counts"]),
    }


def workflow_result_select(archive: dict, ids: list[str]) -> dict:
    if not ids:
        raise ValueError("at least one workflow selector is required")
    duplicates = [value for index, value in enumerate(ids) if value in ids[:index]]
    if duplicates:
        raise ValueError("duplicate workflow selector(s): " + ", ".join(dict.fromkeys(duplicates)))
    available = {}
    for row in archive["items"]:
        available.setdefault(row["id"], []).append(row)
    missing = [value for value in ids if value not in available]
    if missing:
        raise ValueError("unknown workflow selector(s): " + ", ".join(missing))
    out = _workflow_envelope(archive)
    out.update({"selectors": list(ids), "rows": [row for value in ids for row in available[value]]})
    return out


def workflow_result_page(archive: dict, kind: str, page: int, page_size: int) -> dict:
    if kind not in WORKFLOW_PAGE_KINDS:
        raise ValueError("workflow page kind must be items, lenses, reports or gaps")
    if not isinstance(page, int) or page < 1:
        raise ValueError("page must be a positive integer")
    if not isinstance(page_size, int) or not 1 <= page_size <= 50:
        raise ValueError("page size must be between 1 and 50")
    rows = archive[kind]
    pages = max(1, (len(rows) + page_size - 1) // page_size)
    if page > pages:
        raise ValueError(f"page {page} exceeds {pages}")
    start = (page - 1) * page_size
    out = _workflow_envelope(archive)
    out.update({
        "kind": kind, "page": page, "pageSize": page_size, "pages": pages,
        "total": len(rows), "complete": page == pages,
        "nextPage": page + 1 if page < pages else None,
        "rows": rows[start:start + page_size],
    })
    return out


def workflow_result_full(archive: dict) -> dict:
    out = _workflow_envelope(archive)
    out['verification'] = archive.get('verification')
    out.update({kind: archive[kind] for kind in WORKFLOW_PAGE_KINDS})
    return out


WORKFLOW_REPLAY = Path(__file__).resolve().parent / "workflow_replay.mjs"


def replay_workflow_result(workflow_dir: Path) -> tuple[object, str | None]:
    """-> (the run's full processed result, None), or (None, the gap that stopped the replay)."""
    node = shutil.which("node")
    if node is None:
        return None, "local replay could not run: node is not on PATH"
    try:
        proc = subprocess.run([node, str(WORKFLOW_REPLAY), str(workflow_dir)], capture_output=True,
                              text=True, encoding="utf-8", timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"local replay could not run: {exc}"
    if proc.returncode != 0:
        return None, proc.stderr.strip() or f"workflow_replay exited {proc.returncode}"
    try:
        return json.loads(proc.stdout), None
    except ValueError:
        return None, "workflow_replay exited 0 without a JSON result"


def workflow_result_full_or_archive(archive: dict, workflow_dir: Path) -> dict:
    result, gap = replay_workflow_result(workflow_dir)
    if gap is None:
        out = _workflow_envelope(archive)
        out.update({"source": "replay", "result": result})
        return out
    out = workflow_result_full(archive)
    out.update({"source": "archive", "replayGap": gap})
    return out


def write_json_atomic(path: Path, value: dict) -> None:
    """Publish JSON without exposing a partial target."""
    payload = (json.dumps(value, indent=1, ensure_ascii=True) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".session-digest-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        replace_path(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def write_text_atomic(path: Path, text: str) -> None:
    """Publish a UTF-8 Markdown projection without exposing a partial target."""
    payload = (text or "").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".session-digest-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        replace_path(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _prompt_preview(rows: list[dict], cap: int) -> list[dict]:
    """First row plus the most recent rows: stable row IDs, the latest row always present,
    never more than `cap` rows. A non-positive cap means uncapped."""
    if not cap or len(rows) <= cap:
        return rows
    if cap == 1:
        return rows[-1:]
    picked = rows[:1] + rows[-(cap - 1):]
    seen: set = set()
    out = []
    for row in picked:
        key = row.get("index")
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return sorted(out, key=lambda row: row.get("index", -1))


def _excerpt(text: str, n: int) -> str:
    """One-line excerpt with an explicit omitted-character count."""
    return clip(" ".join((text or "").split()), n)


def _clip_bytes(text: str, max_bytes: int) -> tuple[str, bool]:
    """Clip to a UTF-8 byte budget on a character boundary. The omitted count is explicit;
    no partial character is silently dropped."""
    text = text or ""
    if len(text.encode("utf-8")) <= max_bytes:
        return text, False
    reserve = 32
    kept: list[str] = []
    used = 0
    for ch in text:
        n = len(ch.encode("utf-8"))
        if used + n > max_bytes - reserve:
            break
        kept.append(ch)
        used += n
    omitted = len(text) - len(kept)
    return "".join(kept).rstrip() + f" …[+{omitted} chars]", True


def _display_files(d: dict, project_dir: str = "") -> dict:
    root = os.path.normpath(project_dir).replace("/", "\\") + "\\" if project_dir else ""
    return {(p[len(root):] if root and p.replace("/", "\\").startswith(root) else p): c
            for p, c in (d.get("files_modified_counts") or {}).items()}


def _prompt_count_text(d: dict) -> str:
    """`N` or `N (+M from command arguments and answers[, +K model-issued commands])`. A model
    `Skill` call's argument row (`issuer: model`) is counted apart: it is not an owner prompt."""
    msgs = [m for m in d.get("user_messages") or [] if isinstance(m, dict)]
    model = sum(1 for m in msgs if m.get("issuer") == "model")
    recovered = sum(1 for m in msgs if m.get("recovered")) - model
    typed = len(msgs) - recovered - model
    extra = ([f"+{recovered} from command arguments and answers"] if recovered else []) + \
        ([f"+{model} model-issued commands"] if model else [])
    return f"{typed} ({', '.join(extra)})" if extra else str(typed)


def _digest_head(d: dict, path: Path, sub_note: str) -> list:
    m = d["metadata"]
    return [f"# Session digest — {str(d.get('session_id') or 'unknown')[:8]}  ({path.name})",
            f"messages {m['total_messages']} · tool calls {m['total_tool_calls']}{sub_note} · duration {(m['duration_seconds'] or 0) // 60} min · "
            f"compactions {d['compactions']['count']} · user prompts {_prompt_count_text(d)} · "
            f"friction rows {len(d.get('friction') or [])} · files modified {len(d.get('files_modified_counts') or {})}"]


def _retrieval_footer(d: dict) -> list:
    sid8 = str(d.get("session_id") or "unknown")[:8]
    return ["", f"Full JSON: logs/session_digest_{sid8}.json",
            f"Evidence index: logs/session_digest_{sid8}.index.json",
            "Page: session_digest.py --digest-file <full-json> --evidence-page prompts|friction|files --page <N>",
            "Select: session_digest.py --digest-file <full-json> --select <ID> [--select <ID> ...]"]


def _mode_row_attribution(row: dict) -> str:
    if row.get("recovered") != "command_arguments":
        return "owner"
    return {"owner": "owner", "model": "model"}.get(row.get("issuer"), "unattributed")


def _mode_row_tag(row: dict) -> str:
    timestamp = row.get("timestamp") or ""
    stamp = timestamp[11:16] if len(timestamp) >= 16 else "--:--"
    signals = ",".join(row.get("signals") or []) or "-"
    return f"{stamp} {signals} {_mode_row_attribution(row)}"


def _mode_prompt_line(row: dict, limit: int) -> str:
    return (f"- [{_evidence_id('U', row)} {_mode_row_tag(row)}] "
            f"{_excerpt(row.get('content') or '', limit)}")


def _mode_friction_line(row: dict, limit: int) -> str:
    kind = "DENIED" if row.get("denied") else "error"
    detail = (f"error: {row.get('error') or '(none)'} | "
              f"next: {row.get('response') or '(no text before the next prompt)'}")
    return (f"- [{_evidence_id('F', row)} {kind}] {row.get('tool') or 'unknown'}: "
            f"{_excerpt(detail, limit)}")


def _mode_filtered_prompts(d: dict) -> list[dict]:
    return [row for row in (d.get("user_messages") or []) if is_substantive_owner_prompt(row)]


def _mode_is_task_anchor(row: dict) -> bool:
    if row.get("recovered") == "command_arguments":
        return False
    content = (row.get("content") or "").strip()
    if content.startswith("(command) "):
        content = content[len("(command) "):].lstrip()
    if content.startswith("/"):
        command = content.split()[0]
        if command in _SUBSTANTIVE_CONTROL_COMMANDS or command in _TASK_ANCHOR_WRAPPER_COMMANDS:
            return False
    return True


def _mode_task_anchor(rows: list[dict]) -> dict | None:
    owned = [row for row in rows if row.get("recovered") != "command_arguments"]
    return next((row for row in owned if _mode_is_task_anchor(row)), owned[0] if owned else None)


def _mode_unique_rows(rows: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        key = _evidence_id("U", row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return sorted(unique, key=lambda row: row.get("index", -1))


def _mode_priority_timeline(rows: list[dict]) -> list[dict]:
    anchor = _mode_task_anchor(rows)
    priority = [anchor] if anchor else []
    priority += [row for row in rows
                 if row.get("signals") or row.get("recovered") == "question_answer"]
    priority += rows[-12:]
    return _mode_unique_rows([row for row in priority if row is not None])


def _mode_files(d: dict, project_dir: str) -> list[tuple[str, int]]:
    return sorted(_display_files(d, project_dir).items(), key=lambda kv: (-kv[1], kv[0]))


def _mode_task_text(task_record: str, limit: int) -> str:
    return _clip_bytes(task_record, max(0, limit))[0] if task_record else ""


def _brief_mode_body(d: dict, path: Path, outcome: str, project_dir: str, task_record: str,
                     clips: tuple[int, int, int, int]) -> str:
    anchor_clip, latest_clip, outcome_clip, task_clip = clips
    census = d.get("tool_census") or {}
    sub_note = (f" (+{census['subagent_total']} in {census['subagent_transcripts']} subagent transcripts)"
                if census.get("subagent_total") else "")
    owner_rows = _mode_filtered_prompts(d)
    anchor = _mode_task_anchor(owner_rows)
    latest = next((row for row in reversed(owner_rows)
                   if row.get("recovered") != "command_arguments"), None)
    latest_command = next((row for row in reversed(owner_rows)
                           if row.get("recovered") == "command_arguments"), None)
    out = _digest_head(d, path, sub_note)
    out += ["", "## Task anchor",
            _mode_prompt_line(anchor, anchor_clip) if anchor else "(none recorded)",
            "", "## Latest owner input/directive",
            _mode_prompt_line(latest, latest_clip) if latest else "(none recorded)"]
    if latest_command is not None:
        out += ["", f"## Latest command request ({_mode_row_attribution(latest_command)})",
                _mode_prompt_line(latest_command, latest_clip)]
    out += ["", "## Status evidence — last substantive assistant text (not a completion verdict)",
            _excerpt(outcome, outcome_clip) or "(no assistant text)"]
    if task_record:
        out += ["", "## Active task record", _mode_task_text(task_record, task_clip)]
    out += _retrieval_footer(d)
    return "\n".join(out)


def _render_brief_mode(d: dict, path: Path, outcome: str, project_dir: str = "", tools: bool = False,
                       task_record: str = "") -> str:
    """Render the locating card, including the task record, clipping toward BRIEF_MAX_BYTES.
    Returns the text over the cap when every clip has reached its floor."""
    clips = [500, 500, 500, 1200]
    while True:
        text = _brief_mode_body(d, path, outcome, project_dir, task_record, tuple(clips))
        if len(text.encode("utf-8")) <= BRIEF_MAX_BYTES:
            return text
        changed = False
        for index in (3, 2, 1, 0):
            if clips[index] > 48:
                clips[index] = max(48, clips[index] // 2)
                changed = True
                break
        if not changed:
            return text


def _agent_work_block(d: dict, outcome: str, limit: int | None) -> list[str]:
    """The agent-work lines, or none when the record carries no `agent_work` (a digest saved before it)."""
    work = d.get("agent_work")
    return [""] + agent_work_lines(work, outcome, limit) if work else []


def _handoff_mode_body(d: dict, path: Path, outcome: str, project_dir: str, task_record: str,
                       timeline: list[dict], friction_limit: int, file_limit: int,
                       clips: tuple[int, int, int, int, int, int], agent_limit: int = 6) -> str:
    row_clip, friction_clip, anchor_clip, latest_clip, outcome_clip, task_clip = clips
    census = d.get("tool_census") or {}
    sub_note = (f" (+{census['subagent_total']} in {census['subagent_transcripts']} subagent transcripts)"
                if census.get("subagent_total") else "")
    all_rows = _mode_filtered_prompts(d)
    anchor = _mode_task_anchor(all_rows)
    latest = next((row for row in reversed(all_rows)
                   if row.get("recovered") != "command_arguments"), None)
    latest_command = next((row for row in reversed(all_rows)
                           if row.get("recovered") == "command_arguments"), None)
    all_friction = list(d.get("friction") or [])
    files = _mode_files(d, project_dir)
    shown_friction = all_friction[-friction_limit:] if friction_limit else []
    shown_files = files[:file_limit] if file_limit else []
    out = _digest_head(d, path, sub_note)
    out += ["", "## Task anchor",
            _mode_prompt_line(anchor, anchor_clip) if anchor else "(none recorded)",
            "", "## Latest owner input/directive",
            _mode_prompt_line(latest, latest_clip) if latest else "(none recorded)"]
    if latest_command is not None:
        out += ["", f"## Latest command request ({_mode_row_attribution(latest_command)})",
                _mode_prompt_line(latest_command, latest_clip)]
    out += ["", "## Status evidence — last substantive assistant text (not a completion verdict)",
            _excerpt(outcome, outcome_clip) or "(no assistant text)"]
    out += _agent_work_block(d, outcome, agent_limit)
    out += ["", f"## Prompt timeline (shown/total: {len(timeline)}/{len(all_rows)})"]
    out += [_mode_prompt_line(row, row_clip) for row in timeline] or ["(none recorded)"]
    if len(timeline) < len(all_rows):
        omitted_ids = [_evidence_id("U", row) for row in all_rows if row not in timeline]
        out.append("Omitted prompt IDs: " + ", ".join(omitted_ids[:40])
                   + (f" …[+{len(omitted_ids) - 40} IDs]" if len(omitted_ids) > 40 else ""))
    out += ["", f"## Recent friction (shown/total: {len(shown_friction)}/{len(all_friction)})"]
    out += [_mode_friction_line(row, friction_clip) for row in shown_friction] or ["(none)"]
    out += ["", f"## Files modified (shown/total: {len(shown_files)}/{len(files)})"]
    out += [f"- {name} ×{count}" for name, count in shown_files] or ["(none; use the files evidence pages for the complete list)"]
    if len(shown_friction) < len(all_friction) or len(shown_files) < len(files):
        out.append("Optional friction/file rows omitted; use evidence pages for exact retrieval.")
    if task_record:
        out += ["", "## Active task record", _mode_task_text(task_record, task_clip)]
    out += _retrieval_footer(d)
    return "\n".join(out)


def _render_handoff_mode(d: dict, path: Path, outcome: str, project_dir: str = "", tools: bool = False,
                         task_record: str = "") -> str:
    """Render a deterministic bounded resume packet, shedding optional evidence first."""
    rows = _mode_filtered_prompts(d)
    priority = _mode_priority_timeline(rows)
    timeline = list(rows)
    friction_limit = min(8, len(d.get("friction") or []))
    file_limit = min(16, len(d.get("files_modified_counts") or {}))
    clips = [500, 260, 500, 500, 1800, 2000]
    agent_limit = 6
    while True:
        text = _handoff_mode_body(d, path, outcome, project_dir, task_record, timeline,
                                  friction_limit, file_limit, tuple(clips), agent_limit)
        if len(text.encode("utf-8")) <= HANDOFF_MAX_BYTES:
            return text
        if file_limit:
            file_limit = 0
            continue
        if friction_limit > 1:
            friction_limit = 1
            continue
        minimum = _mode_unique_rows(priority + rows[-4:])
        if timeline != minimum and len(timeline) > len(minimum):
            timeline = minimum
            continue
        if d.get("agent_work") and agent_limit > 0:
            agent_limit = {6: 3, 3: 1}.get(agent_limit, 0)
            continue
        if len(timeline) > 8:
            timeline = _prompt_preview(timeline, max(8, len(timeline) // 2))
            continue
        if len(timeline) > 2:
            timeline = _prompt_preview(timeline, max(2, len(timeline) // 2))
            continue
        changed = False
        for index in (4, 5, 3, 2, 0, 1):
            floor = 256 if index == 5 else 48
            if clips[index] > floor:
                clips[index] = max(floor, clips[index] // 2)
                changed = True
                break
        if changed:
            continue
        raise ValueError("handoff mandatory packet exceeds HANDOFF_MAX_BYTES")


def _render_full_mode(d: dict, path: Path, outcome: str, project_dir: str = "", tools: bool = False,
                      task_record: str = "") -> str:
    """Render the uncapped human projection; exact machine fields remain in JSON/index files."""
    census = d.get("tool_census") or {}
    sub_note = (f" (+{census['subagent_total']} in {census['subagent_transcripts']} subagent transcripts)"
                if census.get("subagent_total") else "")
    out = _digest_head(d, path, sub_note)
    out += ["", "Projection: human-readable evidence; not raw transcript or JSON field parity."]
    prompts = d.get("user_messages") or []
    out += ["", f"## User prompts ({len(prompts)}) — complete recovered prompt projection"]
    out += [_mode_prompt_line(row, 0) for row in prompts] or ["(none)"]
    fr = d.get("friction") or []
    out += ["", f"## Friction ({len(fr)}) — complete error and response text"]
    if not fr:
        out.append("(none)")
    for f in fr:
        kind = "DENIED" if f.get("denied") else "error"
        out.append(f"- [{_evidence_id('F', f)} {kind}] {f.get('tool') or 'unknown'}: `{f.get('input') or ''}`"
                   f"\n  error: {f.get('error') or ''}"
                   f"\n  next: {f.get('response') or '(no text before the next prompt)'}")
    items = _mode_files(d, project_dir)
    out += ["", f"## Files modified ({len(items)}) — complete list"]
    out += [f"- {p} ×{c}" for p, c in items] or ["(none)"]
    out += _agent_work_block(d, outcome, None)
    if tools:
        normalized = {"main": census.get("main", {}), "subagents": census.get("subagents", {}),
                      "subagent_transcripts": census.get("subagent_transcripts", 0),
                      "main_total": census.get("main_total", 0),
                      "subagent_total": census.get("subagent_total", 0)}
        out += render_tools(normalized)
    out += ["", "## Status evidence — last substantive assistant text block (not a completion verdict)",
            outcome or "(no assistant text)"]
    if task_record:
        out += ["", "## Active task record", task_record]
    out += _retrieval_footer(d)
    return "\n".join(out)


def render(d: dict, path: Path, outcome: str, caps: dict, project_dir: str = "", tools: bool = False,
           task_record: str = "") -> str:
    """Render one presentation without mutating the durable evidence dictionary."""
    if caps.get("mode") == "full":
        return _render_full_mode(d, path, outcome, project_dir, tools, task_record)
    if caps.get("mode") == "brief":
        return _render_brief_mode(d, path, outcome, project_dir, tools, task_record)
    return _render_handoff_mode(d, path, outcome, project_dir, tools, task_record)



def render_context(d: dict) -> str:
    census = d.get("context_census") or {}
    boundaries = census.get("boundaries", [])
    boundary_count = census.get("boundary_count", len(boundaries))
    first = census.get("first_assistant") or {}
    known = lambda value: "unknown" if value is None else str(value)
    sid = str(d.get("session_id") or "unknown")[:8]
    out = [f"# Context census — {sid}",
           f"Main assistant messages: {census.get('assistant_messages', 0)}; compact boundaries: {boundary_count}",
           f"First assistant: {first.get('model') or 'unknown'}; reported input sum: {known(first.get('reported_input_sum'))}",
           "Usage is provider-reported. UTF-8 bytes below are cumulative evidence, not live context.",
           "", "| Usage field | Observed total | Measured messages | Missing |", "|---|---:|---:|---:|"]
    for field, counts in census.get("usage", {}).items():
        out.append(f"| {field} | {known(counts['observed_total'])} | {counts['measured_messages']} | {counts['missing_messages']} |")
    shown = boundaries[-10:]
    location = "full set in JSON" if boundary_count == len(boundaries) else f"{boundary_count - len(boundaries)} absent from this backup"
    out += ["", f"## Compact boundaries (last {len(shown)} of {boundary_count}; {location})",
            "| Timestamp | Pre | Post | Next reported input | Duration ms |", "|---|---:|---:|---:|---:|"]
    for boundary in shown:
        next_message = boundary.get("next_assistant") or {}
        values = [boundary.get('timestamp'), boundary.get('pre_tokens'), boundary.get('post_tokens'),
                  next_message.get('reported_input_sum'), boundary.get('duration_ms')]
        out.append("| " + " | ".join(known(value) for value in values) + " |")
    results = census.get("tool_results") or {}
    out += ["", f"Tool result text: {known(results.get('text_utf8_bytes'))} UTF-8 bytes in {results.get('count', 0)} results."]
    tools = sorted(results.get("by_tool", {}).items(), key=lambda pair: -(pair[1]['text_utf8_bytes'] or 0))
    out += [f"- {name}: {known(counts['text_utf8_bytes'])} text bytes; {counts['missing_text_results']} missing text measurements"
            for name, counts in tools[:6]]
    out += ["", "Coverage: " + json.dumps(census.get("coverage", {}), sort_keys=True),
            f"Full context JSON: logs/session_digest_{sid}.context.json"]
    return "\n".join(out)


def full_export_receipt(d: dict, export_path: Path, projection: str, task_record: str,
                       project_dir: str = "", tools: bool = False) -> str:
    """Describe an already-published full projection without printing its body."""
    census = d.get("tool_census") or {}
    files = d.get("files_modified_counts") or {}
    tool_count = census.get("main_total", 0) + census.get("subagent_total", 0)
    receipt = "\n".join([
        f"Full export: {export_path}",
        f"UTF-8 bytes: {len(projection.encode('utf-8'))}",
        "Sections: prompts=%d friction=%d files=%d tools=%d task_record=%s last_assistant=%s" % (
            len(d.get("user_messages") or []), len(d.get("friction") or []), len(files),
            tool_count if tools else 0, "yes" if task_record else "no",
            "yes" if d.get("outcome_last_assistant_text") else "no"),
        "Exact retrieval: --digest-file <full-json> --select <ID> or --evidence-page prompts|friction|files --page <N>",
        "Resume retrieval: session_digest.py --session <id-prefix> --handoff",
    ])
    clipped, _ = _clip_bytes(receipt, FULL_RECEIPT_MAX_BYTES)
    return clipped


def main() -> None:
    ap = argparse.ArgumentParser(description=usage(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", help="session id or unique prefix")
    ap.add_argument("--prompt-tail", help="text the session's latest real prompt must contain (e.g. session_end)")
    ap.add_argument("--project-dir", default=harness_root(), help="repo root the session ran in (default: the harness root)")
    presentation = ap.add_mutually_exclusive_group()
    presentation.add_argument("--brief", action="store_true",
                              help="bounded 2,048-byte identity card; not sufficient for pickup")
    presentation.add_argument("--handoff", action="store_true",
                              help="bounded 16,384-byte resume packet (also the default)")
    presentation.add_argument("--full", action="store_true",
                              help="atomically export a human-readable Markdown projection; print only a receipt")
    ap.add_argument("--digest-file", metavar="PATH", help="saved full JSON used by --select")
    ap.add_argument("--workflow-dir", metavar="PATH",
                    help="exact Workflow transcriptDir containing journal.jsonl")
    ap.add_argument("--workflow-kind", choices=("review", "explore"),
                    help="native Workflow result contract to validate")
    workflow_op = ap.add_mutually_exclusive_group()
    workflow_op.add_argument("--workflow-manifest", action="store_true",
                             help="validate a Workflow journal and print its body-free manifest")
    workflow_op.add_argument("--workflow-select", action="append", metavar="ID",
                             help="print one exact F<number> or C<number> source; repeatable")
    workflow_op.add_argument("--workflow-page", choices=WORKFLOW_PAGE_KINDS,
                             help="print one deterministic Workflow result page")
    workflow_op.add_argument("--workflow-full", action="store_true",
                             help="print the full processed result by local replay, else every archived source and coverage record")
    ap.add_argument("--expect-journal-sha256", metavar="HASH",
                    help="manifest SHA-256 required before any Workflow evidence output")
    ap.add_argument("--select", action="append", metavar="ID",
                    help="print one exact U<prompt-index> or F<friction-index> row; repeatable")
    ap.add_argument("--evidence-page", choices=("prompts", "friction", "files"),
                    help="print one bounded page from the compact evidence index")
    ap.add_argument("--page", type=int, default=1, help="evidence page number (default 1)")
    ap.add_argument("--page-size", type=int, default=20,
                    help="evidence rows per page, 1-50 (default 20)")
    ap.add_argument("--json-only", action="store_true", help="print only the JSON path")
    ap.add_argument("--context-only", action="store_true",
                    help="print bounded context accounting; skip outcome and child-transcript scans")
    ap.add_argument("--previous", action="store_true",
                    help="the nearest older prompt-bearing transcript before the exact active session")
    ap.add_argument("--list", type=int, metavar="N", help="list the newest N transcripts and exit")
    ap.add_argument("--match", metavar="TEXT",
                    help="pick the transcript whose assistant wrote TEXT (a pasted closing message); token overlap, "
                         "so re-rendered markdown still matches")
    ap.add_argument("--match-file", metavar="PATH", help="like --match, text read from PATH (long pastes, quotes)")
    ap.add_argument("--tools", action="store_true",
                    help="include the per-tool call census in the --full offline export")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")

    workflow_operation = (a.workflow_manifest or a.workflow_select
                          or a.workflow_page or a.workflow_full)
    workflow_option = (workflow_operation or a.workflow_dir or a.workflow_kind
                       or a.expect_journal_sha256)
    if workflow_option:
        if not workflow_operation:
            ap.error("--workflow-dir/--workflow-kind requires a --workflow-manifest/select/page/full operation")
        if not a.workflow_dir or not a.workflow_kind:
            ap.error("Workflow result operations require --workflow-dir and --workflow-kind")
        session_options = (a.session or a.prompt_tail or a.brief or a.handoff or a.full or a.digest_file
                           or a.select or a.evidence_page or a.json_only or a.context_only
                           or a.previous or a.list or a.match or a.match_file or a.tools)
        if session_options:
            ap.error("Workflow result operations are mutually exclusive with session-digest operations")
        if a.workflow_manifest and a.expect_journal_sha256:
            ap.error("--workflow-manifest computes the journal hash; do not pass --expect-journal-sha256")
        if not a.workflow_manifest and not a.expect_journal_sha256:
            ap.error("Workflow evidence output requires --expect-journal-sha256 from --workflow-manifest")
        try:
            archive = build_workflow_result_archive(Path(a.workflow_dir), a.workflow_kind)
            if a.workflow_manifest:
                value = workflow_result_manifest(archive)
            else:
                verify_workflow_journal_hash(archive, a.expect_journal_sha256)
                if a.workflow_select:
                    value = workflow_result_select(archive, a.workflow_select)
                elif a.workflow_page:
                    value = workflow_result_page(archive, a.workflow_page, a.page, a.page_size)
                else:
                    value = workflow_result_full_or_archive(archive, Path(a.workflow_dir))
            print(json.dumps(value, indent=1, ensure_ascii=True))
        except ValueError as exc:
            ap.error(str(exc))
        return

    if a.select or a.evidence_page:
        if not a.digest_file:
            ap.error("--select/--evidence-page requires --digest-file")
        if a.select and a.evidence_page:
            ap.error("--select and --evidence-page are mutually exclusive")
        saved = json.loads(Path(a.digest_file).read_text(encoding="utf-8"))
        try:
            value = (select_evidence(saved, a.select) if a.select else
                     evidence_page(build_evidence_index(saved), a.evidence_page,
                                   a.page, a.page_size))
            print(json.dumps(value, indent=1, ensure_ascii=True))
        except ValueError as exc:
            ap.error(str(exc))
        return
    if a.digest_file:
        ap.error("--digest-file requires --select or --evidence-page")
    if a.tools and not a.full:
        ap.error("--tools requires --full; read the census from the exported .full.md")

    pdir = session_store(a.project_dir)
    if a.list:
        print(list_sessions(pdir, a.list))
        return
    if a.match or a.match_file:
        text = Path(a.match_file).read_text(encoding="utf-8", errors="replace") if a.match_file else a.match
        path, ranked = match_transcript(pdir, text)
        print("MATCH " + "  ".join(f"{p.stem[:8]}={s:.2f}" for s, p in ranked) + "  (first is used)",
              file=sys.stderr)
    else:
        path = pick_transcript(pdir, a.session, a.prompt_tail, a.previous, cwd=a.project_dir)
    b = TranscriptSummaryBuilder(path.stem, str(path), full_evidence=not a.context_only)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            b.process_line(line)
    d = b.finalize(backup_limits=False)
    outcome = ""
    if not a.context_only:
        outcome = last_assistant_text(path)
        d["outcome_last_assistant_text"] = outcome
        d["tool_census"] = tool_census(path)
        d["user_messages"] = owner_prompts(path)
        d["agent_work"] = agent_work(path, last_owner_timestamp(d["user_messages"]), a.project_dir)
        if isinstance(d.get("evidence_coverage"), dict):
            d["evidence_coverage"]["collected_user_messages"] = len(d["user_messages"])
    else:
        d = {"session_id": path.stem, "context_census": d["context_census"],
             "omitted_scans": ["outcome", "child_tool_census"]}

    logs = Path(a.project_dir) / "logs"
    logs.mkdir(exist_ok=True)
    suffix = ".context" if a.context_only else ""
    out = logs / f"session_digest_{path.stem[:8]}{suffix}.json"
    write_json_atomic(out, d)
    if not a.context_only:
        index_out = logs / f"session_digest_{path.stem[:8]}.index.json"
        write_json_atomic(index_out, build_evidence_index(d))
    if a.json_only:
        print(out)
    elif a.context_only:
        print(render_context(d))
    else:
        block = task_record_block(path.stem, a.project_dir)
        caps = BRIEF if a.brief else (FULL if a.full else SESSION)
        projection = render(d, path, outcome, caps, a.project_dir, a.tools, block)
        if a.full:
            export_path = logs / f"session_digest_{path.stem[:8]}.full.md"
            try:
                write_text_atomic(export_path, projection)
            except OSError as exc:
                print(f"full export failed: {exc}", file=sys.stderr)
                raise SystemExit(1)
            sys.stdout.write(full_export_receipt(d, export_path, projection, block, a.project_dir, a.tools))
        else:
            sys.stdout.write(projection + ("" if a.brief else adoption_note(path.stem)))


def adoption_note(target: str) -> str:
    """Hand the running sidecars of `target`, a session with no live host, to this session, and say which.

    A resume packet is the pickup, so its run moves the jobs: this session's `watch_records.py --session`
    reads only its own hook state, and the jobs' wakes would otherwise stay with a session that is gone.
    `dispatch_table.adopt_sidecars` owns the live-host test and the move. Empty when nothing moved; a failure
    is reported, because the jobs then wake nobody."""
    current = (os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not current or not target or current == target:
        return ""
    try:
        import dispatch_table
        moved = dispatch_table.adopt_sidecars(target, current)
    except Exception as exc:
        return "\n\nAdopting the running jobs of session %s failed (%s: %s); their ends wake no session.\n" % (
            target[:8], type(exc).__name__, exc)
    if not moved:
        return ""
    return "\n\nAdopted %d running job(s) of session %s; this session's watcher wakes on each end:\n%s\n" % (
        len(moved), target[:8], "\n".join("- %s  %s" % (e["jobs"][0].get("label"), e.get("record")) for e in moved))


def task_record_block(session_id: str, project_dir: str) -> str:
    """The session's active task record, or an explicit unavailable-evidence diagnostic."""
    try:
        tools = str(Path(__file__).resolve().parent)
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import task_record
        if not os.environ.get("HARNESS_TASK_RECORD_DIR"):
            os.environ["HARNESS_TASK_RECORD_DIR"] = str(Path(project_dir) / ".claude" / "logs" / "tasks")
        rec = task_record.active(session_id)
        if rec:
            block = task_record.render_block(rec["task_id"])
            if not block:
                raise ValueError("active record could not be rendered")
            return block
        # active() deliberately salvages bad records and returns None on directory
        # errors. The digest must not turn missing evidence into a no-task result.
        directory = Path(task_record.record_dir())
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            return ""
        for name in names:
            if name.endswith('.json'):
                with (directory / name).open(encoding='utf-8') as stream:
                    record = json.load(stream)
                if not isinstance(record, dict):
                    raise ValueError("invalid task evidence")
        return ""
    except Exception as exc:
        return "Task record evidence unavailable (%s); do not infer no active task." % type(exc).__name__


if __name__ == "__main__":
    main()
