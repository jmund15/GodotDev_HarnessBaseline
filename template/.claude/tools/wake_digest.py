"""What a watcher wake prints for each job that ended: its result head, report path and verification counts.

`watch_records.py` appends these lines under the `DONE`/`DIED`/`STOPPED` line of an ended job, so the next
message after a wake acts on them without a tail, cat or state-check call. One definition for every
dispatch kind: a sidecar record (`<record>.claims.json`), a fan-out job (`<label>.md` beside its record),
a Workflow run (its host record `<session>/workflows/<id>.json` and journal) and a background shell (its
`<task-notification>` row in the session transcript, parsed by `hooks/_background_tasks.py`, and the
output file it names). Every function reads and returns text; none writes.
"""
import calendar
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hooks"))
import _background_tasks  # noqa: E402
sys.path.insert(0, HERE)
import dispatch_result  # noqa: E402

# A head is what the orchestrator reads to decide whether to open the report: one screen line per field. The
# report path carries the rest.
HEAD_CHARS = 600
# A fan-out driver writes `<label>.md` after the child's `.exit` (measured 0.2-0.6 s later), and the watcher
# wakes on `.exit`; the digest waits this long for the report before reporting none.
DELIVERABLE_GRACE_SEC = 3.0
# A gate or test run prints its pass/fail summary in its last lines; the failure-marker lines cover a failure
# that scrolled above them.
TAIL_LINES = 8
MARKER_LINES = 8
# The output file is read from its end: a shell's tail never needs its first megabytes.
TAIL_BYTES = 65536
UNRATED_SHOWN = 12
# Twice the longest Bash timeout the harness sets (the session watcher's 7200000 ms): an older launch with no
# end row lost its notification, so it is no live work.
SHELL_LIVE_SECONDS = 4 * 3600
SHELL_CHUNK_BYTES = 8 << 20
SHELL_ROW_KEYS = (b"backgroundTaskId", b"watch_records", b"<task-notification>", b"Successfully stopped")
FAILURE_MARKER = re.compile(r"Traceback|\bfail(?:ed|ure|s)?\b|\berror\b|exception|\bfatal\b|\bpanic\b", re.IGNORECASE)
# A background shell that ran `watch_records.py` ends with one of its own terminal lines, then the client's
# `[exited with code N]` trailer. It reports itself.
WATCHER_TERMINALS = ("STILL RUNNING", "NONE", "COVERED by")
EXIT_TRAILER = re.compile(r"^\[exited with code -?\d+\]$")


def head(value, chars=HEAD_CHARS):
    """`value` on one line, cut at `chars` with the count of the characters left out."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = " ".join(text.split())
    return text if len(text) <= chars else "%s [+%d chars]" % (text[:chars], len(text) - chars)


def _json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _counts(counts):
    return ", ".join("%s %s" % item for item in counts.items()) if isinstance(counts, dict) and counts else ""


def _amend_line(brief, label, record, fanout_dir):
    paths = dispatch_result._sidecar_source(fanout_dir or record, label)
    if not brief or paths is None:
        return []
    try:
        with open(paths[1], encoding="utf-8", errors="replace") as fh:
            unread = dispatch_result.amend_unread(brief, fh.read())
    except OSError:
        return []
    return ["  AMEND UNREAD %s: the job never read this amendment; its work follows the brief alone" % unread] if unread else []


def sidecar_lines(label, record, fanout_dir=None, brief=None):
    """The indented digest lines of one ended sidecar job.

    A `brief` whose amend file the job's stream never read successfully adds an `AMEND UNREAD` line.

    The result is the record's `.claims.json` `result`, else the head of the fan-out deliverable
    `<label>.md` written since this attempt launched (its `<record>.pid`). Verification is the claims file's `verification.status` with its counts, `UNCHECKED`
    when the file or its verdict is absent. The report is the deliverable when one exists, else the claims file."""
    claims_path = str(record) + ".claims.json"
    claims = _json(claims_path)
    deliverable = str(dispatch_result.deliverable_path(fanout_dir, label)) if fanout_dir else None
    try:
        launched = os.path.getmtime(str(record) + ".pid")
    except OSError:
        launched = None

    def current(path):
        try:
            return os.path.getmtime(path) >= (launched or 0)
        except OSError:
            return False

    if deliverable and claims is None:
        deadline = time.monotonic() + DELIVERABLE_GRACE_SEC
        while not current(deliverable) and time.monotonic() < deadline:
            time.sleep(0.1)
    deliverable = deliverable if deliverable and current(deliverable) else None
    result = (claims or {}).get("result")
    if result is None and deliverable:
        try:
            with open(deliverable, encoding="utf-8", errors="replace") as fh:
                result = fh.read(HEAD_CHARS * 4)
        except OSError:
            pass
    missing = "no result in %s" if claims is not None else "no %s"
    lines = ["  result: %s" % head(result) if result not in (None, "") else
             "  result: none (%s and no deliverable)" % (missing % os.path.basename(claims_path))]
    nested = (claims or {}).get("verification")
    verdict = nested if isinstance(nested, dict) and "status" in nested else (claims or {})
    status = verdict.get("status")
    if isinstance(status, str) and status != "unavailable":
        counts = _counts(verdict.get("counts"))
        lines.append("  verification: %s%s" % (status, " (%s)" % counts if counts else ""))
    else:
        lines.append("  verification: UNCHECKED (%s)" % ("no claims verdict" if claims is not None
                                                          else "no %s" % os.path.basename(claims_path)))
    report = deliverable or (claims_path if claims is not None else None)
    if report:
        lines.append("  report: %s" % report)
    return lines + _amend_line(brief, label, record, fanout_dir)


def workflow_lines(host, events):
    """The indented digest lines of one ended Workflow run.

    `host` is the run's host record dict (`status`, `result`, `args`, `durationMs`) and `events` the journal
    events of `watch_records.read_journal`. The result is the host record's; a run whose host record holds none
    shows each journal `result` row's agent label instead. Verification is the result's `counts` when it
    carries them. The report is the run's `spillDir`, from its result or args."""
    host = host or {}
    result, lines = host.get("result"), []
    parsed = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except ValueError:
            parsed = result
    failed = sum(1 for e in events if e.get("type") == "failed")
    if result not in (None, ""):
        lines.append("  result: %s" % head(parsed if not isinstance(parsed, dict) else
                                           {k: v for k, v in parsed.items() if k != "counts"}))
    else:
        labels = {e["agentId"]: e.get("label") for e in events if e.get("type") == "started" and e.get("agentId")}
        done = [labels.get(e.get("agentId")) or e.get("agentId") for e in events if e.get("type") == "result"]
        lines.append("  result: none on the host record; %d agent result(s) in the journal%s"
                     % (len(done), ": " + ", ".join(map(str, done[:UNRATED_SHOWN])) if done else ""))
    if failed:
        lines.append("  failed agents: %d (journal `failed` rows)" % failed)
    counts = _counts(parsed.get("counts")) if isinstance(parsed, dict) else ""
    if counts:
        lines.append("  counts: %s" % counts)
    spill = None
    for source in (parsed, host.get("args")):
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except ValueError:
                continue
        value = source.get("spillDir") if isinstance(source, dict) else None
        if isinstance(value, str) and value.strip():
            spill = value
            break
    if spill:
        lines.append("  report: %s" % spill)
    return lines


def transcript_path(session_id):
    """The session's transcript `<projects>/<project>/<session>.jsonl`, else None."""
    import model_registry
    found = glob.glob(os.path.join(glob.escape(str(model_registry.session_projects_dir())), "*",
                                   "%s.jsonl" % session_id))
    return found[0] if found else None


def new_shell_scan():
    return {"offset": 0, "launched": {}, "ended": set(), "watchers": set(), "notices": []}


def _epoch(stamp):
    try:
        return calendar.timegm(time.strptime(str(stamp)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _scan_row(raw, pos, scan):
    if not any(key in raw for key in SHELL_ROW_KEYS):
        return
    try:
        row = json.loads(raw)
    except ValueError:
        return
    if not isinstance(row, dict):
        return
    if b"<task-notification>" in raw:
        seen = set()
        for notice in _background_tasks.ended_notices(row):
            if notice["task_id"] not in seen:
                seen.add(notice["task_id"])
                scan["notices"].append((pos, notice))
                scan["ended"].add(notice["task_id"])
    if row.get("isSidechain"):
        return
    result = row.get("toolUseResult")
    if isinstance(result, dict) and result.get("task_id") and str(result.get("message", "")).startswith("Successfully stopped"):
        scan["ended"].add(result["task_id"])
    content = (row.get("message") or {}).get("content")
    for item in content if isinstance(content, list) else ():
        if not isinstance(item, dict):
            continue
        if item.get("type") == "tool_use" and "watch_records" in str((item.get("input") or {}).get("command", "")):
            scan["watchers"].add(item.get("id"))
        elif item.get("type") == "tool_result":
            task = result.get("backgroundTaskId") if isinstance(result, dict) else None
            if task:
                scan["launched"][task] = (item.get("tool_use_id"), _epoch(row.get("timestamp")))


def advance_shell_scan(path, scan):
    """Parse the transcript bytes after `scan["offset"]` once, in bounded chunks of whole lines: ended notices (with
    their byte position), TaskStop results, launched background tasks and watcher launches. Returns the file size,
    None when it cannot be read."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if scan["offset"] > size:
        scan.update(new_shell_scan())
    chunk = SHELL_CHUNK_BYTES
    with open(path, "rb") as fh:
        while scan["offset"] < size:
            fh.seek(scan["offset"])
            data = fh.read(chunk)
            end = data.rfind(b"\n") + 1
            if end == 0:
                if len(data) < chunk:
                    break
                chunk *= 2
                continue
            pos = scan["offset"]
            for raw in data[:end].splitlines(keepends=True):
                _scan_row(raw, pos, scan)
                pos += len(raw)
            scan["offset"] += end
    return size


def shell_notices(path, scan, offset):
    """(ended shell notices whose row starts at or after byte `offset`, the offset to resume from).

    Reads only whole lines, so a row still being written is read on the next call. An offset past the file's
    end (a replaced transcript) restarts at its end. A watcher's own shell is not a notice."""
    size = advance_shell_scan(path, scan)
    if size is None:
        return [], offset
    if offset is None or offset > size:
        return [], size
    found = []
    for pos, notice in scan["notices"]:
        if pos >= offset and not any(n["task_id"] == notice["task_id"] for n in found):
            found.append(notice)
    return [n for n in found if not _is_watcher(n)], scan["offset"]


def running_shells(path, scan, now=None):
    """The ids of this session's running Bash/PowerShell background tasks. A task is running once its launch row
    (a main-session `toolUseResult.backgroundTaskId`) has no `<task-notification>` and no successful TaskStop
    result naming it, and was launched within `SHELL_LIVE_SECONDS`. A watcher's own shell is not work."""
    if advance_shell_scan(path, scan) is None:
        return set()
    now = time.time() if now is None else now
    return {task for task, (use, when) in scan["launched"].items()
            if task not in scan["ended"] and use not in scan["watchers"]
            and (when is None or now - when <= SHELL_LIVE_SECONDS)}


def _tail_text(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(fh.tell() - TAIL_BYTES, 0))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def _is_watcher(notice):
    text = _tail_text(notice.get("output_file") or "")
    last = next((l.strip() for l in reversed((text or "").splitlines())
                 if l.strip() and not EXIT_TRAILER.match(l.strip())), "")
    return last.startswith(WATCHER_TERMINALS[0]) or last == WATCHER_TERMINALS[1] or last.startswith(WATCHER_TERMINALS[2])


def shell_text(notice):
    """`SHELL <id> <status> exit=<n> <summary>` then the output path, failure-marker lines and the tail."""
    code = notice.get("exit_code")
    out = ["SHELL %s %s exit=%s %s" % (notice["task_id"], notice.get("status") or "?",
                                       code if code is not None else "?", head(notice.get("summary"), 160))]
    path = notice.get("output_file")
    text = _tail_text(path) if path else None
    if text is None:
        out.append("  output: unreadable%s" % (" (%s)" % path if path else ""))
        return "\n".join(out)
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    out.append("  output: %s" % path)
    tail = lines[-TAIL_LINES:]
    markers = [l for l in lines[:-TAIL_LINES] if FAILURE_MARKER.search(l)][-MARKER_LINES:]
    if markers:
        out.append("  failure lines above the tail:")
        out.extend("    " + head(l, 240) for l in markers)
    out.append("  tail:")
    out.extend("    " + head(l, 240) for l in tail)
    return "\n".join(out)


def unrated_line(session_id):
    """`UNRATED <n>: <label>, ...` for the session's dispatches awaiting a rating; None when none are, and an
    `UNRATED unknown` line when the count cannot be read (an unknown total is not a clean one)."""
    try:
        import orchestration_metrics as om
        session_dir = om.find_session_dir(session_id)
        if not session_dir:
            return None
        detail = om.pending_detail(session_dir, True, priced=False)
    except Exception as exc:
        return "UNRATED unknown (%s: %s)" % (type(exc).__name__, exc)
    if detail.get("sidecar_error"):
        return "UNRATED unknown (sidecar source unreadable: %s)" % detail["sidecar_error"]
    pending = detail["remaining"]
    if not pending:
        return None
    shown = ", ".join("%s:%s" % pair for pair in pending[:UNRATED_SHOWN])
    more = " +%d more" % (len(pending) - UNRATED_SHOWN) if len(pending) > UNRATED_SHOWN else ""
    return ("UNRATED %d: %s%s. Rate every consumed one in one `orchestration_metrics.py --rate-batch` call "
            "(`/orchestration_metrics` §Incremental rating)." % (len(pending), shown, more))
