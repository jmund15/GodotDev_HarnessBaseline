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
sys.path.insert(0, os.path.dirname(TOOL))  # the tool imports its sibling stream_events
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
# The 5-hour window's terminal shape, as a Claude CLI child ended on it (subtype "success", is_error true).
SESSION_LIMIT_RESULT = ev(type="result", subtype="success", is_error=True, terminal_reason="api_error",
                          api_error_status=429, session_id="s1",
                          result="You've hit your session limit \u00b7 resets 4:40pm (America/Chicago)")
# A terminal 429 whose wording no pattern knows: the status alone decides.
UNWORDED_429_RESULT = ev(type="result", is_error=True, terminal_reason="api_error", api_error_status=429,
                         session_id="s1", result="API Error: 429")
# The client's own resume prompt after a reply hits the output-token cap (Claude Code 2.1.x).
CAPPED_TURN = ev(type="user", session_id="s1", message={"role": "user", "content": [{"type": "text", "text":
    "Output token limit hit. Resume directly \u2014 no apology, no recap of what you were doing. Pick up "
    "mid-thought if that is where the cut happened. Break remaining work into smaller pieces."}]})
QUOTED_RESULT = ev(type="user", session_id="s1", message={"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "t1", "content": "OUTPUT_CAP_RESUME = \"Output token limit hit.\""}]})
QUOTED_ASSISTANT = ev(type="assistant", session_id="s1", message={"role": "assistant", "content": [
    {"type": "text", "text": "Output token limit hit. is the phrase the client sends"}]})
CAPPED_RESULT = ev(type="result", is_error=True, terminal_reason="api_error", session_id="s1",
                   result="API Error: Claude's response exceeded the 32000 output token maximum. "
                          "To configure this behavior, set the CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable.")
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


case("recovery_work counts new tools before a compact boundary, not summaries or results",
     lambda: rc.recovery_work([
         ev(type='assistant', message={'content': [{'type': 'tool_use', 'name': 'Read'}]}),
         ev(type='system', subtype='compact_boundary'),
         ev(type='assistant', message={'content': [{'type': 'text', 'text': '<summary>x</summary>'}]}),
         DONE]) == 1 and rc.recovery_work([WORK, DONE, RETRY_500]) == 0)
case("--work exposes the same count to the launcher",
     lambda: subprocess.run([sys.executable, TOOL, '--work'], input=DONE + '\n', capture_output=True,
                            text=True, timeout=60).stdout.strip() == '0')

case("structured permanent statuses outrank transient wording",
     lambda: all(rc.classify([ev(type='result', session_id='s1', is_error=True,
                                api_error_status=status, result='WebSocket stream error')]) == (None, None)
                 for status in (400, 401, 403, 404, 429)))

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
case("a terminal session-limit 429 (the 5-hour window) is limited and not work",
     lambda: rc.watch([SESSION_LIMIT_RESULT], 0, 10)[0:3:2] == (False, True))
case("a terminal 429 result in any wording is limited",
     lambda: rc.watch([UNWORDED_429_RESULT], 0, 10)[2] is True)
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
ERR_RESULT = ev(type="result", subtype="success", is_error=True, terminal_reason="api_error",
                result="Credit balance is too low", session_id="s1")
case("terminal_result: a success result is 0, an error result is 1",
     lambda: rc.terminal_result([WORK, DONE]) == 0 and rc.terminal_result([WORK, ERR_RESULT]) == 1)
case("terminal_result: no result, or only a usage-limit result, is None",
     lambda: rc.terminal_result([WORK, RETRY_429]) is None and rc.terminal_result([WORK, LIMIT_RESULT]) is None)


def watch_cli_result_field():
    path = os.path.join(TMP, "ended.jsonl")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(WORK + "\n" + ERR_RESULT + "\n")
    out = subprocess.run([sys.executable, TOOL, "--watch", path, "0", "0", "10"],
                         capture_output=True, text=True, timeout=60).stdout.rstrip("\n").split("\t")
    plain = subprocess.run([sys.executable, TOOL, "--watch", "-", "0", "0", "10"], input=WORK + "\n",
                           capture_output=True, text=True, timeout=60).stdout.rstrip("\n").split("\t")
    return len(out) == 7 and out[6] == "1" and out[5] == "s1" and len(plain) == 7 and plain[6] == ""


case("--watch prints the chunk's terminal result (1 = error) as a seventh field, empty without one",
     watch_cli_result_field)
case("--watch with the wrong argument count exits 2",
     lambda: subprocess.run([sys.executable, TOOL, "--watch", "-"], capture_output=True,
                            text=True, timeout=60).returncode == 2)

NOW = time.time()
TASK_UPDATED = ev(type="system", subtype="task_updated", task_id="b1")
case("stream_status: a result followed only by background-task bookkeeping is finished",
     lambda: rc.stream_status(stream("taskafter.stream", [WORK, DONE, TASK_UPDATED]), NOW)[0] == "finished")
case("terminal_result_age: the age of a stream ending on a result, through trailing system events",
     lambda: 590 <= rc.terminal_result_age(stream("aged.stream", [WORK, ERR_RESULT, TASK_UPDATED], age=600),
                                           time.time()) <= 610)
case("terminal_result_age: a stream with work after its result, or no result, is None",
     lambda: rc.terminal_result_age(stream("rewoke.stream", [WORK, DONE, WORK]), NOW) is None
     and rc.terminal_result_age(stream("noresult.stream", [WORK]), NOW) is None
     and rc.terminal_result_age(os.path.join(TMP, "absent.stream"), NOW) is None)
case("stream_status: a running job the client resumed past the output-token cap is output-cap",
     lambda: rc.stream_status(stream("capped.stream", [WORK, CAPPED_TURN, WORK]), time.time())[0] == "output-cap")
case("stream_status: output-cap names the fix, the row's registry output maximum",
     lambda: "max-output" in rc.stream_status(stream("capped2.stream", [WORK, CAPPED_TURN, WORK]), time.time())[1])
case("stream_status: a finished result outranks an earlier output-cap resume",
     lambda: rc.stream_status(stream("cappeddone.stream", [WORK, CAPPED_TURN, WORK, DONE]), NOW)[0] == "finished")
case("stream_status: a stale stream with an earlier output-cap resume is stalled",
     lambda: rc.stream_status(stream("cappedstale.stream", [WORK, CAPPED_TURN, WORK], age=4000), time.time(), 900)[0]
     == "stalled")
case("stream_status: trailing 429 retries after an earlier output-cap resume are usage-limit",
     lambda: rc.stream_status(stream("cappedlimit.stream", [WORK, CAPPED_TURN, WORK] + [RETRY_429] * 10), NOW)[0]
     == "usage-limit")
case("stream_status: the resume phrase quoted in a tool result is not an output-cap cut",
     lambda: rc.stream_status(stream("quoted.stream", [WORK, QUOTED_RESULT, WORK]), time.time())[0] == "working")
case("stream_status: the resume phrase quoted in assistant text is not an output-cap cut",
     lambda: rc.stream_status(stream("quoted2.stream", [WORK, QUOTED_ASSISTANT, WORK]), time.time())[0] == "working")
case("stream_status: a terminal result that outgrew the output cap is output-cap, not finished",
     lambda: rc.stream_status(stream("cappedterm.stream", [WORK, CAPPED_RESULT]), NOW)[0] == "output-cap")
case("stream_status: a terminal output-cap result names the registry fix",
     lambda: "maxOutputTokens (model_registry.py max-output, reference/sidecar_runs.md)" in rc.stream_status(stream("cappedterm2.stream", [WORK, CAPPED_RESULT]), NOW)[1])
case("output_cap: reads the cap size from the terminal message and ignores other text",
     lambda: rc.output_cap("API Error: Claude's response exceeded the 32000 output token maximum.") == "32000"
     and rc.output_cap("something else") is None)
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
