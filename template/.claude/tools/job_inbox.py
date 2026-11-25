#!/usr/bin/env python3
"""Send a running job a message it reads mid-run: `job_inbox.py send <label | agent id> (--text <text> | --file <path>)`.

`SendMessage` cannot resume a running Workflow job (the project denies the Agent tool), and a sidecar child registers
no inbox. This tool appends a stamped block to `.claude/scratch/inbox/<key>.md`; `hooks/job_inbox_deliver.py`
(PostToolUse) injects the new bytes into the job's context on its next tool call. Prints the path written, then one
line naming how the key resolved.

Key: an agent id (`a` and hex) is its own key. A label resolves, in order, to
  native   the `agentId` of its newest `started` row in this session's workflow journals
           (`<session dir>/subagents/workflows/*/journal.jsonl`; the journal written last wins, its last row within);
  sidecar  the child's session id, read from the fan-out record of this session's pending dispatch entry for the
           label (`<label>.record.json`, written when the child ends) or, while it runs, its `-P` stream's first
           `session_id`;
  label    `label-<label>` with each character outside [A-Za-z0-9._-] replaced by `_`, the file a seat outside
           Claude Code reads itself (`guards/common.md`). No hook delivers it: the resolution line says so.
The session is CLAUDE_CODE_SESSION_ID, or `--session`.

Proof: tests/test_job_inbox.py.
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "hooks"))
import job_inbox_deliver  # noqa: E402

_AGENT_ID = re.compile(r"^a[0-9a-f]{8,}$")
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def label_key(label):
    return "label-" + _UNSAFE.sub("_", label)


def _native_agent(label, session_id, roots):
    import model_registry
    transcript = model_registry.find_session_transcript(session_id, roots=roots, allow_dir=True)
    if not transcript:
        return None
    newest, agent = -1.0, None
    for journal in (Path(transcript).with_suffix("") / "subagents" / "workflows").glob("*/journal.jsonl"):
        try:
            stamp = journal.stat().st_mtime
            with open(journal, encoding="utf-8", errors="replace") as fh:
                rows = [json.loads(line) for line in fh if '"started"' in line]
        except (OSError, ValueError):
            continue
        found = [row.get("agentId") for row in rows
                 if isinstance(row, dict) and row.get("type") == "started" and row.get("label") == label]
        if found and found[-1] and stamp > newest:
            newest, agent = stamp, found[-1]
    return agent


def _stream_session(stream):
    try:
        with open(stream, encoding="utf-8", errors="replace") as fh:
            for _ in range(50):
                line = fh.readline()
                if not line:
                    break
                if '"session_id"' in line:
                    try:
                        sid = json.loads(line).get("session_id")
                    except ValueError:
                        continue
                    if sid:
                        return str(sid)
    except OSError:
        pass
    return None


def _sidecar_session(label, session_id):
    import _hook_state
    import watch_records
    pending = _hook_state.read_json_salvage(_hook_state.state_path(session_id)).get("dispatch_table_pending") or []
    entries = [e for e in pending if isinstance(e, dict) and e.get("record")
               and any(isinstance(j, dict) and j.get("label") == label for j in e.get("jobs") or [])]
    for entry in sorted(entries, key=lambda e: e.get("launched_at") or 0, reverse=True):
        record = entry["record"]
        try:
            with open(record, encoding="utf-8") as fh:
                sid = (json.load(fh) or {}).get("sessionId")
        except (OSError, ValueError, AttributeError):
            sid = None
        stream = watch_records.record_stream(record)
        sid = sid or (_stream_session(stream) if stream else None)
        if sid:
            return str(sid)
    return None


def resolve_key(target, session_id, roots=None):
    """(key, kind) for a label or agent id; kind is native, sidecar or label (`resolve` in the module docstring)."""
    if _AGENT_ID.match(target):
        return target, "native"
    if session_id:
        agent = _native_agent(target, session_id, roots)
        if agent:
            return agent, "native"
        child = _sidecar_session(target, session_id)
        if child:
            return child, "sidecar"
    return label_key(target), "label"


def send(root, key, text):
    """Append one stamped block to the inbox of `key` under `root`'s checkout; returns the file's path."""
    directory = job_inbox_deliver.inbox_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (key + ".md")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    block = "\n## %s from the dispatcher\n\n%s\n\n" % (stamp, text.strip())
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, block.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def _kind_line(kind, key):
    if kind == "label":
        return "resolved: label fallback %s; no hook delivers it, only a seat that reads this file" % key
    return "resolved: %s job %s; delivered on its next tool call" % (kind, key)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    send_parser = sub.add_parser("send")
    send_parser.add_argument("target", help="a job label or an agent id")
    send_parser.add_argument("--text")
    send_parser.add_argument("--file")
    send_parser.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    args = parser.parse_args(argv)
    if (args.text is None) == (args.file is None):
        print("job_inbox: give exactly one of --text or --file", file=sys.stderr)
        return 2
    try:
        text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as err:
        print("job_inbox: cannot read %s: %s" % (args.file, err), file=sys.stderr)
        return 2
    if not text.strip():
        print("job_inbox: the message is empty", file=sys.stderr)
        return 2
    key, kind = resolve_key(args.target, args.session)
    path = send(job_inbox_deliver.project_root(), key, text)
    print(str(path).replace("\\", "/"))
    print(_kind_line(kind, key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
