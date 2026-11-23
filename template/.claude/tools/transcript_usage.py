#!/usr/bin/env python3
"""The one reader of Claude Code transcript token usage, and of the login directories transcripts live in.

ONE API CALL PER `message.id`, never one per record. An assistant message spanning several content
blocks is written as several JSONL lines that each repeat the same `message.usage`, and `output_tokens`
grows across them, so the LAST record of an id holds the finalized usage and the complete content.
Summing per record double-counts.

Transport. A session whose calls name any non-`claude-` model ran through an external seat's proxy and
is `proxied`; every other session is `anthropic`. A proxied child can echo a `claude-*` pin of its own,
so the names decide across all of a session's calls, never its first one.

Login dirs. A login's transcripts live under `<login dir>/projects`. The login dirs are the default
`~/.claude`, `CLAUDE_CONFIG_DIR` when set, and each seat's `configDir` in `seats.json`, each once.

    transcript_usage.py <transcript.jsonl>     print the per-call usage rows as JSON lines
"""
import json
import os
import sys
from datetime import datetime

import launch_check  # the one seats.json reader (tools/launch_check.py)

ANTHROPIC = "anthropic"
PROXIED = "proxied"
SYNTHETIC_MODEL = "<synthetic>"
DEFAULT_LOGIN = "~/.claude"


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_ts(value):
    """Epoch seconds of an ISO-8601 transcript timestamp, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def iter_records(path, bad=None):
    """JSON objects of one JSONL file. Malformed and non-object lines are counted in `bad['lines']`."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    value = None
                if not isinstance(value, dict):
                    if bad is not None:
                        bad["lines"] = bad.get("lines", 0) + 1
                    continue
                yield value
    except OSError:
        if bad is not None:
            bad["unreadable"] = True


def reduce_records(records):
    """{calls, models}: `calls` is (message id, last assistant record) per id, in first-seen order;
    `models` is every model name the assistant records use. A record with no id keys on its uuid."""
    calls, order, models, count = {}, [], set(), 0
    for o in records:
        if o.get("type") != "assistant" or not isinstance(o.get("message"), dict):
            continue
        count += 1
        m = o["message"]
        if isinstance(m.get("model"), str) and m["model"]:
            models.add(m["model"])
        mid = m.get("id") or o.get("uuid") or ("rec-%d" % count)
        if mid not in calls:
            order.append(mid)
        calls[mid] = o
    return {"calls": [(mid, calls[mid]) for mid in order], "models": models}


def call_usage(mid, record):
    """Token usage of one reduced call. A token field the record lacks is None, never 0."""
    m = record.get("message") or {}
    us = m.get("usage") if isinstance(m.get("usage"), dict) else {}
    split = us.get("cache_creation") if isinstance(us.get("cache_creation"), dict) else {}
    return dict(
        id=mid, ts=parse_ts(record.get("timestamp")), model=m.get("model"),
        session=record.get("sessionId") or record.get("session_id"),
        inp=_number(us.get("input_tokens")), out=_number(us.get("output_tokens")),
        cr=_number(us.get("cache_read_input_tokens")),
        cw=_number(us.get("cache_creation_input_tokens")),
        cw5m=_number(split.get("ephemeral_5m_input_tokens")),
        cw1h=_number(split.get("ephemeral_1h_input_tokens")))


def transport_of(models):
    if any(not str(m).startswith("claude-") for m in models if m != SYNTHETIC_MODEL):
        return PROXIED
    return ANTHROPIC


def _session_from_path(path):
    parts = os.path.normpath(path).split(os.sep)
    if "subagents" in parts:
        i = parts.index("subagents")
        return parts[i - 1] if i > 0 else None
    return os.path.splitext(parts[-1])[0]


def read_transcript(path, bad=None):
    """{path, session, transport, calls, models} for one transcript or subagent file."""
    red = reduce_records(iter_records(path, bad))
    calls = [call_usage(mid, rec) for mid, rec in red["calls"]]
    session = next((c["session"] for c in calls if c["session"]), None) or _session_from_path(path)
    return dict(path=path, session=session, calls=calls, models=red["models"], transport=transport_of(red["models"]))


def transcript_files(root, since=None):
    for base, _dirs, names in os.walk(root):
        for name in sorted(names):
            if not name.endswith(".jsonl"):
                continue
            full = os.path.join(base, name)
            if since is not None:
                try:
                    if os.path.getmtime(full) < since:
                        continue
                except OSError:
                    continue
            yield full


def login_dirs():
    """Every login directory, expanded, each once: the default, `CLAUDE_CONFIG_DIR`, then each seat's `configDir`."""
    found = [DEFAULT_LOGIN]
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        found.append(os.environ["CLAUDE_CONFIG_DIR"])
    found += [row["configDir"] for row in launch_check.seat_rows().values() if row.get("configDir")]
    out = []
    for path in found:
        full = os.path.abspath(os.path.expanduser(path))
        if full not in out:
            out.append(full)
    return out


def projects_dirs():
    return [os.path.join(login, "projects") for login in login_dirs()]


def scan(roots, since=None):
    """Calls across every transcript under `roots`, one per message id, each with its session's `transport`.

    `since` skips files last modified before that epoch. A message id seen in several files (a resumed
    session copies its history) counts once, with its largest output. Synthetic calls are dropped.
    Returns `calls`, `files` scanned, `gaps` (roots that are not directories), and the malformed line
    count with the files that held them.
    """
    best, files, bad_lines, bad_files, gaps = {}, 0, 0, [], []
    for root in roots:
        if not os.path.isdir(root):
            gaps.append(root)
            continue
        for path in transcript_files(root, since):
            files += 1
            bad = {}
            t = read_transcript(path, bad)
            if bad.get("lines") or bad.get("unreadable"):
                bad_lines += bad.get("lines", 0)
                bad_files.append(path)
            for c in t["calls"]:
                if c["model"] == SYNTHETIC_MODEL:
                    continue
                c["transport"] = t["transport"]
                prior = best.get(c["id"])
                if prior is None or (c["out"] or 0) > (prior["out"] or 0):
                    best[c["id"]] = c
    return dict(calls=list(best.values()), files=files, gaps=gaps,
                unparseable_lines=bad_lines, unparseable_files=bad_files)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: transcript_usage.py <transcript.jsonl>", file=sys.stderr)
        sys.exit(2)
    for row in read_transcript(sys.argv[1])["calls"]:
        print(json.dumps(row))
