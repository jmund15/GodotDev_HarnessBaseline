"""How the hooks read a session's background tasks from a transcript: one definition for kill_guard,
foreground_wait_guard and compact_directive_ledger.

A launch is a tool_result row whose `toolUseResult` carries `backgroundTaskId` (Bash, PowerShell), `taskId`
(Monitor, Workflow) or `agentId` with `isAsync` true (a background Agent; a foreground one also carries an
`agentId`). Result text that merely quotes the launch wording is not a launch. A task ended once a `<task-id>`
notification names it."""
import re

ENDED_RE = re.compile(r"<task-id>(\w+)</task-id>")


NOTICE_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.DOTALL)
EXIT_CODE_RE = re.compile(r"exit code (-?\d+)")


def _tag(block, name):
    found = re.search(r"<%s>(.*?)</%s>" % (name, name), block, re.DOTALL)
    return found.group(1).strip() if found else None


def ended_notices(row):
    """The `<task-notification>` blocks a transcript row's `content` carries, each as `{task_id, output_file,
    status, summary, exit_code}` (`exit_code` an int from the summary's `exit code N` (`completed (exit code 0)`, `failed with exit code 1`), else None)."""
    content = row.get("content") if isinstance(row, dict) else None
    out = []
    for block in NOTICE_RE.findall(content if isinstance(content, str) else ""):
        task_id = _tag(block, "task-id")
        if not task_id:
            continue
        summary = _tag(block, "summary")
        code = EXIT_CODE_RE.search(summary or "")
        out.append({"task_id": task_id, "output_file": _tag(block, "output-file"), "status": _tag(block, "status"),
                    "summary": summary, "exit_code": int(code.group(1)) if code else None})
    return out


def launched_task_id(row):
    """The background task id a transcript row's `toolUseResult` reports, else None."""
    result = row.get("toolUseResult") if isinstance(row, dict) else None
    if not isinstance(result, dict):
        return None
    return (result.get("backgroundTaskId") or result.get("taskId")
            or (result.get("agentId") if result.get("isAsync") is True else None) or None)
