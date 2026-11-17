"""Proof: a runner started under another runner's read recorder gives its proof one recorder, its own.

A proof that runs the runner (a nested run) inherits the outer runner's `PYTHONPATH` and `NODE_OPTIONS`. When the outer
runner's recorder folder is a different path than the inner one's (a land runs the runner from one checkout and its
proofs from an export), prepending the inner recorder left two on the path: the sitecustomize it shadows was the
other recorder, which ran as a second recorder no `retarget` reaches. Every read then also landed in the proof's
base log, so no `_case_pool` case had reads of its own and no case was ever reused.

    python3 .claude/tests/test_harness_tests_nested_recorder.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import harness_tests  # noqa: E402

FAILS = []

PROOF = '''import os, sys
import sitecustomize
sitecustomize.retarget(os.path.join(os.environ["HARNESS_READ_LOG"], "cases", "x"))
open(os.environ["MARKER"]).read()
with open(os.environ["MARKER"] + ".out", "w") as fh:
    fh.write("%d %d" % (sum(1 for p in os.environ["PYTHONPATH"].split(os.pathsep) if os.path.basename(p) == "_read_recorder"),
                        os.environ["NODE_OPTIONS"].count("node_recorder.js")))
'''


def check(name, ok, detail=""):
    print("%-4s %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        FAILS.append("%s: %s" % (name, detail))


def main():
    parent = tempfile.mkdtemp(prefix="harnessnestedrec_")
    try:
        outer = os.path.join(parent, "outer", "_read_recorder")
        shutil.copytree(harness_tests._RECORDER_DIR, outer)
        marker = os.path.join(parent, "marker.txt")
        proof = os.path.join(parent, "proof.py")
        for path, text in ((marker, "x"), (proof, PROOF)):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        saved = dict(os.environ)
        os.environ.update(
            MARKER=marker, PYTHONPATH=outer, HARNESS_READ_LOG=os.path.join(parent, "outer_log"),
            NODE_OPTIONS="--require " + harness_tests.json.dumps(os.path.join(outer, "node_recorder.js")))
        sink = {}
        try:
            status, _elapsed, detail = harness_tests.run_proof(proof, reads_sink=sink)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        check("the nested proof runs", status == "pass", detail[-300:])
        with open(marker + ".out", encoding="utf-8") as fh:
            counts = fh.read()
        check("the child's PYTHONPATH and NODE_OPTIONS name one recorder each", counts == "1 1", counts)
        read = lambda events: any(e[0] == "r" and os.path.normcase(e[1]) == os.path.normcase(marker) for e in events)
        check("a read after retarget is in the case's log", read(sink.get("cases", {}).get("x", [])), sink.get("cases"))
        check("a read after retarget is not in the proof's base log", not read(sink.get("base", [])),
              "the marker read reached the base log: a second recorder ran")
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    print("%d failure(s)" % len(FAILS) if FAILS else "all cases pass")
    for line in FAILS:
        print("  " + line)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
