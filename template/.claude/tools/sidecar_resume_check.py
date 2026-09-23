#!/usr/bin/env python3
"""Decide whether a finished `claude -p` sidecar run should be RESUMED, and say why.

Supersedes `sidecar_compact_end.py`, which detected one of the two resumable endings. Prints
`<session_id>\\t<reason>` when the run should be resumed, and nothing otherwise.

RESUME IS NOT RETRY, and the distinction is what keeps this inside the standing rule against
auto-retrying a billed call. `--resume <sid>` continues the same session: every turn already paid
for is kept, and the run picks up where it stopped (measured — a 91-turn lens killed by a mid-stream
reset resumed and finished at 58 further turns, exit 0). A fresh dispatch would re-buy all of it.
So this tool NEVER re-issues a run from the start; with no session id there is nothing to resume and
it stays silent.

Two resumable endings:

  compaction  an auto-compaction ends a -p run — the child answers the continuation summary and the
              harness closes the turn. `compact_boundary`, no tool call after it, result opens with
              <analysis>/<summary>.
  transient   the upstream stream aborted mid-flight. The proxy hands the child an api_error whose
              message names a connection-level fault. The work done so far is intact in the session.

Deliberately NOT resumable — each would loop or re-buy, so they exit silent:
  * deterministic request faults (unknown model, malformed request, 400/404) — identical on resume
  * auth/permission faults (401/403) — a resume repeats the rejection
  * quota / rate limits — resumable in principle, but needs backoff this tool does not implement
  * any run with no session id

The same module owns the live stream-event classification the launcher watchdog and
`sidecar_fanout.py --status` share: which events are work, which are retries, and which result is a
provider usage limit.

Usage:  sidecar_resume_check.py < stream.jsonl   (or a path as argv[1])
        sidecar_resume_check.py --watch <path|-> <offset> <run> <limit>
            fold the complete lines after byte <offset>; prints
            <new_offset>\\t<work 0|1>\\t<429 run>\\t<usage_limited 0|1>\\t<resets_at>\\t<session_id>
        sidecar_resume_check.py --selftest
"""
import json
import os
import re
import sys

# Connection-level signatures observed from the codex proxy. Each names a transport fault, never a
# rejection of the request itself — that is the property that makes a resume safe.
TRANSIENT = re.compile(
    r"websocket stream error"
    r"|connection reset without closing handshake"
    r"|forcibly closed by the remote host"
    r"|os error 10054"
    r"|connection reset by peer"
    r"|broken pipe"
    r"|stream (?:closed|ended) unexpectedly"
    r"|upstream connect error"
    r"|502 bad gateway"
    r"|503 service unavailable"
    r"|504 gateway timeout",
    re.I,
)

# Checked FIRST. A message matching these is deterministic or needs backoff; resuming loops.
NEVER_RESUME = re.compile(
    r"unknown model"
    r"|invalid[_ ]request"
    r"|authentication|unauthorized|permission denied|forbidden"
    r"|\b40[0134]\b"
    r"|rate[_ ]limit|quota|insufficient[_ ]credit|billing"
    r"|usage[ _-]?limit|\b429\b",
    re.I,
)

USAGE_LIMIT = re.compile(r"(?:usage|weekly)[ _-]?limit", re.I)
USAGE_LIMIT_REASON = "provider-usage-limit"


def _events(lines):
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            yield d


def event_kind(d):
    """-> result | retry | system | work. Only work and results count as progress."""
    t = d.get("type")
    if t == "result":
        return "result"
    if t == "system":
        return "retry" if d.get("subtype") == "api_retry" else "system"
    if t == "rate_limit_event":
        return "system"
    return "work"


def is_usage_limit_result(d):
    if d.get("type") != "result":
        return False
    if d.get("terminal_reason") == USAGE_LIMIT_REASON:
        return True
    text = d.get("result")
    return bool(d.get("is_error")) and isinstance(text, str) and bool(USAGE_LIMIT.search(text))


def reset_epoch(d):
    """Epoch seconds a rejected rate-limit window resets at, when a rate_limit_event names one."""
    info = d.get("rate_limit_info") if d.get("type") == "rate_limit_event" else None
    if not isinstance(info, dict):
        return None
    candidates = []
    if info.get("status") == "rejected":
        candidates.append(info.get("resetsAt"))
    for window in (info.get("unifiedWindows") or {}).values():
        if isinstance(window, dict) and (window.get("utilization") or 0) >= 1:
            candidates.append(window.get("resetsAt"))
    found = [int(v) for v in candidates if isinstance(v, (int, float)) and v > 0]
    return max(found) if found else None


def watch(lines, run=0, limit=10):
    """Fold stream lines -> (work_seen, 429_run, usage_limited, resets_at, session_id).

    `run` counts consecutive 429 api_retry lines with no work, result or other retry between them.
    """
    work, limited, resets, sid = False, False, None, None
    for d in _events(lines):
        sid = d.get("session_id") or sid
        kind = event_kind(d)
        if kind == "retry":
            run = run + 1 if d.get("error_status") == 429 else 0
        elif kind == "result":
            run = 0
            if is_usage_limit_result(d):
                limited = True
            else:
                work = True
        elif kind == "work":
            run, work = 0, True
        resets = reset_epoch(d) or resets
        if limit > 0 and run >= limit:
            limited = True
    return work, run, limited, resets, sid


def _watch_cli(args):
    if len(args) != 4:
        return 2
    source, offset, run, limit = args[0], int(args[1] or 0), int(args[2] or 0), int(args[3] or 0)
    if source == "-":
        chunk, consumed = sys.stdin.read(), offset
        lines = chunk.splitlines()
    else:
        try:
            with open(source, "rb") as fh:
                fh.seek(offset)
                raw = fh.read()
        except OSError:
            raw = b""
        end = raw.rfind(b"\n") + 1
        consumed = offset + end
        lines = raw[:end].decode("utf-8", "replace").splitlines()
    work, run, limited, resets, sid = watch(lines, run, limit)
    print("%d\t%d\t%d\t%d\t%s\t%s" % (consumed, work, run, limited, resets or "", sid or ""))
    return 0


def stream_status(path, now, stall_sec=900, limit=10):
    """-> (state, detail) for a job's -P stream: working | retrying | stalled | finished | usage-limit."""
    try:
        size, mtime = os.path.getsize(path), os.path.getmtime(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - 262144))
            raw = fh.read().decode("utf-8", "replace")
    except OSError as err:
        return "stalled", "stream unreadable: %s" % err
    lines = raw.splitlines()
    if size > 262144 and lines:
        lines = lines[1:]
    events = list(_events(lines))
    age = int(now - mtime)
    if len(events) == 1 and "seeAlso" in events[0]:
        return "finished", "stream bounded after exit; see %s" % events[0]["seeAlso"]
    if events and event_kind(events[-1]) == "result":
        last = events[-1]
        if is_usage_limit_result(last):
            return "usage-limit", "terminal usage-limit result"
        if last.get("terminal_reason") == "stall":
            return "stalled", "the watchdog killed the child"
        return "finished", "result %s" % (last.get("terminal_reason") or last.get("subtype") or "")
    last_work = max((i for i, d in enumerate(events) if event_kind(d) == "work"), default=-1)
    trailing = events[last_work + 1:]
    retries = [d for d in trailing if event_kind(d) == "retry"]
    run = 0
    for d in retries:
        run = run + 1 if d.get("error_status") == 429 else 0
    if limit > 0 and run >= limit:
        return "usage-limit", "%d consecutive 429 retries with no work" % run
    if age >= stall_sec:
        return "stalled", "no write for %ds (limit %ds)" % (age, stall_sec)
    if retries:
        return "retrying", "%d retries since the last work event (last status %s), last write %ds ago" % (
            len(retries), retries[-1].get("error_status"), age)
    return "working", "last write %ds ago" % age


def classify(stream):
    """-> (session_id, reason) or (None, None). `stream` is an iterable of raw jsonl lines."""
    compacted = False
    work_after = 0
    sid = None
    result_text = ""
    is_error = False
    terminal = None
    saw_result = False

    for line in stream:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        if d.get("session_id") and not sid:
            sid = d["session_id"]
        if t == "system" and d.get("subtype") == "compact_boundary":
            compacted = True
            work_after = 0
        elif t == "assistant" and compacted:
            for c in d.get("message", {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    work_after += 1
        elif t == "result":
            saw_result = True
            sid = d.get("session_id") or sid
            r = d.get("result", "")
            result_text = r if isinstance(r, str) else ""
            is_error = bool(d.get("is_error"))
            terminal = d.get("terminal_reason")

    if not saw_result or not sid:
        return None, None

    if NEVER_RESUME.search(result_text):
        return None, None

    if is_error and TRANSIENT.search(result_text):
        return sid, "transient"

    if compacted and work_after == 0:
        head = result_text.lstrip()[:40].lower()
        if head.startswith("<analysis>") or head.startswith("<summary>"):
            return sid, "compaction"

    # An api_error with no recognized signature is reported so the caller can widen the pattern
    # deliberately rather than silently losing the run.
    if is_error and terminal == "api_error":
        return None, None

    return None, None


def _selftest() -> int:
    """Every branch must be shown FIRING and shown STAYING SILENT. A detector that cannot be made to
    fire and a detector that matches nothing emit the same output."""
    def run(lines):
        return classify(lines)

    R = lambda **kw: json.dumps({"type": "result", "session_id": "S1", **kw})
    ok = True
    cases = [
        ("transient fires", [R(is_error=True, terminal_reason="api_error",
                               result="API Error: WebSocket stream error: WebSocket protocol error: "
                                      "Connection reset without closing handshake")], ("S1", "transient")),
        ("transient 10054", [R(is_error=True, terminal_reason="api_error",
                               result="WebSocket stream error: IO error: An existing connection was "
                                      "forcibly closed by the remote host. (os error 10054)")], ("S1", "transient")),
        ("compaction fires", [json.dumps({"type": "system", "subtype": "compact_boundary"}),
                              R(result="<analysis>x</analysis><summary>y</summary>")], ("S1", "compaction")),
        ("clean run silent", [R(result="done, wrote the report")], (None, None)),
        ("unknown model silent", [R(is_error=True, terminal_reason="api_error",
                                    result='Unknown model "gpt-9.9-nope". Supported: ...')], (None, None)),
        ("auth failure silent", [R(is_error=True, terminal_reason="api_error",
                                   result="401 Unauthorized: authentication failed")], (None, None)),
        ("rate limit silent", [R(is_error=True, terminal_reason="api_error",
                                 result="rate_limit exceeded, retry later")], (None, None)),
        ("usage limit silent", [R(is_error=True, terminal_reason="api_error", api_error_status=429,
                                  result="API Error: Request rejected (429) · The usage limit has been reached")],
         (None, None)),
        ("no session silent", [json.dumps({"type": "result", "is_error": True,
                                           "result": "WebSocket stream error"})], (None, None)),
        ("compaction+work silent", [json.dumps({"type": "system", "subtype": "compact_boundary"}),
                                    json.dumps({"type": "assistant", "message": {"content": [
                                        {"type": "tool_use", "name": "Read", "input": {}}]}}),
                                    R(result="<summary>y</summary>")], (None, None)),
    ]
    for name, lines, want in cases:
        got = run(lines)
        if got != want:
            print("SELFTEST FAIL: %-24s want=%s got=%s" % (name, want, got))
            ok = False
    print("sidecar_resume_check SELFTEST: %s — %d cases, every branch fired and stayed silent"
          % ("PASS" if ok else "FAIL", len(cases)))
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    if len(sys.argv) > 1 and sys.argv[1] == "--watch":
        return _watch_cli(sys.argv[2:])
    src = open(sys.argv[1], encoding="utf-8") if len(sys.argv) > 1 else sys.stdin
    sid, reason = classify(src)
    if sid:
        print("%s\t%s" % (sid, reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
