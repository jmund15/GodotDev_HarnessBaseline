#!/usr/bin/env python3
"""Proof for tools/battery_children.py: each battery test runs as its own headless `claude -p` child
with the prompt verbatim on stdin, and the run writes the scorer's inputs (test_score_routing_battery.py
proves the scorer reads them).

The `claude` binary is a planted stub (BATTERY_CLAUDE_BIN) that records its argv and stdin and prints
the `--output-format json` shape a real child prints.

    python3 .claude/tests/test_battery_children.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, "..", "tools"))
TOOL = os.path.join(TOOLS, "battery_children.py")
failures = []

STUB = r'''import json, os, sys, uuid
prompt = sys.stdin.read()
sid = str(uuid.uuid5(uuid.NAMESPACE_URL, prompt))
with open(os.path.join(os.environ["STUB_LOG"], sid + ".json"), "w", encoding="utf-8") as fh:
    json.dump({"argv": sys.argv[1:], "stdin": prompt}, fh)
if "FAIL-ME" in prompt:
    sys.stderr.write("planted failure\n")
    sys.exit(1)
print(json.dumps({"type": "result", "is_error": False, "session_id": sid, "result": "answer to " + prompt[:20]}))
'''


def ck(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else "\n        " + str(detail)[-400:]))
    if not cond:
        failures.append(label)


def main():
    with tempfile.TemporaryDirectory() as td:
        stub = os.path.join(td, "claude_stub.py")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write(STUB)
        log = os.path.join(td, "log")
        os.makedirs(log)
        prompts = {"A1": "[ROUTECHECK-A1] Where is X defined?\n  keep this indent", "A2": "[ROUTECHECK-A2] Edit Y", "B1": "FAIL-ME"}
        jobs = os.path.join(td, "jobs.json")
        with open(jobs, "w", encoding="utf-8") as fh:
            json.dump([{"label": k, "prompt": v} for k, v in prompts.items()], fh)
        out = os.path.join(td, "out")
        env = dict(os.environ, BATTERY_CLAUDE_BIN=sys.executable + " " + stub, STUB_LOG=log)
        p = subprocess.run([sys.executable, TOOL, jobs, "--model", "planted-model", "--effort", "low", "--out-dir", out],
                           env=env, capture_output=True, text=True, timeout=120)
        ck("a run with one failed child exits 1 and still writes every record", p.returncode == 1, (p.returncode, p.stdout, p.stderr))
        calls = [json.load(open(os.path.join(log, f), encoding="utf-8")) for f in os.listdir(log)]
        by_stdin = {c["stdin"]: c["argv"] for c in calls}
        ck("each prompt reaches its child byte for byte on stdin, with no wrapper",
           set(by_stdin) == set(prompts.values()), sorted(by_stdin))
        argv = by_stdin.get(prompts["A1"], [])
        ck("each child runs headless with the pinned model, effort, json output and auto permissions",
           "-p" in argv and argv[argv.index("--model") + 1] == "planted-model" and argv[argv.index("--effort") + 1] == "low"
           and argv[argv.index("--output-format") + 1] == "json" and argv[argv.index("--permission-mode") + 1] == "auto",
           argv)
        try:
            ids = json.load(open(os.path.join(out, "agent_ids.json"), encoding="utf-8"))
            responses = json.load(open(os.path.join(out, "responses.json"), encoding="utf-8"))
        except (OSError, ValueError) as exc:
            ids, responses = {}, {}
            ck("the run writes agent_ids.json and responses.json", False, exc)
        ck("agent_ids.json maps each finished test to its child session in the scorer's shape",
           set(ids) == {"A1", "A2"} and all(v["agent_id"] is None and v["session_id"] for v in ids.values()), ids)
        ck("responses.json holds each child's final answer", responses.get("A2", "").startswith("answer to"), responses)
        ck("a failed child is named on stderr and left out of both files", "B1" in p.stderr and "B1" not in ids, p.stderr)

    print("test_battery_children: %d failed" % len(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
