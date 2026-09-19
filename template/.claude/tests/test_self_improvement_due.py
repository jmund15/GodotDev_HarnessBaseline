#!/usr/bin/env python3
"""Re-runnable proof for tools/self_improvement_due.py.

Builds a fixture `.claude`-shaped tree: a self-evaluate archive with a planted row
count, an `eval_dashboard_last.json` stamp, and an `orchestration_candidates.json`
with a planted mtime. Runs the tool as a subprocess and asserts on stdout/exit code.

    python3 .claude/tests/test_self_improvement_due.py
"""
import datetime
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "self_improvement_due.py")
TODAY = datetime.date.today()
TODAY_ISO = TODAY.isoformat()

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print("  ok   " + label)
    else:
        print("  FAIL " + label + ((" — " + detail) if detail else ""))
        FAILURES.append(label)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle)


def archive_with_rows(n):
    return {"structured_entries": [{"session_id": "s%03d" % i} for i in range(n)]}


def run(base, *args):
    cmd = [sys.executable, TOOL, "--root", base, "--today", TODAY_ISO] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


def run_no_today(base, *args):
    cmd = [sys.executable, TOOL, "--root", base] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)


HOLD_LOCK = r'''
import sys
sys.path.insert(0, sys.argv[1])
import _file_lock
handle = _file_lock.acquire(sys.argv[2], 5.0)
print("held" if handle else "refused", flush=True)
sys.stdin.readline()
'''


def review_cases():
    """case 13 — five expired review-by triggers in the review scope make the line due, naming /autolearn."""
    import re
    with tempfile.TemporaryDirectory() as base:
        write_json(os.path.join(base, "self_evaluate_archive.json"), archive_with_rows(3))
        write_json(os.path.join(base, "logs", "eval_dashboard_last.json"), {"row_count": 3, "date": TODAY_ISO})
        write_json(os.path.join(base, "orchestration_candidates.json"), {"ok": True})
        rules = os.path.join(base, "rules")
        os.makedirs(rules, exist_ok=True)
        for i in range(5):
            with open(os.path.join(rules, "r%d.md" % i), "w", encoding="utf-8", newline="\n") as handle:
                handle.write("Rule.\n\n<!-- retire-when: review-by: 2020-01-01 -->\n")

        print("case 13 — five expired reviews are due and name their own command")
        proc = run(base, "--line")
        line = proc.stdout.strip()
        check("the line names 5 instruction reviews and /autolearn",
              "5 instruction reviews past their review-by date (run /autolearn)" in line, repr(line))
        check("a reviews-only line does not send the reader to /eval_dashboard",
              "run /eval_dashboard" not in line, repr(line))
        proc = run(base)
        check("report mode prints a DUE reviews row", re.search(r"^reviews:\s+DUE", proc.stdout, re.M) is not None,
              proc.stdout)

        print("case 14 — four expired reviews are not due")
        os.remove(os.path.join(rules, "r4.md"))
        proc = run(base, "--line")
        check("the line is empty with four expired reviews", proc.stdout.strip() == "", repr(proc.stdout))
        proc = run(base)
        check("report mode prints a live reviews row", re.search(r"^reviews:\s+live", proc.stdout, re.M) is not None,
              proc.stdout)


def in_process_cases():
    import contextlib
    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location("self_improvement_due_probe", TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    with tempfile.TemporaryDirectory() as base:
        archive = os.path.join(base, "self_evaluate_archive.json")
        write_json(archive, archive_with_rows(11))
        write_json(os.path.join(base, "orchestration_candidates.json"), {"ok": True})

        print("case 11 — --line loads the archive once")
        real = tool.store.effective_entries
        loads = []

        def counting(path):
            loads.append(path)
            return real(path)

        tool.store.effective_entries = counting
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = tool.main(["--root", base, "--today", TODAY_ISO, "--line"])
        finally:
            tool.store.effective_entries = real
        check("exit 0 and a due line", code == 0 and out.getvalue().startswith("Self-improvement due:"),
              repr(out.getvalue()))
        check("one archive load, not two", len(loads) == 1, "loads=%d" % len(loads))

        print("case 12 — a held ledger lock names the lock timeout, not an unreadable archive")
        lock = tool.store.ledger_path(archive) + ".lock"
        hooks_dir = os.path.join(ROOT, "hooks")
        holder = subprocess.Popen([sys.executable, "-c", HOLD_LOCK, hooks_dir, lock], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        saved_timeout = tool.store.LOCK_TIMEOUT_SECONDS
        try:
            check("holder process holds the ledger lock", holder.stdout.readline().strip() == "held")
            tool.store.LOCK_TIMEOUT_SECONDS = 0.3
            due, reason = tool.rows_due(base)
            check("a held lock is due with a lock-timeout reason",
                  due and "lock" in reason and "unreadable" not in reason, reason)
        finally:
            tool.store.LOCK_TIMEOUT_SECONDS = saved_timeout
            holder.stdin.write("\n")
            holder.stdin.flush()
            holder.wait(timeout=10)


def main():
    if not os.path.exists(TOOL):
        raise SystemExit("RED: %s does not exist" % TOOL)

    with tempfile.TemporaryDirectory() as base:
        archive = os.path.join(base, "self_evaluate_archive.json")
        stamp = os.path.join(base, "logs", "eval_dashboard_last.json")
        candidates = os.path.join(base, "orchestration_candidates.json")

        print("case 1 — missing stamp is due")
        write_json(archive, archive_with_rows(2))
        write_json(candidates, {"ok": True})
        proc = run(base)
        check("exit 0 on a plain report", proc.returncode == 0, proc.stderr)
        check("rows: no stamp reads as DUE", "rows:       DUE" in proc.stdout
              and "no eval_dashboard stamp" in proc.stdout, proc.stdout)

        print("case 2 — stamp present, small delta is live (not due)")
        write_json(stamp, {"row_count": 0, "date": (TODAY - datetime.timedelta(days=4)).isoformat()})
        write_json(archive, archive_with_rows(3))
        proc = run(base)
        check("rows: delta 3 < 10 reads as live", "rows:       live" in proc.stdout
              and "3 self-evaluate rows since the last stamp (< 10)" in proc.stdout, proc.stdout)

        print("case 3 — stamp present, large delta is due")
        write_json(archive, archive_with_rows(11))
        proc = run(base)
        check("rows: delta 11 >= 10 reads as DUE", "rows:       DUE" in proc.stdout
              and "11 self-evaluate rows since the last stamp (>= 10)" in proc.stdout, proc.stdout)
        write_json(archive, archive_with_rows(3))  # restore for later cases

        print("case 4 — candidates file age")
        proc = run(base)
        check("fresh candidates file reads as live",
              "candidates: live" in proc.stdout, proc.stdout)

        os.remove(candidates)
        proc = run(base)
        check("absent candidates file reads as DUE",
              "candidates: DUE" in proc.stdout and "is absent" in proc.stdout, proc.stdout)

        write_json(candidates, {"ok": True})
        old = time.time() - 8 * 86400
        os.utime(candidates, (old, old))
        proc = run(base)
        check("candidates older than 7 days reads as DUE",
              "candidates: DUE" in proc.stdout and "days old (> 7)" in proc.stdout, proc.stdout)

        recent = time.time() - 2 * 86400
        os.utime(candidates, (recent, recent))

        print("case 5 — --line is empty when nothing is due")
        proc = run(base, "--line")
        check("exit 0", proc.returncode == 0, proc.stderr)
        check("stdout is empty when neither check is due",
              proc.stdout.strip() == "", repr(proc.stdout))

        print("case 6 — --line names the reason when due")
        write_json(archive, archive_with_rows(11))
        proc = run(base, "--line")
        check("exit 0", proc.returncode == 0, proc.stderr)
        check("--line prints one non-empty line naming the row count and /eval_dashboard",
              proc.stdout.strip().startswith("Self-improvement due:")
              and "run /eval_dashboard" in proc.stdout
              and proc.stdout.count("\n") <= 1, repr(proc.stdout))
        write_json(archive, archive_with_rows(3))

        print("case 7 — --stamp writes the row count and date, then clears the row check")
        write_json(archive, archive_with_rows(11))
        proc = run(base, "--stamp")
        check("exit 0", proc.returncode == 0, proc.stderr)
        with open(stamp, encoding="utf-8") as handle:
            stamped = json.load(handle)
        check("stamp carries the live row count", stamped.get("row_count") == 11, str(stamped))
        check("stamp carries today's date", stamped.get("date") == TODAY_ISO, str(stamped))
        proc = run(base)
        check("row check reads live immediately after a stamp",
              "rows:       live" in proc.stdout and "0 self-evaluate rows since the last stamp" in proc.stdout,
              proc.stdout)

        print("case 8 — --stamp refuses on an unreadable archive")
        os.remove(archive)
        proc = run(base, "--stamp")
        check("--stamp exits 2 when the archive cannot be read", proc.returncode == 2, str(proc.returncode))
        check("--stamp names the refusal", "REFUSED" in proc.stdout, proc.stdout)
        write_json(archive, archive_with_rows(11))  # restore

        print("case 9 — --help and an unknown flag")
        help_proc = run_no_today(base, "--help")
        check("--help exits 0", help_proc.returncode == 0, str(help_proc.returncode))
        check("--help documents --line and --stamp",
              "--line" in help_proc.stdout and "--stamp" in help_proc.stdout, help_proc.stdout)
        bad_proc = run_no_today(base, "--nope")
        check("an unknown flag exits 2", bad_proc.returncode == 2, str(bad_proc.returncode))

        print("case 10 — --line and --stamp are mutually exclusive")
        both_proc = run(base, "--line", "--stamp")
        check("--line and --stamp together exits 2 (argparse mutual exclusion)",
              both_proc.returncode == 2, str(both_proc.returncode))

    in_process_cases()
    review_cases()

    print()
    if FAILURES:
        print("FAILED %d case(s): %s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("PASS — self_improvement_due.py proof green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
