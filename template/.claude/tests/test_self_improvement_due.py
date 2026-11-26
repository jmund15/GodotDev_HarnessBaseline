#!/usr/bin/env python3
"""Re-runnable proof for tools/self_improvement_due.py: the SessionStart line that says `/autolearn` is due.

Builds a fixture `.claude`-shaped tree with planted `review-by` and evidence markers, runs the tool as a
subprocess and asserts on stdout and the exit code. The tool reads no archive, stamp or candidates file, so an
empty root is the control case.

    python3 .claude/tests/test_self_improvement_due.py
"""
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "self_improvement_due.py")
TODAY_ISO = datetime.date.today().isoformat()

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" — " + detail) if detail else ""))
        FAILURES.append(label)


def run(base, *args, env=None):
    cmd = [sys.executable, TOOL, "--root", base, "--today", TODAY_ISO] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def empty_root_cases():
    with tempfile.TemporaryDirectory() as base:
        proc = run(base, "--line")
        check("an empty root prints no line and exits 0", proc.returncode == 0 and proc.stdout.strip() == "",
              repr(proc.stdout))
        proc = run(base)
        check("report mode exits 0 and reads reviews and evidence as live",
              proc.returncode == 0 and re.search(r"^reviews:\s+live", proc.stdout, re.M)
              and re.search(r"^evidence:\s+live", proc.stdout, re.M), proc.stdout)
        check("no report row names a retired command or stamp", "eval_dashboard" not in proc.stdout
              and "candidates" not in proc.stdout, proc.stdout)
        bad = subprocess.run([sys.executable, TOOL, "--stamp"], capture_output=True, text=True)
        check("the retired --stamp flag is an unknown flag (exit 2)", bad.returncode == 2, str(bad.returncode))


def review_cases():
    """Five expired review-by triggers in the review scope make the line due, naming /autolearn."""
    with tempfile.TemporaryDirectory() as base:
        rules = os.path.join(base, "rules")
        for i in range(5):
            write_text(os.path.join(rules, "r%d.md" % i), "Rule.\n\n<!-- retire-when: review-by: 2020-01-01 -->\n")

        proc = run(base, "--line")
        line = proc.stdout.strip()
        check("five expired reviews: the line names them and /autolearn",
              "5 instruction reviews past their review-by date (run /autolearn)" in line, repr(line))
        check("the line holds one line", line.startswith("Self-improvement due:") and "\n" not in line, repr(line))
        proc = run(base)
        check("report mode prints a DUE reviews row", re.search(r"^reviews:\s+DUE", proc.stdout, re.M) is not None,
              proc.stdout)

        os.remove(os.path.join(rules, "r4.md"))
        proc = run(base, "--line")
        check("four expired reviews leave the line empty", proc.stdout.strip() == "", repr(proc.stdout))
        proc = run(base)
        check("report mode prints a live reviews row", re.search(r"^reviews:\s+live", proc.stdout, re.M) is not None,
              proc.stdout)


def evidence_cases():
    """Evidence markers add a due reason naming /autolearn; a raising scan is due with its error."""
    marker = "<!-- basis: thin; reverses-if: the planted observation; review-when: %s -->\n"
    with tempfile.TemporaryDirectory() as base:
        escapes = os.path.join(base, "escape.jsonl")
        write_text(escapes, json.dumps({"id": "e1", "ts": "2026-06-01T00:00:00Z", "paths": ["Jmodot"]}) + "\n")
        env = dict(os.environ, HARNESS_ESCAPE_LOG=escapes)
        rule = os.path.join(base, "commands", "rule.md")

        def line():
            return run(base, "--line", env=env).stdout.strip()

        write_text(rule, "Step.\n\n" + marker % "escape path:Jmodot since:2026-01-01 >= 2")
        check("a live marker leaves the line empty", line() == "", repr(line()))
        write_text(rule, "Step.\n\n" + marker % "escape path:Jmodot since:2026-01-01 >= 1")
        got = line()
        check("one fired marker makes the line name it and /autolearn",
              "1 evidence markers fired, 0 malformed (run /autolearn)" in got, repr(got))
        write_text(rule, "Step.\n\n" + marker % "escape path:Jmodot >= 1")
        got = line()
        check("one malformed marker makes the line name it",
              "0 evidence markers fired, 1 malformed (run /autolearn)" in got, repr(got))
        os.remove(rule)
        check("no marker leaves the line empty", line() == "", repr(line()))

        spec = importlib.util.spec_from_file_location("self_improvement_due_evidence_probe", TOOL)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        import rule_retirement
        real = rule_retirement.evidence_due

        def boom(root):
            raise RuntimeError("planted")

        rule_retirement.evidence_due = boom
        try:
            due, reason = tool.markers_due(base)
            got = tool.due_line(base, datetime.date.today())
        finally:
            rule_retirement.evidence_due = real
        check("a raising scan is due with its error", due and "evidence scan failed" in reason
              and "RuntimeError" in reason, reason)
        check("the due line carries the failed scan and names /autolearn",
              "evidence scan failed" in got and "(run /autolearn)" in got, got)


def main():
    if not os.path.exists(TOOL):
        raise SystemExit("RED: %s does not exist" % TOOL)
    empty_root_cases()
    review_cases()
    evidence_cases()

    print()
    if FAILURES:
        print("FAILED %d case(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("PASS — self_improvement_due.py proof green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
