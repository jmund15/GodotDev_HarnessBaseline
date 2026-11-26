#!/usr/bin/env python3
"""Proof for `seat_findings.py`: a review seat's findings recorded one row at a time survive a failed structured return.

The units run in process over files planted under this proof's temp root; one spawned case proves the registered
command runs and exits 0 or 2. The close that reads the rows back is proved in `test_sidecar_fanout_review_split.py`.

    python3 .claude/tests/test_seat_findings.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))
import seat_findings  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="seat_findings_"))
FAILED = []


def check(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label + ("" if ok else "\n     " + str(detail)[:400]))
    if not ok:
        FAILED.append(label)


def brief(name):
    path = TMP / (name + ".md")
    path.write_text("Review the subject.\n", encoding="utf-8")
    return path


FINDING = {"agent": "m", "action": "FIX", "category": "bug", "file": "a/b.cs:3", "description": 'quotes " and \\ stay'}
COVERAGE = {"checked": {"toolsUsed": ["Read:a"], "stoppedAt": "exhausted-leads", "basis": "read"}, "gaps": []}

b = brief("one")
check("the findings file is the brief's path with its extension replaced",
      seat_findings.path_for(b) == TMP / "one.findings.jsonl")
check("a seat that recorded nothing recovers nothing", seat_findings.recover(b) is None)

seat_findings.add(b, json.dumps(FINDING))
seat_findings.add(b, json.dumps(dict(FINDING, description="second")))
seat_findings.add(b, json.dumps(COVERAGE))
got = seat_findings.recover(b)
check("each recorded finding comes back whole, with the coverage row's checked and gaps",
      got and [f["description"] for f in got["findings"]] == [FINDING["description"], "second"]
      and got["checked"] == COVERAGE["checked"] and got["gaps"] == [], got)

b = brief("report")
seat_findings.add(b, json.dumps(dict(COVERAGE, report="| a | b |")))
check("the coverage row's lens report comes back too", seat_findings.recover(b)["report"] == "| a | b |")

b = brief("coverage_last")
seat_findings.add(b, json.dumps(COVERAGE))
seat_findings.add(b, json.dumps(dict(COVERAGE, gaps=[{"item": "x", "reason": "missing"}])))
check("the last coverage row wins", seat_findings.recover(b)["gaps"] == [{"item": "x", "reason": "missing"}])

b = brief("findings_only")
seat_findings.add(b, json.dumps(FINDING))
check("a seat that recorded findings and no coverage row recovers them with no `checked`",
      "checked" not in seat_findings.recover(b) and len(seat_findings.recover(b)["findings"]) == 1)

b = brief("torn")
seat_findings.add(b, json.dumps(FINDING))
with seat_findings.path_for(b).open("a", encoding="utf-8", newline="\n") as stream:
    stream.write('{"agent": "cut off mid-str')
check("a torn line costs only itself", [f["description"] for f in seat_findings.recover(b)["findings"]] == [FINDING["description"]])

b = brief("stale")
seat_findings.add(b, json.dumps(FINDING))
later = time.time() + 60
os.utime(b, (later, later))
check("a findings file older than its brief belongs to an earlier run", seat_findings.recover(b) is None)

b = brief("refused")
for label, text in (("text that is not JSON", "{not json"), ("a JSON array", "[1]"), ("an object that is neither row", '{"x": 1}')):
    try:
        seat_findings.add(b, text)
        refused = False
    except ValueError:
        refused = True
    check("add refuses " + label + " and stores nothing", refused and not seat_findings.path_for(b).exists())

for engine in ("dispatch.js", "review_fanout.js"):
    check(engine + " names the same findings suffix when it routes a dead seat to its recorded rows",
          "'" + seat_findings.SUFFIX + "'" in (HERE.parent / "workflows" / engine).read_text(encoding="utf-8"))

b = brief("spawned")
env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
run = subprocess.run([sys.executable, str(TOOLS / "seat_findings.py"), "add", str(b)], input=json.dumps(FINDING),
                     capture_output=True, text=True, env=env, timeout=60)
bad = subprocess.run([sys.executable, str(TOOLS / "seat_findings.py"), "add", str(b), "--json", "{"],
                     capture_output=True, text=True, env=env, timeout=60)
check("the registered command records a row from stdin (exit 0) and refuses a bad one (exit 2)",
      run.returncode == 0 and bad.returncode == 2 and len(seat_findings.recover(b)["findings"]) == 1,
      (run.returncode, run.stderr, bad.returncode, bad.stderr))

print("\n%d failed" % len(FAILED) if FAILED else "\nall pass")
sys.exit(1 if FAILED else 0)
