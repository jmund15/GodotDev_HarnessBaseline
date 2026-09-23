#!/usr/bin/env python3
"""Proof: `tools/sidecar_resume_check.py` classifies live stream events for the launcher watchdog
and `sidecar_fanout.py --status`, and its resume classifier still passes its own selftest.

Run: python3 .claude/tests/test_sidecar_resume_check.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "tools", "sidecar_resume_check.py")
spec = importlib.util.spec_from_file_location("sidecar_resume_check_probe", TOOL)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

TMP = tempfile.mkdtemp(prefix="resume_check_")


def ev(**fields):
    return json.dumps(fields)


WORK = ev(type="assistant", session_id="s1")
RETRY_429 = ev(type="system", subtype="api_retry", error_status=429, session_id="s1")
RETRY_500 = ev(type="system", subtype="api_retry", error_status=500)
LIMIT_RESULT = ev(type="result", is_error=True, terminal_reason="api_error",
                  result="API Error: 429 · The usage limit has been reached", session_id="s1")
WEEKLY_LIMIT_RESULT = ev(type="result", is_error=True, terminal_reason="api_error",
                         api_error_status=429,
                         result="You've hit your weekly limit · resets 1pm", session_id="s1")
DONE = ev(type="result", is_error=False, terminal_reason="completed", session_id="s1")
REJECTED = ev(type="rate_limit_event", rate_limit_info={"status": "rejected", "resetsAt": 1900000000})


def stream(name, lines, age=0):
    path = os.path.join(TMP, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    if age:
        old = time.time() - age
        os.utime(path, (old, old))
    return path


CASES = []


def case(name, fn):
    CASES.append((name, fn))


case("the resume classifier's own selftest passes",
     lambda: subprocess.run([sys.executable, TOOL, "--selftest"], capture_output=True,
                            text=True, timeout=60).returncode == 0)
case("a 429 retry run below the limit is not a usage limit",
     lambda: rc.watch([RETRY_429] * 9, 0, 10)[1:3] == (9, False))
case("ten consecutive 429 retries with no work are a usage limit",
     lambda: rc.watch([RETRY_429] * 10, 0, 10)[2] is True)
case("a work event between retries resets the 429 run",
     lambda: rc.watch([RETRY_429] * 9 + [WORK, RETRY_429], 0, 10)[1:3] == (1, False))
case("a non-429 retry resets the run",
     lambda: rc.watch([RETRY_429] * 9 + [RETRY_500, RETRY_429], 0, 10)[1] == 1)
case("the run carries across calls through its argument",
     lambda: rc.watch([RETRY_429], 9, 10)[2] is True)
case("retries are not work; an assistant event is",
     lambda: rc.watch([RETRY_429], 0, 10)[0] is False and rc.watch([WORK], 0, 10)[0] is True)
case("a terminal usage-limit result is limited and not work",
     lambda: rc.watch([LIMIT_RESULT], 0, 10)[0:3:2] == (False, True))
case("a terminal weekly-limit 429 is limited and not work",
     lambda: rc.watch([WEEKLY_LIMIT_RESULT], 0, 10)[0:3:2] == (False, True))
case("a rejected rate_limit_event names the reset time and the session id is kept",
     lambda: rc.watch([WORK, REJECTED], 0, 10)[3:] == (1900000000, "s1"))


def watch_cli_partial_line():
    path = os.path.join(TMP, "partial.jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(WORK + "\n" + RETRY_429[:10])
    out = subprocess.run([sys.executable, TOOL, "--watch", path, "0", "0", "10"],
                         capture_output=True, text=True, timeout=60).stdout.strip().split("\t")
    return out[0] == str(len(WORK) + 1) and out[1] == "1" and out[3] == "0"


case("--watch consumes only complete lines and reports the new offset", watch_cli_partial_line)
case("--watch with the wrong argument count exits 2",
     lambda: subprocess.run([sys.executable, TOOL, "--watch", "-"], capture_output=True,
                            text=True, timeout=60).returncode == 2)

NOW = time.time()
case("stream_status: a finished result is finished",
     lambda: rc.stream_status(stream("done.stream", [WORK, DONE]), NOW)[0] == "finished")
case("stream_status: a terminal usage-limit result is usage-limit",
     lambda: rc.stream_status(stream("limit.stream", [WORK, LIMIT_RESULT]), NOW)[0] == "usage-limit")
case("stream_status: trailing 429 retries at the limit are usage-limit",
     lambda: rc.stream_status(stream("retry10.stream", [WORK] + [RETRY_429] * 10), NOW)[0] == "usage-limit")
case("stream_status: a few trailing retries are retrying",
     lambda: rc.stream_status(stream("retry2.stream", [WORK, RETRY_429, RETRY_429]), NOW)[0] == "retrying")
case("stream_status: an old unfinished stream is stalled",
     lambda: rc.stream_status(stream("old.stream", [WORK], age=4000), time.time(), 900)[0] == "stalled")
case("stream_status: a fresh unfinished stream is working",
     lambda: rc.stream_status(stream("fresh.stream", [WORK]), time.time(), 900)[0] == "working")
case("stream_status: a bounded pointer stream is finished",
     lambda: rc.stream_status(stream("bounded.stream", [json.dumps({"seeAlso": "x.out.json"})]), NOW)[0]
     == "finished")
case("stream_status: a missing stream reads stalled, never working",
     lambda: rc.stream_status(os.path.join(TMP, "absent.stream"), NOW)[0] == "stalled")


def main():
    failed = 0
    for name, fn in CASES:
        try:
            ok, detail = bool(fn()), ""
        except Exception as exc:
            ok, detail = False, "  raised %s: %s" % (type(exc).__name__, exc)
        failed += not ok
        print("%s %s%s" % ("ok  " if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(CASES) - failed, len(CASES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
