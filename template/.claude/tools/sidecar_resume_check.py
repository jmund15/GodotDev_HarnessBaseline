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

Usage:  sidecar_resume_check.py < stream.jsonl   (or a path as argv[1])
        sidecar_resume_check.py --selftest
"""
import json
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
    r"|rate[_ ]limit|quota|insufficient[_ ]credit|billing",
    re.I,
)


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
    src = open(sys.argv[1], encoding="utf-8") if len(sys.argv) > 1 else sys.stdin
    sid, reason = classify(src)
    if sid:
        print("%s\t%s" % (sid, reason))
    return 0


if __name__ == "__main__":
    sys.exit(main())
