"""Re-runnable proof for one admission point: the publish worktree's checks and a proof run beside
it share the machine slot store, so neither can oversubscribe the box, and a proof that nests case
workers takes one slot, so the pool keeps its width.

    python3 .claude/tests/test_harness_tests_admission.py
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import harness_tests as runner  # noqa: E402

PUBLISH = HERE.parent / "tools" / "baseline_publish.py"

# Both a publish check and a harness proof mark start/end with one file each, so the test reads
# real process overlap instead of trusting either pool's width. One file per event: a shared append
# from many Windows processes can interleave into blank lines, which measures the OS, not the pools.
RECORDER = r'''
import os, sys, time
log = os.environ["ADMISSION_LOG"]
name = os.path.basename(sys.argv[0].replace("\\", "/"))

def emit(kind):
    open(os.path.join(log, kind + "__" + name + "__" + repr(time.time())), "x").close()

emit("start")
time.sleep(0.4)
emit("end")
'''


def write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


def load_publish():
    spec = importlib.util.spec_from_file_location("baseline_publish_under_admission", PUBLISH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def events(log_dir):
    found = []
    for entry in os.listdir(log_dir):
        kind, name, at = entry.split("__")
        found.append((float(at), kind == "end", name))
    return found


def peak(events):
    running = best = 0
    for _, closing, _ in sorted(events):
        running += -1 if closing else 1
        best = max(best, running)
    return best


class Admission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="harness_admission_")
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.addCleanup(patch.stopall)
        self.log = os.path.join(self.root, "admission_events")
        os.makedirs(self.log)
        patch.dict(os.environ, {
            "HARNESS_SLOT_DIR": os.path.join(self.root, "slots"),
            "HARNESS_MACHINE_SLOTS": "4",
            "HARNESS_TEST_JOBS": "4",
            "HARNESS_SLOT_BUDGET": "",
            "HARNESS_MACHINE_ROOT": os.path.join(self.root, "slots"),
            "ADMISSION_LOG": self.log,
        }).start()
        for key, value in [("STAMP_PATH", os.path.join(self.root, "stamp.json")),
                           ("DURATIONS_PATH", os.path.join(self.root, "durations.json")),
                           ("EXCLUDED", {}), ("HEAVY", {}), ("SERIAL", {})]:
            patch.object(runner, key, value, create=True).start()
        patch.object(runner, "_behavior_plan",
                     lambda root, paths=None: {"proofs": [], "cases": {}, "unresolved": []}).start()

    def test_publish_checks_and_a_proof_run_share_the_slot_budget(self):
        worktree = Path(self.root) / "publish"
        for tool in ("tools/gen_manifest.py", "tools/audit_baseline.py"):
            write(worktree, tool, RECORDER)
        for index in range(10):
            write(worktree, "tests/test_check_%d.py" % index, RECORDER)
        proofs = [write(self.root, ".claude/tests/test_proof_%d.py" % i, RECORDER) for i in range(6)]
        publish = load_publish()
        failures = []

        def run_proofs():
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    runner._run_selected(proofs, False, {}, {})
            except Exception as exc:  # noqa: BLE001 - surfaced in the test's own failure
                failures.append(exc)

        thread = threading.Thread(target=run_proofs)
        thread.start()
        time.sleep(0.2)
        header = publish._validate(worktree, [], False)
        thread.join()
        self.assertFalse(failures, failures)
        self.assertIn("mode=", header)
        most = peak(events(self.log))
        self.assertLessEqual(most, 4, "%d processes ran at once on 4 machine slots" % most)
        self.assertGreaterEqual(most, 2, "both sides never overlapped, so nothing was proven")

    def test_a_case_pool_proof_takes_one_slot_so_the_pool_keeps_its_width(self):
        # A nested case pool is bounded inside the proof by HARNESS_CASE_JOBS. Charging it to the
        # machine budget instead put 3 slots on each such proof and cut the pool to a third of its width.
        proof = write(self.root, ".claude/tests/test_pooled.py", "import sys\nsys.exit(0)  # _case_pool\n")
        plain = write(self.root, ".claude/tests/test_plain.py", "import sys\nsys.exit(0)\n")
        os.environ["HARNESS_MACHINE_SLOTS"] = "0"
        os.environ["HARNESS_CASE_JOBS"] = "2"
        seen = []
        real = runner._take_slots

        def spy(exclusive=False, count=1):
            seen.append((exclusive, count))
            return real(exclusive, count)

        patch.object(runner, "_take_slots", spy).start()
        with contextlib.redirect_stdout(io.StringIO()):
            runner._launch(proof)
            runner._launch(plain)
        self.assertEqual(seen, [(False, 1), (False, 1)], seen)

    def test_a_pool_of_case_pool_proofs_runs_its_full_width(self):
        os.environ["HARNESS_MACHINE_SLOTS"] = "4"
        os.environ["HARNESS_CASE_JOBS"] = "2"
        proofs = [write(self.root, ".claude/tests/test_pooled_%d.py" % index,
                        RECORDER + "\n# _case_pool\n") for index in range(8)]
        with contextlib.redirect_stdout(io.StringIO()):
            runner._run_selected(proofs, False, {}, {})
        most = peak(events(self.log))
        self.assertEqual(most, 4, "a 4-slot pool of case-pool proofs ran %d at once" % most)

    def test_the_pool_admits_the_slowest_recorded_proof_first(self):
        # A short proof admitted ahead of the longest one held the machine slots and started the
        # longest proof 23 s late on a real close selection. The selection below is ascending, so
        # only a sort by recorded duration submits it in the order asserted.
        proofs = [write(self.root, ".claude/tests/test_timed_%d.py" % i, "import sys\nsys.exit(0)\n")
                  for i in range(4)]
        names = [os.path.basename(p) for p in proofs]
        Path(runner.DURATIONS_PATH).write_text(json.dumps(dict(zip(names, [0.5, 0.6, 4.0, 5.0]))), encoding="utf-8")
        order = []

        class Spy(ThreadPoolExecutor):
            def submit(self, fn, *args, **kwargs):
                defaults = fn.__defaults__ or ()
                order.append(os.path.basename(defaults[0]) if defaults else "?")
                return super().submit(fn, *args, **kwargs)

        patch.object(runner, "ThreadPoolExecutor", Spy).start()
        with contextlib.redirect_stdout(io.StringIO()):
            runner._run_selected(proofs, False, {}, {})
        self.assertEqual(order, [names[3], names[2], names[1], names[0]], order)


if __name__ == "__main__":
    unittest.main()
