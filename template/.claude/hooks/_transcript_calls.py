"""One reader of a session transcript's main-thread Bash calls, for the guards that prove what this session did.

`session_events(transcript_path)` returns one event list per transcript file: the hook's own and, when
`CLAUDE_CODE_SESSION_ID` names another file beside it, that one. Events keep file order: a `Call` (a Bash
tool_use), the `Result` of a Bash call seen earlier, and a `Notice` for each `<task-notification>` a harness row
carries. A sidechain row (a subagent's) yields nothing. A caller pairs ids and tests creation times against the
stamps; a missing stamp stays None and the caller fails closed.

Cost: only lines that name a tool call, a tool result or a notice are parsed, and only Bash calls and their results are
kept. A file larger than `MAX_BYTES` is read from its tail, so a call older than that window proves nothing. The
answer is memoized by file path, size and mtime, so two judges in one hook call share one read.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import NamedTuple

MAX_BYTES = 256 * 1024 * 1024  # a full parse of a 239 MB transcript measured 1.5 s; the dispatcher's budget is 75 s
_TASK_NOTIFICATION = re.compile(r'<task-notification>\s*<task-id>(\w+)</task-id>.*?<output-file>(.+?)</output-file>'
                                r'\s*<status>(\w+)</status>\s*<summary>(.*?)</summary>', re.S)
_WANTED = ('"tool_use"', '"tool_result"', 'task-notification')
_memo: dict[str, tuple[tuple[int, int], list]] = {}


class Call(NamedTuple):
    stamp: float | None
    id: str
    command: str
    cwd: str | None


class Result(NamedTuple):
    stamp: float | None
    id: str
    is_error: bool
    text: str
    interrupted: bool


class Notice(NamedTuple):
    stamp: float | None
    task: str
    output: str
    status: str
    summary: str


def timestamp(row: dict) -> float | None:
    try:
        return datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00')).timestamp()
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _result_text(block: dict) -> str:
    content = block.get('content')
    if isinstance(content, list):
        return '\n'.join(str(part.get('text', '')) for part in content if isinstance(part, dict))
    return content if isinstance(content, str) else ''


def _task_notices(row: dict) -> list[tuple[str, str, str, str]]:
    """(task id, output file, status, summary) for each `<task-notification>` a harness row carries.

    Assistant rows are skipped: a model can type a notice into its own text, and only the harness writes these rows.
    A queued row keeps its text in `content`, an attachment in `attachment.prompt`, a user row in its text blocks."""
    if row.get('type') not in ('user', 'queue-operation', 'attachment'):
        return []
    message = row.get('message')
    body = message.get('content') if isinstance(message, dict) else None
    attachment = row.get('attachment')
    texts = []
    for value in (row.get('content'), attachment.get('prompt') if isinstance(attachment, dict) else None, body):
        if isinstance(value, str):
            texts.append(value)
        elif isinstance(value, list):
            texts += [str(part.get('text', '')) for part in value if isinstance(part, dict)]
    return [m.groups() for text in texts for m in _TASK_NOTIFICATION.finditer(text)]


def _read_events(path: str) -> list:
    with open(path, 'rb') as handle:
        size = os.fstat(handle.fileno()).st_size
        if size > MAX_BYTES:
            handle.seek(size - MAX_BYTES)
            handle.readline()
        raw = handle.read()
    events, bash_ids = [], set()
    for line in raw.decode('utf-8', errors='replace').splitlines():
        if not any(word in line for word in _WANTED):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get('isSidechain'):
            continue
        stamp = timestamp(row)
        content = (row.get('message') or {}).get('content') if isinstance(row.get('message'), dict) else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('name') == 'Bash':
                bash_ids.add(block.get('id'))
                events.append(Call(stamp, block.get('id'), (block.get('input') or {}).get('command', ''), row.get('cwd')))
            elif block.get('type') == 'tool_result' and block.get('tool_use_id') in bash_ids:
                outcome = row.get('toolUseResult')
                events.append(Result(stamp, block.get('tool_use_id'), bool(block.get('is_error')), _result_text(block),
                                     isinstance(outcome, dict) and bool(outcome.get('interrupted'))))
        if 'task-notification' in line:
            events += [Notice(stamp, *notice) for notice in _task_notices(row)]
    return events


def _events_of(path: str) -> list:
    try:
        info = os.stat(path)
        key = (info.st_size, info.st_mtime_ns)
        cached = _memo.get(path)
        if cached and cached[0] == key:
            return cached[1]
        events = _read_events(path)
    except (OSError, ValueError):
        return []
    _memo[path] = (key, events)
    return events


def session_events(transcript_path: str | None) -> list[list]:
    """One event list per distinct transcript of this session; an unreadable file is an empty list."""
    if not transcript_path:
        return []
    sid = os.environ.get('CLAUDE_CODE_SESSION_ID', '')
    paths = [transcript_path] + ([os.path.join(os.path.dirname(transcript_path), sid + '.jsonl')] if sid else [])
    return [_events_of(path) for path in dict.fromkeys(paths)]
