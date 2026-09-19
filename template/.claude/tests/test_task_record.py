#!/usr/bin/env python3
"""Proof for tools/task_record.py: one durable record per task, bounded, locked, checkable.

    python3 .claude/tests/test_task_record.py
"""
import importlib.util
import json
import multiprocessing
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "task_record.py")
sys.stdout.reconfigure(encoding="utf-8")
FAILURES = []


def check(label, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", label))
    if not ok:
        FAILURES.append(label + (" :: " + str(detail)[:300] if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location("task_record", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cli(*args, cwd):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, encoding="utf-8",
                          cwd=cwd, timeout=60)


def worker(args):
    root, i = args
    os.environ["HARNESS_TASK_RECORD_DIR"] = root
    mod = load()
    mod.add_item("t-lock", "requirement", "req %d" % i)
    return True


def main():
    root = tempfile.mkdtemp(prefix="task_record_")
    os.environ["HARNESS_TASK_RECORD_DIR"] = root
    try:
        tr = load()
    except Exception as exc:
        check("tools/task_record.py imports", False, repr(exc))
        print("\n%d failure(s)" % len(FAILURES))
        return 1
    check("tools/task_record.py imports", True)

    rec = tr.open_record("s1-drive", session_id="s1", title="Drive")
    check("open writes a record with every field present", all(k in rec for k in (
        "task_id", "session_id", "title", "opened", "updated", "requirements", "exclusions", "decisions", "scope",
        "phase", "active_jobs", "verification", "accepted_artifacts", "next_action")), sorted(rec))
    tr.set_fields("s1-drive", phase="build", next_action="run proofs")
    check("set updates phase and next action", tr.load("s1-drive")["phase"] == "build"
          and tr.load("s1-drive")["next_action"] == "run proofs")

    evidence = os.path.join(root, "evidence.txt")
    with open(evidence, "w", encoding="utf-8") as fh:
        fh.write("v1\n")
    d = tr.add_item("s1-drive", "decision", "keep the hook", evidence=[evidence])
    check("a decision gets an id and hashed evidence", d["id"] == "D1" and d["evidence"][0]["sha256"]
          and d["evidence"][0]["path"] == evidence, d)
    tr.add_item("s1-drive", "requirement", "no push")
    tr.add_item("s1-drive", "exclusion", "no .cs edits")
    tr.add_job("s1-drive", run_id="wf_1", label="review:a", source="workflow", state="running")
    v = tr.add_verification("s1-drive", check="proof", result="pass", evidence=[evidence])
    check("job and verification rows land", tr.load("s1-drive")["active_jobs"][0]["run_id"] == "wf_1"
          and v["result"] == "pass" and v["evidence"][0]["sha256"])

    big = "x" * (tr.MAX_BYTES + 200)
    try:
        tr.add_item("s1-drive", "requirement", big)
        check("a write past MAX_BYTES is refused and names the field", False, "no refusal")
    except tr.RecordTooLarge as exc:
        check("a write past MAX_BYTES is refused and names the field", "requirements" in str(exc), str(exc))
    check("the refused write left the record unchanged", len(tr.load("s1-drive")["requirements"]) == 1)

    # freshness check: fresh -> STALE -> MISSING; a decision without a reproduction is hash-only
    rows = tr.check_record("s1-drive")
    check("check reports fresh rows and hash-only for a decision without a reproduction",
          all(r["status"] == "fresh" for r in rows) and any(r.get("repro") == "hash-only" for r in rows), rows)
    with open(evidence, "w", encoding="utf-8") as fh:
        fh.write("v2\n")
    rows = tr.check_record("s1-drive")
    check("a changed evidence file reads STALE", any(r["status"] == "STALE" for r in rows), rows)
    os.remove(evidence)
    rows = tr.check_record("s1-drive")
    check("a removed evidence file reads MISSING", any(r["status"] == "MISSING" for r in rows), rows)
    bad = tr.add_item("s1-drive", "decision", "reproduce it", reproduction=sys.executable + " -c \"import sys; sys.exit(3)\"")
    rows = tr.check_record("s1-drive", rerun=True)
    check("check --rerun reports REPRO-FAIL for a failing stored reproduction",
          any(r["id"] == bad["id"] and r.get("repro") == "REPRO-FAIL" for r in rows), rows)
    tr.archive_item("s1-drive", bad["id"])
    check("archive moves a decision out of the record",
          all(x["id"] != bad["id"] for x in tr.load("s1-drive")["decisions"])
          and any(x["id"] == bad["id"] for x in tr.load("s1-drive").get("archived", [])))

    # usage errors surface as KeyError (the CLI's REFUSED path), never as a substitute OSError
    try:
        tr.add_item("never-opened", "requirement", "x")
        check("a mutator on a record that was never opened raises KeyError naming the task", False, "no error")
    except KeyError as exc:
        check("a mutator on a record that was never opened raises KeyError naming the task",
              "never-opened" in str(exc), str(exc))
    except Exception as exc:  # noqa: BLE001
        check("a mutator on a record that was never opened raises KeyError naming the task", False, repr(exc))
    try:
        tr.archive_item("s1-drive", "D99")
        check("archiving an unknown decision id raises KeyError naming it", False, "no error")
    except KeyError as exc:
        check("archiving an unknown decision id raises KeyError naming it", "D99" in str(exc), str(exc))
    except Exception as exc:  # noqa: BLE001
        check("archiving an unknown decision id raises KeyError naming it", False, repr(exc))
    r = cli("add", "never-opened", "requirement", "x", cwd=root)
    check("the CLI reports a usage error as REFUSED with exit 1, not a traceback",
          r.returncode == 1 and r.stderr.startswith("REFUSED:") and "Traceback" not in r.stderr,
          (r.returncode, r.stderr[:200]))

    tr.open_record("s1-later", session_id="s1", title="Later")
    check("active picks the newest record of the session", tr.active("s1")["task_id"] == "s1-later")
    check("active is None for an unknown session", tr.active("nope") is None)

    # bounded block: requirements and exclusions whole, decisions water-filled, omissions named
    tr.open_record("s1-block", session_id="s1b", title="Block")
    for i in range(3):
        tr.add_item("s1-block", "requirement", "requirement %d" % i)
    for i in range(18):
        tr.add_item("s1-block", "decision", "decision %d " % i + "y" * 60)
    check("18 decisions of ~75 B fit under the record cap (first data point for the cap)",
          len(json.dumps(tr.load("s1-block")).encode("utf-8")) <= tr.MAX_BYTES, len(json.dumps(tr.load("s1-block")).encode("utf-8")))
    blk = tr.render_block("s1-block", max_bytes=700)
    check("the block fits its byte cap", len(blk.encode("utf-8")) <= 700, len(blk.encode("utf-8")))
    check("requirements stay whole in a tight block", all(("requirement %d" % i) in blk for i in range(3)), blk)
    check("omitted decisions are named with the show command", "omitted" in blk and "task_record.py show s1-block" in blk, blk)
    check("an empty session renders no block", tr.render_block("missing-task", max_bytes=700) == "")

    # CLI round trip and locking under concurrent writers
    r = cli("open", "c-1", "--session", "c", "--title", "CLI", cwd=root)
    r2 = cli("show", "c-1", cwd=root)
    check("CLI open then show round-trips", r.returncode == 0 and r2.returncode == 0
          and json.loads(r2.stdout)["task_id"] == "c-1", (r.stderr, r2.stdout[:200]))
    tr.open_record("t-lock", session_id="l", title="Lock")
    with multiprocessing.Pool(4) as pool:
        pool.map(worker, [(root, i) for i in range(12)])
    check("12 concurrent adds through the lock all survive", len(tr.load("t-lock")["requirements"]) == 12,
          len(tr.load("t-lock")["requirements"]))

    print("\n%d failure(s)" % len(FAILURES) if FAILURES else "\nall ok")
    for f in FAILURES:
        print("  " + f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
