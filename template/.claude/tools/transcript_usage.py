#!/usr/bin/env python3
"""The one reader of Claude Code transcript token usage.

ONE API CALL PER `message.id`, never one per record. An assistant message spanning several
content blocks is written as several JSONL lines that each repeat the same `message.usage`, and
`output_tokens` grows across them, so the LAST record of an id holds the finalized usage and the
complete content array. Summing per record double-counts (measured 2.00x over one six-agent run,
2026-08-19; the history lives in orchestration_metrics.agent_usage, which reduces through here).

Consumers: `orchestration_metrics.agent_usage` (per Workflow agent) and `plan_usage_calc.py`
(account-wide, per call with its timestamp and 5-minute/1-hour cache-write split).

Transport. A call counts toward the Anthropic plan window only when its session ran on the host
transport. A session's transport is the sidecar spend ledger's `transport` for its `sessionId`
(SIDECAR_LEDGER_PATH overrides the ledger, here and in orchestration_metrics). A session the ledger
does not name is the host unless it served a non-`claude-` model, which only a proxy returns; it is
then `proxied`. A `claude-*` model name alone never proves Anthropic spend: a proxied child echoes
its own pin (gotcha_self_reported_model_identity_is_not_authority), which is why the ledger decides
first. A proxied session that has no ledger row and echoes a `claude-*` pin is counted as host;
the ledger is the only record that separates it.

    transcript_usage.py <transcript.jsonl>     print the per-call usage rows as JSON lines
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model_registry  # noqa: E402

HOST_TRANSPORT = "anthropic"
PROXIED = "proxied"
SYNTHETIC_MODEL = "<synthetic>"


def sidecar_ledger_path():
    return (os.environ.get("SIDECAR_LEDGER_PATH")
            or os.path.expanduser("~/.claude/sidecar_ledger.jsonl"))


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
    """One pass over a transcript's records.

    Returns `calls` (the last assistant record of each message id, in first-seen order; a record
    with no id falls back to its own uuid), `recs` (assistant records seen), the `models` and
    `efforts` those records name, and `first`/`last`: the datetimes spanning every timestamped record.
    """
    calls, order, recs = {}, [], 0
    models, efforts = set(), set()
    first = last = None
    for o in records:
        ts = o.get("timestamp")
        if ts:
            try:
                t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                first = t if first is None else min(first, t)
                last = t if last is None else max(last, t)
            except Exception:
                pass
        if o.get("type") != "assistant":
            continue
        recs += 1
        if isinstance(o.get("effort"), str) and o["effort"]:
            efforts.add(o["effort"])
        m = o.get("message")
        if not isinstance(m, dict):
            continue
        if isinstance(m.get("model"), str) and m["model"]:
            models.add(m["model"])
        mid = m.get("id") or o.get("uuid") or ("rec-%d" % recs)
        if mid not in calls:
            order.append(mid)
        calls[mid] = o
    return dict(calls=[(mid, calls[mid]) for mid in order], recs=recs, models=models,
                efforts=efforts, first=first, last=last)


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


def ledger_transports(path=None):
    """{sessionId: transport} from the sidecar spend ledger; {} when it is absent."""
    out = {}
    for rec in iter_records(path or sidecar_ledger_path()):
        sid, transport = rec.get("sessionId"), rec.get("transport")
        if sid and transport:
            out[str(sid)] = str(transport)
    return out


def session_transport(session, models, ledger):
    if session and session in ledger:
        return ledger[session]
    if any(not str(m).startswith("claude-") for m in models if m != SYNTHETIC_MODEL):
        return PROXIED
    return HOST_TRANSPORT


def _session_from_path(path):
    parts = os.path.normpath(path).split(os.sep)
    if "subagents" in parts:
        i = parts.index("subagents")
        return parts[i - 1] if i > 0 else None
    return os.path.splitext(parts[-1])[0]


def read_transcript(path, ledger=None, bad=None):
    """{path, session, transport, calls, models} for one transcript, JSONL stream or agent file."""
    red = reduce_records(iter_records(path, bad))
    calls = [call_usage(mid, rec) for mid, rec in red["calls"]]
    session = next((c["session"] for c in calls if c["session"]), None) or _session_from_path(path)
    ledger = ledger_transports() if ledger is None else ledger
    return dict(path=path, session=session, calls=calls, models=red["models"],
                transport=session_transport(session, red["models"], ledger))


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


def scan(root=None, since=None, ledger_path=None):
    """Host-transport calls across every transcript under `root` (default: this session's login).

    `since` skips files last modified before that epoch. A message id seen in several files (a
    resumed session copies its history) counts once, with its largest output. Returns `calls`,
    `excluded` ({session: transport} for non-host sessions), `files` scanned, and the malformed line
    count with the files that held them.
    """
    root = root or model_registry.session_projects_dir()
    ledger = ledger_transports(ledger_path)
    best, excluded, files, bad_lines, bad_files = {}, {}, 0, 0, []
    for path in transcript_files(root, since):
        files += 1
        bad = {}
        t = read_transcript(path, ledger, bad)
        if bad.get("lines") or bad.get("unreadable"):
            bad_lines += bad.get("lines", 0)
            bad_files.append(path)
        if not t["calls"]:
            continue
        if t["transport"] != HOST_TRANSPORT:
            excluded[t["session"]] = t["transport"]
            continue
        for c in t["calls"]:
            if c["model"] == SYNTHETIC_MODEL:
                continue
            prior = best.get(c["id"])
            if prior is None or (c["out"] or 0) > (prior["out"] or 0):
                best[c["id"]] = c
    return dict(calls=list(best.values()), excluded=excluded, files=files,
                unparseable_lines=bad_lines, unparseable_files=bad_files)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        sys.exit(2)
    for row in read_transcript(sys.argv[1])["calls"]:
        print(json.dumps(row))
