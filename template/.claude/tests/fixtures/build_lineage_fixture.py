"""Build the committed TaskStop-lineage fixture from real transcripts, keeping only the fields the guard reads.

    python build_lineage_fixture.py <out dir> <task id> <transcript>...

The fixture publishes to every project, so no row is copied whole: each output row is rebuilt from a field whitelist
and every free-text field is replaced. Per input file `<session id>[.extract].jsonl` the output `<out dir>/<session id>.jsonl` holds:

- each `derived-from` row (`type`, `v`, `sessionId`, and each session's `type`, `id`, `via`);
- the file's first top-level timestamped row, reduced to `type`, `isSidechain`, `timestamp` (the `until` bound of the
  walk);
- the launch of the task: its `tool_use` row (`id`, `name`, `command` replaced by a placeholder) and its `tool_result`
  row (`tool_use_id`, `toolUseResult.backgroundTaskId`, `taskId` or `agentId`+`isAsync`, a generic content line);
- each `enqueue` notice row naming the task, rebuilt as a `<task-notification>` with its ids, `status`, an
  `<output-file>` of `tasks/<id>.output` and a generic `<summary>` (the real description and path hold the home
  directory).

Real session and task ids stay: they are what the walk follows. Tests: `tests/floor/test_floor_kill.py`.
"""
import json
import os
import re
import sys
from datetime import datetime

NOTICE = re.compile(r"<task-notification>(.*?)</task-notification>", re.S)


def stamp(row):
    value = row.get("timestamp")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return value


def tag(block, name):
    found = re.search(r"<%s>(.*?)</%s>" % (name, name), block, re.S)
    return found.group(1).strip() if found else ""


def launched_id(row):
    result = row.get("toolUseResult")
    if not isinstance(result, dict):
        return None
    return result.get("backgroundTaskId") or result.get("taskId") or (
        result.get("agentId") if result.get("isAsync") is True else None) or None


def blocks_of(row):
    content = (row.get("message") or {}).get("content") if isinstance(row.get("message"), dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def read_rows(path):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def launch_use_id(rows, task):
    for row in rows:
        if row.get("isSidechain") or launched_id(row) != task:
            continue
        for block in blocks_of(row):
            if block.get("type") == "tool_result":
                return block.get("tool_use_id")
    return None


def launch_rows(row, task, use_id):
    base = {"type": row.get("type"), "isSidechain": bool(row.get("isSidechain")), "timestamp": row.get("timestamp")}
    out = []
    for block in blocks_of(row):
        if block.get("type") == "tool_use" and block.get("id") == use_id:
            use = {"type": "tool_use", "id": use_id, "name": block.get("name"), "input": {"command": "<command>"}}
            out.append({**base, "message": {"role": "assistant", "content": [use]}})
        elif block.get("type") == "tool_result" and block.get("tool_use_id") == use_id and launched_id(row) == task:
            keep = {k: v for k, v in row["toolUseResult"].items() if k in ("backgroundTaskId", "taskId", "agentId", "isAsync")}
            result = {"type": "tool_result", "tool_use_id": use_id, "content": "Command running in background with ID: %s." % task}
            out.append({**base, "message": {"role": "user", "content": [result]}, "toolUseResult": keep})
    return out


def notice_row(row, task):
    content = row.get("content")
    if row.get("type") != "queue-operation" or row.get("operation") != "enqueue" or not isinstance(content, str):
        return None
    kept = []
    for block in NOTICE.findall(content):
        if tag(block, "task-id") != task:
            continue
        status = tag(block, "status")
        kept.append("<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>%s</tool-use-id>\n"
                    "<output-file>tasks/%s.output</output-file>\n<status>%s</status>\n"
                    "<summary>Background task %s</summary>\n</task-notification>"
                    % (task, tag(block, "tool-use-id"), task, status, status))
    if not kept:
        return None
    return {"type": "queue-operation", "operation": "enqueue", "timestamp": row.get("timestamp"), "content": "\n".join(kept)}


def derived_row(row):
    sessions = row.get("sessions") if isinstance(row.get("sessions"), list) else []
    return {"type": "derived-from", "v": row.get("v"), "sessionId": row.get("sessionId"),
            "sessions": [{"type": s.get("type"), "id": s.get("id"), "via": s.get("via")} for s in sessions if isinstance(s, dict)]}


def build(path, task):
    rows = list(read_rows(path))
    use_id = launch_use_id(rows, task)
    out, first_done = [], False
    for row in rows:
        launched = launch_rows(row, task, use_id) if use_id and not row.get("isSidechain") else []
        notice = notice_row(row, task)
        if row.get("type") == "derived-from":
            out.append(derived_row(row))
        elif launched:
            out += launched
        elif notice:
            out.append(notice)
        elif not first_done and stamp(row):
            out.append({"type": row.get("type"), "isSidechain": bool(row.get("isSidechain")), "timestamp": row["timestamp"]})
        if not first_done and stamp(row):
            first_done = True
    return out


def main(argv):
    folder, task, paths = argv[1], argv[2], argv[3:]
    os.makedirs(folder, exist_ok=True)
    for path in paths:
        session = os.path.basename(path).split(".")[0]
        rows = build(path, task)
        with open(os.path.join(folder, session + ".jsonl"), "w", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows))
        print("%s: %d rows" % (session, len(rows)))


if __name__ == "__main__":
    main(sys.argv)
