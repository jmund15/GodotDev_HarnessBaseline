"""What a watcher wake prints for each job that ended: its result head, report path and verification counts.

`watch_records.py` appends these lines under the `DONE`/`DIED`/`STOPPED` line of an ended job, so the next
message after a wake acts on them without a tail, cat or state-check call. One definition for every
dispatch kind: a sidecar record (`<record>.claims.json`), a fan-out job (`<label>.md` beside its record),
a Workflow run (its host record `<session>/workflows/<id>.json` and journal) and a background shell (its
`<task-notification>` row in the session transcript, parsed by `hooks/_background_tasks.py`, and the
output file it names). Every function reads and returns text; none writes.
"""
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hooks"))
import _background_tasks  # noqa: E402

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


def sidecar_lines(label, record, fanout_dir=None):
    """The indented digest lines of one ended sidecar job.

    The result is the record's `.claims.json` `result`, else the head of the fan-out deliverable
    `<label>.md`. Verification is the claims file's `verification.status` with its counts, `UNCHECKED`
    when the file or its verdict is absent. The report is the deliverable when one exists, else the claims file."""
    claims_path = str(record) + ".claims.json"
    claims = _json(claims_path)
    deliverable = os.path.join(str(fanout_dir), "%s.md" % label) if fanout_dir else None
    if deliverable and claims is None:
        deadline = time.monotonic() + DELIVERABLE_GRACE_SEC
        while not os.path.isfile(deliverable) and time.monotonic() < deadline:
            time.sleep(0.1)
    deliverable = deliverable if deliverable and os.path.isfile(deliverable) else None
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
    return lines


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


def shell_notices(path, offset):
    """(ended shell notices appended after byte `offset`, the offset to resume from).

    Reads only whole lines, so a row still being written is read on the next call. An offset past the file's
    end (a replaced transcript) restarts at its end. A watcher's own shell is not a notice."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if offset is None or offset > size:
        return [], size
    notices = []
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read()
    end = data.rfind(b"\n") + 1
    for raw in data[:end].splitlines():
        if b"<task-notification>" not in raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        for notice in _background_tasks.ended_notices(row):
            if not any(n["task_id"] == notice["task_id"] for n in notices):
                notices.append(notice)
    return [n for n in notices if not _is_watcher(n)], offset + end


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
