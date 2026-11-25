"""Proof: a runner started under another runner's read recorder gives its proof one recorder, its own.

A proof that runs the runner (a nested run) inherits the outer runner's `PYTHONPATH` and `NODE_OPTIONS`. When the outer
runner's recorder folder is a different path than the inner one's (a land runs the runner from one checkout and its
proofs from an export), prepending the inner recorder left two on the path: the sitecustomize it shadows was the
other recorder, which ran as a second recorder no `retarget` reaches. Every read then also landed in the proof's
base log, so no `_case_pool` case had reads of its own and no case was ever reused.

    python3 .claude/tests/test_harness_tests_nested_recorder.py
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

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


def check_startup_beacon(parent, name, command, env):
    log = Path(parent) / (name + '_reads')
    done = subprocess.run(command, cwd=parent, env=dict(env, HARNESS_READ_LOG=str(log)),
                          capture_output=True, text=True, timeout=20)
    check(name + ' starts without a recorder fault', done.returncode == 0 and not done.stderr, done.stderr)
    if done.returncode != 0 or not done.stdout.strip().isdigit():
        check(name + ' reports its PID', False, done.stdout)
        return None
    pid = int(done.stdout)
    beacon = log / ('%d.beacon' % pid)
    check(name + ' writes its startup beacon', beacon.is_file(), beacon)
    if not beacon.is_file():
        return None
    payload = json.loads(beacon.read_text(encoding='utf-8'))
    check(name + ' beacon records the interpreter, PID and log',
          (payload['recorder'], payload['pid'], Path(payload['log'])) == (name, pid, log), payload)
    return beacon


def check_beacon_and_zero_events(parent):
    recorder = Path(harness_tests._RECORDER_DIR)
    env = dict(os.environ, PYTHONPATH=str(recorder), PYTHONNOUSERSITE='1', NODE_OPTIONS='')
    beacon = check_startup_beacon(parent, 'python', [sys.executable, '-c', 'import os; print(os.getpid())'], env)
    node = shutil.which('node')
    if node:
        check_startup_beacon(parent, 'node', [node, '-e', 'console.log(process.pid)'],
                             dict(env, NODE_OPTIONS='--require ' + json.dumps(str(recorder / 'node_recorder.js'))))
    else:
        print('skip node beacon: node is not on PATH')

    if beacon is None:
        return
    shim = Path(parent) / 'shim' / '_read_recorder'
    shim.mkdir(parents=True)
    (shim / 'sitecustomize.py').write_text('''import os, shutil
log = os.environ["HARNESS_READ_LOG"]
beacon = os.environ["BEACON_FIXTURE"]
os.makedirs(log, exist_ok=True)
shutil.copyfile(beacon, os.path.join(log, os.path.basename(beacon)))
''', encoding='utf-8')
    proof = Path(parent) / 'silent.py'
    proof.write_text('pass\n', encoding='utf-8')
    sink = {}
    with patch.dict(os.environ, dict(env, PYTHONPATH=str(shim), BEACON_FIXTURE=str(beacon),
                                     HARNESS_READ_LOG=os.path.join(parent, 'runner_reads'),
                                     HARNESS_READ_RECORDER=str(shim))):
        status, _, detail = harness_tests.run_proof(str(proof), reads_sink=sink)
    check('the launched recorder leaves a beacon but no events',
          status == 'pass' and sink.get('events') == [] and sink.get('log_files') == 0
          and sink.get('beacons') == 1 and not sink.get('faults'), (detail, sink))
    reason = harness_tests._unobserved_evidence(sink, None)
    check('a loaded zero-event recorder gets a reason without an import fault',
          bool(reason) and 'beacon' in reason and '1' in reason
          and not any(fault in reason.lower() for fault in ('sitecustomize', 'import', 'module')), reason)


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
        check_beacon_and_zero_events(parent)
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    print("%d failure(s)" % len(FAILS) if FAILS else "all cases pass")
    for line in FAILS:
        print("  " + line)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
