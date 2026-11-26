#!/usr/bin/env python3
"""Re-runnable proof for hooks/activity_registry.py, the Python reader of the gate/suite activity registry.

Its decisions: which records are live (PID-verified, garbage swept), and which live runs block a working-tree
write on one checkout (`tree_write_blockers`). Each case plants a registry directory and calls the function.

    python3 .claude/tests/test_activity_registry_blockers.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.join(HERE, "..", "hooks")
sys.path.insert(0, HOOKS)
import activity_registry as reg  # noqa: E402

CHECKOUT = "C:/work/repo"
DEAD_PID = 2 ** 22 + 12345  # beyond any pid the OS hands out here


def plant(folder, name, **fields):
    rec = {"pid": os.getpid(), "kind": "gate", "checkout": CHECKOUT, "label": name,
           "startedAt": time.time(), "heartbeatAt": time.time(), **fields}
    path = os.path.join(folder, name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    return path


def labels(records):
    return [r["label"] for r in records]


def main():
    folder = tempfile.mkdtemp(prefix="actreg_")
    reg.REGISTRY_DIR = folder
    cases = []

    cases.append(("an empty registry blocks nothing", reg.tree_write_blockers(CHECKOUT) == []))
    plant(folder, "gate-run", kind="gate", startedAt=100)
    plant(folder, "suite-run", kind="suite", startedAt=200)
    plant(folder, "waiter-run", kind="waiter")
    plant(folder, "other-checkout", checkout="C:/work/elsewhere")
    got = labels(reg.tree_write_blockers(CHECKOUT))
    cases.append(("a live gate and suite on the checkout block, newest first", got == ["suite-run", "gate-run"]))
    cases.append(("a waiter holds nothing and is not a blocker", "waiter-run" not in got))
    cases.append(("a run on another checkout is not a blocker", "other-checkout" not in got))
    cases.append(("the checkout matches with a trailing slash",
                  labels(reg.tree_write_blockers(CHECKOUT + "/"))[:1] == ["suite-run"]))
    if os.name == "nt":
        cases.append(("the checkout matches across separators on Windows",
                      labels(reg.tree_write_blockers("C:\\work\\repo"))[:1] == ["suite-run"]))
    cases.append(("a blank checkout blocks nothing", reg.tree_write_blockers("") == []))

    dead = plant(folder, "dead-run", pid=DEAD_PID)
    cases.append(("a record whose pid is gone is not reported", "dead-run" not in labels(reg.tree_write_blockers(CHECKOUT))))
    cases.append(("and it is swept from the registry", not os.path.exists(dead)))
    junk = os.path.join(folder, "junk.json")
    with open(junk, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    reg.load_registry()
    cases.append(("an unparseable record is swept", not os.path.exists(junk)))
    cases.append(("the live records survive the sweep",
                  sorted(labels(reg.load_registry())) == sorted(["gate-run", "suite-run", "waiter-run", "other-checkout"])))

    cases.append(("a pid that is not an integer is not live", reg.is_live("12", None) is False and reg.is_live(None, None) is False))
    cases.append(("this process is live", reg.is_live(os.getpid(), None) is True))
    cases.append(("a missing pid is not live", reg.is_live(DEAD_PID, None) is False))
    cases.append(("a timestamp parses from an ISO string and from epoch seconds, else None",
                  reg.parse_ts("2026-11-25T10:00:00Z") == 1795600800.0 and reg.parse_ts(5) == 5.0
                  and reg.parse_ts("x") is None and reg.parse_ts(None) is None))
    if os.name == "nt":
        table = reg.proc_table_windows()
        cases.append(("the Windows snapshot lists this process with its parent",
                      os.getpid() in table and table[os.getpid()][0] == os.getppid()))

    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import activity_registry" % HOOKS],
                       capture_output=True, text=True, timeout=60)
    cases.append(("importing it cold prints nothing and exits 0", r.returncode == 0 and not r.stdout and not r.stderr))

    failures = [label for label, ok in cases if not ok]
    for label, ok in cases:
        print("%-4s %s" % ("ok" if ok else "FAIL", label))
    print("\n%d/%d cases pass" % (len(cases) - len(failures), len(cases)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
