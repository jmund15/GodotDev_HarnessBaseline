#!/usr/bin/env python3
"""Run each battery test as its own headless `claude -p` child, prompt verbatim on stdin.

A battery measures what a session does with a bare prompt. A subagent gets a wrapped prompt or a
reduced tool set, and `dispatch` adds a brief line and rails, so each test runs as a top-level
session instead: the project's own hooks, tools and permissions, and nothing added to the prompt.

    python3 .claude/tools/battery_children.py <jobs.json> --model <id> --out-dir <dir>
                                              [--effort <level>] [--max-parallel N]
                                              [--permission-mode <mode>]

jobs.json is a list of {"label": ..., "prompt": "..."} or {"label": ..., "promptFile": "..."}.
Writes to --out-dir:
  agent_ids.json   {label: {"agent_id": null, "session_id": <child session>}}, the shape
                   score_routing_battery.py reads (a null agent_id reads the session's own transcript)
  responses.json   {label: <the child's final answer>}
  runs.json        every child's record, failed ones included
A failed child is named on stderr and left out of the first two files; the run then exits 1.
`--permission-mode` defaults to `auto`, the classifier an interactive session uses.
BATTERY_CLAUDE_BIN overrides the `claude` command (proofs plant a stub there).
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHILD_SECONDS = 1800


def claude_cmd():
    raw = os.environ.get("BATTERY_CLAUDE_BIN")
    return shlex.split(raw, posix=(os.name != "nt")) if raw else ["claude"]


def run_child(job, args):
    prompt = job["prompt"] if "prompt" in job else Path(job["promptFile"]).read_text(encoding="utf-8")
    argv = claude_cmd() + ["-p", "--model", args.model, "--output-format", "json",
                           "--permission-mode", args.permission_mode]
    if args.effort:
        argv += ["--effort", args.effort]
    try:
        p = subprocess.run(argv, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           cwd=REPO, timeout=CHILD_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"label": job["label"], "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    try:
        out = json.loads(p.stdout)
    except ValueError:
        out = {}
    ok = p.returncode == 0 and bool(out.get("session_id")) and not out.get("is_error")
    return {"label": job["label"], "ok": ok, "session_id": out.get("session_id"), "result": out.get("result"),
            "returncode": p.returncode, "error": None if ok else (p.stderr.strip()[-500:] or "no session in output")}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("jobs")
    ap.add_argument("--model", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--effort")
    ap.add_argument("--max-parallel", type=int, default=4)
    ap.add_argument("--permission-mode", default="auto")
    args = ap.parse_args(argv)
    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    bad = [j for j in jobs if not isinstance(j, dict) or not j.get("label") or not ("prompt" in j or "promptFile" in j)]
    if bad or len({j["label"] for j in jobs}) != len(jobs):
        print("battery_children: every job needs a unique label and a prompt or promptFile", file=sys.stderr)
        return 2
    with ThreadPoolExecutor(max_workers=max(1, args.max_parallel)) as pool:
        runs = list(pool.map(lambda j: run_child(j, args), jobs))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    good = [r for r in runs if r["ok"]]
    (out / "agent_ids.json").write_text(json.dumps(
        {r["label"]: {"agent_id": None, "session_id": r["session_id"]} for r in good}, indent=2), encoding="utf-8")
    (out / "responses.json").write_text(json.dumps({r["label"]: r["result"] for r in good}, indent=2), encoding="utf-8")
    (out / "runs.json").write_text(json.dumps(runs, indent=2), encoding="utf-8")
    for r in runs:
        if not r["ok"]:
            print("battery_children: %s failed: %s" % (r["label"], r["error"]), file=sys.stderr)
    print("battery_children: %d of %d children finished; records in %s" % (len(good), len(runs), out))
    return 0 if len(good) == len(runs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
