"""Proof of the shared agent-stream reader behind the watcher's progress clause and `--digest`.

Fixtures are cut from captured live output (`fixtures/watch_progress/`): `gatefix_trim.stream` from a four-hour
executor run that reworked one test file, `survey_trim.stream` from a read-only review seat,
`opencode_trim.stream` from an opencode `--format json` run.
"""
import calendar
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "watch_progress"
sys.path.insert(0, str(ROOT / "tools"))
import agent_progress as ap  # noqa: E402

PASS = 0
WINDOW = 3000
# Words that name a verdict or an action pick. The reader reports measures; none may appear in what it prints.
VERDICTS = re.compile(r"\b(loop\w*|stall\w*|lagging|converg\w*|stuck|runaway|behind|too slow|should stop|stop it|kill)\b", re.I)


def check(condition, label, detail=""):
    global PASS
    if not condition:
        raise AssertionError("%s: %s" % (label, detail))
    PASS += 1
    print("PASS", label)


def epoch(stamp):
    return calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) + float("0" + stamp[19:-1])


def last_stamp(path):
    last = None
    for raw in open(path, "rb"):
        row = json.loads(raw)
        if isinstance(row.get("timestamp"), str):
            last = row["timestamp"]
    return epoch(last)


def numbers(text):
    return [int(n) for n in text.split()]


tmp = Path(tempfile.mkdtemp(prefix="agent_progress_"))

# --- an executor stream: the most-edited file, the rate series, no commits, a writer
gate = ap.read(str(FIX / "gatefix_trim.stream"))
now = last_stamp(FIX / "gatefix_trim.stream") + 60
text = ap.clause(gate, now=now, window=WINDOW)
check(gate is not None and gate.readable, "the executor stream reads")
check("AlphaRosterTests.cs 12" in text, "the clause names the most-edited file with its count", text)
check(text.index("AlphaRosterTests.cs") < text.index("BetaVariationTests.cs"),
      "the most-edited file leads the edit list", text)
series = re.search(r"tool calls[^()]*\(per 50 min, oldest first: ([\d ]+)\)", text)
check(series is not None, "the clause shows tool calls per check-in window", text)
rates = numbers(series.group(1))
check(len(rates) >= 3 and rates[0] > 2 * rates[-1], "the series shows the tool-call rate falling", str(rates))
check(re.search(r"context \d+K", text) is not None, "the clause shows the context size", text)
check(gate.writer is True and "commits 0" in text, "a writer prints its commit count, zero included", text)
check("test result lines" in text and "failing" in text, "the clause counts test results the stream shows", text)
check('said "' in text, "the clause quotes the job's latest stated intent", text)
check(len(text) < 700 and "\n" not in text, "the clause stays one bounded line", str(len(text)))
check(not VERDICTS.search(re.sub(r'said ".*"', "", text)), "the measures carry no verdict word", text)

# --- a read-only seat prints no commit field
survey = ap.read(str(FIX / "survey_trim.stream"))
stext = ap.clause(survey, now=last_stamp(FIX / "survey_trim.stream") + 5, window=WINDOW)
check(survey.writer is False and "commit" not in stext, "a read-only job prints no commit field", stext)
check(re.search(r"reads \d+ \(\d+ files\)", stext) is not None, "a read-only job prints its reads and distinct files", stext)
check("edits" not in stext, "a read-only job prints no edit field", stext)

# --- an opencode event stream reads through the same reader
oc = ap.read(str(FIX / "opencode_trim.stream"))
otext = ap.clause(oc, now=time.time(), window=WINDOW)
check(oc.readable and re.search(r"tool calls [1-9]", otext) is not None, "an opencode stream reads", otext)

# --- unreadable and empty streams
bad = tmp / "bad.stream"
bad.write_text("this is not json\nneither is this\n", encoding="utf-8")
check(ap.clause(ap.read(str(bad)), now=time.time(), window=WINDOW) == "progress unreadable",
      "an unparseable stream prints progress unreadable, never zero progress")
stub = tmp / "stub.stream"
stub.write_text(json.dumps({"seeAlso": "x.out.json", "note": "progress stream bounded after the run completed"}), encoding="utf-8")
check(ap.clause(ap.read(str(stub)), now=time.time(), window=WINDOW) == "progress unreadable",
      "a bounded stub with no rows prints progress unreadable")
empty = tmp / "empty.stream"
empty.write_text("", encoding="utf-8")
check(ap.read(str(empty)) is None and ap.read(str(tmp / "absent.stream")) is None,
      "an empty or absent stream has no clause at all")

# --- incremental: an appended stream parses only the new rows, a partial last line waits for its newline
live = tmp / "live.stream"
rows = [json.loads(raw) for raw in open(FIX / "gatefix_trim.stream", "rb")]
head = "".join(json.dumps(r) + "\n" for r in rows[:150])
live.write_text(head, encoding="utf-8", newline="\n")
first = ap.read(str(live))
calls_head = len(first.calls)
parsed = []
real_loads = ap.json.loads


def counting(raw, *a, **k):
    parsed.append(1)
    return real_loads(raw, *a, **k)


ap.json.loads = counting
try:
    again = ap.read(str(live))
    check(not parsed and len(again.calls) == calls_head, "an unchanged stream is not parsed again")
    tail = "".join(json.dumps(r) + "\n" for r in rows[150:160])
    with open(live, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(tail)
    grown = ap.read(str(live))
    check(len(parsed) <= 10 and len(grown.calls) > calls_head, "an appended stream parses only its new rows", str(len(parsed)))
    partial = json.dumps(rows[160])
    with open(live, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(partial[:40])
    before = len(ap.read(str(live)).calls)
    with open(live, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(partial[40:] + "\n")
    after = len(ap.read(str(live)).calls)
    expected = sum(1 for b in rows[160].get("message", {}).get("content", []) if b.get("type") == "tool_use")
    check(after - before == expected, "a row written in two pieces counts once, after its newline", "%d %d" % (before, after))
finally:
    ap.json.loads = real_loads
live.write_text(head[:2000].rsplit("\n", 1)[0] + "\n", encoding="utf-8", newline="\n")
check(len(ap.read(str(live)).calls) <= 3, "a rewritten, shorter stream is read from its start")

# --- the digest: the whole-run view
digest = ap.digest(gate, label="gatefix")
check("AlphaRosterTests.cs" in digest and "per hour" in digest and "context" in digest,
      "the digest shows per-hour calls, edits per file and context growth", digest[:400])
check(len(digest) < 12000, "the digest output is bounded", str(len(digest)))
check(not VERDICTS.search(re.sub(r'(said|\d\d:\d\d) .*', '', digest)), "the digest carries no verdict word outside quoted job text", digest[:300])

# --- duration history: nearest-rank p90 over finished same-shape records, no basis below ten
metrics = tmp / "orchestration_metrics.jsonl"
records = tmp / "records"
records.mkdir()
lines = []


def finished(name, seconds, builtins, state="completed", workflow="sidecar"):
    path = records / (name + ".record.json")
    path.write_text(json.dumps({"toolProfile": {"builtins": builtins}}), encoding="utf-8")
    lines.append(json.dumps({"workflow": workflow, "state": state, "secs": seconds, "record_path": str(path)}))


for index in range(12):
    finished("w%d" % index, (index + 1) * 60, ["Read", "Edit", "Bash"])
finished("w-failed", 99999, ["Read", "Edit"], state="exit-1")
finished("w-flow", 99999, ["Read", "Edit"], workflow="review-fanout")
for index in range(3):
    finished("r%d" % index, 100 + index, ["Read", "Grep"])
lines.append(json.dumps({"workflow": "sidecar", "state": "completed", "secs": 5, "record_path": str(records / "gone.record.json")}))
metrics.write_text("\n".join(lines) + "\n", encoding="utf-8")
count, p90 = ap.history("writer", str(metrics))
check(count == 12 and p90 == 11.0, "a writer's p90 is the nearest-rank 90th percentile of its completed sidecar runs", str((count, p90)))
count, p90 = ap.history("read-only", str(metrics))
check(count == 3 and p90 is None, "a sample under ten has no p90", str((count, p90)))
check(ap.history("writer", str(tmp / "missing.jsonl")) == (0, None), "a missing metrics file is an empty sample")
past = ap.clause(gate, now=now, window=WINDOW, age_min=200, history=(12, 11.0), shape="writer")
check("past p90 of n=12 writer jobs (11 min)" in past, "a job older than the p90 says so with its sample size", past)
within = ap.clause(gate, now=now, window=WINDOW, age_min=5, history=(12, 11.0), shape="writer")
check("p90" not in within, "a job inside the p90 prints no duration field", within)
nobasis = ap.clause(gate, now=now, window=WINDOW, age_min=500, history=(3, None), shape="writer")
check("no basis" in nobasis and "n=3" in nobasis, "a sample under ten prints no basis", nobasis)

# --- a checklist file the job wrote: items done of total
checklist = tmp / "progress.md"
checklist.write_text("- [x] one\n- [x] two\n- [ ] three\n- [ ] four\n", encoding="utf-8")
cl_rows = [
    {"type": "system", "subtype": "init", "tools": ["Write", "Read"], "cwd": str(tmp)},
    {"type": "assistant", "timestamp": "2026-10-18T12:00:00.000Z", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": str(checklist), "content": "x"}}],
        "usage": {"input_tokens": 10}}},
]
cl = tmp / "cl.stream"
cl.write_text("".join(json.dumps(r) + "\n" for r in cl_rows), encoding="utf-8", newline="\n")
check("items 2 of 4 in progress.md" in ap.clause(ap.read(str(cl)), now=epoch("2026-10-18T12:05:00.000Z"), window=WINDOW),
      "a checklist file the job wrote prints items done of total")

# --- a worktree job counts its commits from the worktree's git log since launch: `git commit -q` prints no line
GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
repo, wt = tmp / "repo", tmp / "wt"
subprocess.run(["git", "init", "-q", str(repo)], check=True, env=GIT_ENV)
subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "before launch"], check=True, env=dict(
    GIT_ENV, GIT_COMMITTER_DATE="2000-01-01T00:00:00Z"))
subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "wt-proof", str(wt)], check=True, env=GIT_ENV)
started = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 600)) + ".000Z"
wt_rows = [
    {"type": "system", "subtype": "init", "tools": ["Bash", "Write"], "cwd": str(wt)},
    {"type": "assistant", "timestamp": started, "message": {"content": [
        {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "git commit -q -m job"}}]}},
    {"type": "user", "timestamp": started, "message": {"content": [{"type": "tool_result", "tool_use_id": "c1", "content": ""}]}},
]
subprocess.run(["git", "-C", str(wt), "commit", "-q", "--allow-empty", "-m", "job"], check=True, env=GIT_ENV)
wt_stream = tmp / "wt.stream"
wt_stream.write_text("".join(json.dumps(r) + "\n" for r in wt_rows), encoding="utf-8", newline="\n")
wt_text = ap.clause(ap.read(str(wt_stream)), now=time.time(), window=WINDOW)
check("commits 1" in wt_text, "a worktree job counts its quiet commit from its worktree log, not the base commit before launch", wt_text)

_real_run, _git_calls = subprocess.run, []
subprocess.run = lambda *a, **k: (_git_calls.append(a), _real_run(*a, **k))[1]
try:
    wt_prog = ap.read(str(wt_stream))
    wt_prog.made_commits()
    first_calls = len(_git_calls)
    ap.clause(wt_prog, now=time.time(), window=WINDOW)
    ap.digest(wt_prog)
    check(len(_git_calls) == first_calls, "made_commits asks git once per Progress state, not per call", str(len(_git_calls)))
finally:
    subprocess.run = _real_run

# A Workflow seat row's usage carries `output_tokens_details.thinking_tokens`; only the heartbeat
# (`{"type":"system","subtype":"thinking_tokens"}`) is skipped, in compact and spaced JSON alike.
def seat_call(n, ts):
    return {"type": "assistant", "timestamp": ts, "isSidechain": True,
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "seat%d" % n, "name": "Bash",
                                                          "input": {"command": "git status"}}],
                        "usage": {"input_tokens": 3, "output_tokens": 90, "output_tokens_details": {"thinking_tokens": 35}}}}


def seat_result(n, ts):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "seat%d" % n, "content": "ok"}]}}


heartbeat = {"type": "system", "subtype": "thinking_tokens", "session_id": "S"}
for compact in (True, False):
    sep = (",", ":") if compact else (", ", ": ")
    seat_stream = tmp / ("seat_%s.stream" % compact)
    seat_rows = [seat_call(1, "2026-10-29T15:23:53.341Z"), seat_result(1, "2026-10-29T15:23:54.000Z"), heartbeat,
                 seat_call(2, "2026-10-29T15:24:10.000Z"), seat_result(2, "2026-10-29T15:24:11.000Z"), heartbeat]
    seat_stream.write_text("".join(json.dumps(r, separators=sep) + "\n" for r in seat_rows), encoding="utf-8", newline="\n")
    seat = ap.read(str(seat_stream))
    check(len(seat.calls) == 2 and len(seat.contexts) == 2,
          "seat rows whose usage carries thinking_tokens count as calls (compact=%s)" % compact, str(len(seat.calls)))
    check(seat.rows == 4, "only the heartbeat rows are skipped (compact=%s)" % compact, str(seat.rows))

# The antigravity seat's agy stream: `event`-keyed rows that agy_seat.py `run` stamps as it tees them.
agy_rows = [json.loads(line) for line in (ROOT / "tests" / "fixtures" / "agy" / "stream.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
agy_stream = tmp / "agy.stream"
agy_stream.write_text("".join(json.dumps(dict(r, timestamp="2026-11-21T19:%02d:00.000Z" % i)) + "\n" for i, r in enumerate(agy_rows)),
                      encoding="utf-8", newline="\n")
agy = ap.read(str(agy_stream))
check(agy.readable and agy.tools, "an agy stream is readable and its init lists the tools", str(agy.rows))
check(len(agy.calls) >= 3 and agy.said, "agy tool steps count as calls and its answer is said", "%d %d" % (len(agy.calls), len(agy.said)))

print("test_agent_progress: %d passed, 0 failed" % PASS)
