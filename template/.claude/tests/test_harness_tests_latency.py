"""Re-runnable proof for harness_tests.py's latency record: queue wait, reuse, the run log, the close
wall line and its floor, the locked flaky log and the `--tiers` coverage table.

    python3 .claude/tests/test_harness_tests_latency.py
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
sys.path.insert(0, str(HERE))
import harness_tests as runner  # noqa: E402
from _harness_repo_case import RepoCase  # noqa: E402


def git(repo, *args):
    subprocess.run(["git"] + list(args), cwd=repo, check=True, capture_output=True, stdin=subprocess.DEVNULL)


def write(repo, rel, text):
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return str(path)


class Planted(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="harness_latency_")
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"HARNESS_SLOT_DIR": os.path.join(self.repo, "slots"),
                                "HARNESS_MACHINE_ROOT": os.path.join(self.repo, "slots"),
                                "HARNESS_SLOT_BUDGET": "",
                                "HARNESS_MACHINE_SLOTS": "1"}).start()
        for key, value in [("STAMP_PATH", os.path.join(self.repo, "stamp.json")), ("EXCLUDED", {}),
                           ("HEAVY", {}), ("SERIAL", {}), ("DURATIONS_PATH", None), ("PASSES_PATH", None),
                           ("FLAKY_LOG", None), ("RUNS_PATH", None)]:
            patch.object(runner, key, value, create=True).start()
        self.runs = os.path.join(self.repo, "harness_runs.json")

    def rows(self):
        with open(self.runs, encoding="utf-8") as fh:
            return json.load(fh)["runs"]


class LogPaths(Planted):
    def test_log_paths_follow_the_override_then_the_stamp(self):
        self.assertEqual(runner.runs_path(), self.runs)
        self.assertEqual(runner.durations_path(), os.path.join(self.repo, "harness_proof_durations.json"))
        patch.object(runner, "RUNS_PATH", "elsewhere/runs.json", create=True).start()
        patch.object(runner, "DURATIONS_PATH", "elsewhere/durations.json", create=True).start()
        self.assertEqual(runner.runs_path(), "elsewhere/runs.json")
        self.assertEqual(runner.durations_path(), "elsewhere/durations.json")


class QueueRecord(Planted):
    def test_queue_wait_recorded(self):
        self.queued_run()

    def test_queue_wait_holds_when_the_work_before_the_ask_is_slow(self):
        # A loaded machine spent 2.7 s on the runner's pre-launch git work and a fixed 4 s release left 1.3 s of
        # wait: the slots are held for a span counted from the runner's ask, never from the run's start.
        real_plan = runner._behavior_plan

        slow = threading.Event()

        def slow_plan(*args, **kwargs):
            if not slow.is_set():
                slow.set()
                time.sleep(4.5)
            return real_plan(*args, **kwargs)
        self.queued_run(slow_plan)

    def queued_run(self, slow_plan=None):
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        write(self.repo, ".claude/settings.json", "{}\n")
        quick = write(self.repo, ".claude/tests/test_quick.py", "import sys\nsys.exit(0)\n")
        queued = write(self.repo, ".claude/tests/test_queued.py", "import sys\nsys.exit(0)\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "seed")
        selected = [quick, queued]
        with contextlib.redirect_stdout(io.StringIO()):
            runner._run_selected(selected, False, {}, {}, runner.RunOptions(runner._pass_cache(self.repo, selected)))
        write(self.repo, ".claude/tests/test_queued.py", "import sys\n# edited\nsys.exit(0)\n")
        held = runner._take_slots(True)
        self.assertTrue(held)
        asked, real_launch = threading.Event(), runner._launch

        def launch(*args, **kwargs):
            asked.set()
            return real_launch(*args, **kwargs)

        def release_after_the_ask():
            if asked.wait(60):
                time.sleep(2.0)
            for handle in held:
                runner._file_lock.release(handle)
        releaser = threading.Thread(target=release_after_the_ask, daemon=True)
        releaser.start()
        self.addCleanup(releaser.join)
        self.addCleanup(asked.set)
        row = {}
        with contextlib.redirect_stdout(io.StringIO()), patch.object(runner, "_launch", launch), \
                patch.object(runner, "_behavior_plan", slow_plan or runner._behavior_plan):
            runner._run_selected(selected, False, {}, {},
                                 runner.RunOptions(runner._pass_cache(self.repo, selected), mode="for", row=row))
        releaser.join()
        by_proof = {p["proof"]: p for p in row["proofs"]}
        self.assertGreaterEqual(by_proof["test_queued.py"]["wait_s"], 1.5, row)
        self.assertFalse(by_proof["test_queued.py"]["reused"], row)
        self.assertTrue(by_proof["test_quick.py"]["reused"], row)
        self.assertEqual(by_proof["test_quick.py"]["wait_s"], 0, row)
        self.assertEqual((row["mode"], row["selected"], row["reused"]), ("for", 2, 1), row)
        self.assertGreaterEqual(row["queue_s"], 1.5, row)
        self.assertTrue(isinstance(row.get("width"), int) and row["width"] > 0, row)
        self.assertEqual(self.rows()[-1]["mode"], "for")
        self.assertEqual(self.rows()[-1]["proofs"][0].keys(),
                         {"proof", "status", "run_s", "wait_s", "reused"})

    def test_slots_off_waits_zero(self):
        proof = write(self.repo, "t/test_zero.py", "import sys\nsys.exit(0)\n")
        os.environ["HARNESS_MACHINE_SLOTS"] = "0"
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-latency-01"
        row = {}
        with contextlib.redirect_stdout(io.StringIO()):
            runner._run_selected([proof], False, {}, {}, runner.RunOptions(mode="all", row=row))
        self.assertEqual(row["proofs"][0]["wait_s"], 0, row)
        self.assertEqual(row["session"], "sess-latency-01")

    def test_201st_row_evicts_the_oldest(self):
        for n in range(201):
            runner._record_run({"ts": n, "mode": "for", "proofs": []})
        rows = self.rows()
        self.assertEqual(len(rows), 200)
        self.assertEqual((rows[0]["ts"], rows[-1]["ts"]), (1, 200))

    def test_evicted_rows_fold_into_a_per_day_history(self):
        """Rows past the 200 kept leave a per-day total (runs, proofs selected and reused, wall and queue seconds,
        runs per mode), so a close-cost audit months later still has the days the rows no longer cover."""
        day = 86400
        start = 1_700_000_000 - 1_700_000_000 % day
        for n in range(207):
            runner._record_run({"ts": start + n * 7200, "mode": "close" if n % 2 else "for", "selected": 3,
                                "reused": 1, "wall_s": 10.0, "queue_s": 4.0, "pool_s": 6.0, "proofs": []})
        state = json.loads(open(runner.runs_path(), encoding="utf-8").read())
        folded = state.get("folded", [])
        self.assertEqual(len(state["runs"]), 200)
        self.assertEqual(sum(d["runs"] for d in folded), 7, folded)
        self.assertEqual(sum(d["wall_s"] for d in folded), 70.0)
        self.assertEqual(sum(d["selected"] for d in folded), 21)
        self.assertEqual(sum(d["reused"] for d in folded), 7)
        self.assertEqual(sum(d["modes"].get("close", 0) + d["modes"].get("for", 0) for d in folded), 7)
        self.assertEqual([d["day"] for d in folded], sorted(d["day"] for d in folded))
        self.assertEqual(len({d["day"] for d in folded}), len(folded))

    def test_the_folded_history_is_bounded(self):
        runner._record_run({"ts": 1, "mode": "for", "proofs": []})
        with open(runner.runs_path(), encoding="utf-8") as fh:
            state = json.load(fh)
        state["folded"] = [{"day": "2000-01-%02d" % (n % 28 + 1) + "-%d" % n, "runs": 1, "wall_s": 1.0, "queue_s": 0.0,
                            "pool_s": 0.0, "selected": 0, "reused": 0, "modes": {}} for n in range(runner._FOLDED_KEPT + 50)]
        with open(runner.runs_path(), "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        for n in range(202):
            runner._record_run({"ts": 100 + n, "mode": "for", "proofs": []})
        self.assertLessEqual(len(json.load(open(runner.runs_path(), encoding="utf-8"))["folded"]), runner._FOLDED_KEPT)


def _ran(proof, run_s, status="pass", reused=False):
    return {"proof": proof, "status": status, "run_s": run_s, "reused": reused}


class BudgetLine(Planted):
    # Two earlier closes: test_a's fastest pass is 80s, test_b's 30s, test_c's 10s; a failed or reused row never
    # counts as a pass time.
    HISTORY = [{"ts": 1, "proofs": [_ran("test_a.py", 100.0), _ran("test_b.py", 30.0), _ran("test_c.py", 10.0),
                                    _ran("test_d.py", 1.0, status="fail")]},
               {"ts": 2, "proofs": [_ran("test_a.py", 80.0), _ran("test_b.py", 0.0, reused=True)]}]

    def test_budget_line_names_a_slow_proof_and_queue_wait(self):
        row = {"wall_s": 42.0, "queue_s": 3.5,
               "proofs": [{"proof": "test_slow.py", "run_s": 12.0},
                          {"proof": "test_fast.py", "run_s": 0.4}]}
        line = runner._budget_line(row, [])
        self.assertIn("test_slow.py 12.0s", line)
        self.assertNotIn("test_fast.py", line)
        self.assertNotIn("300s", line)
        self.assertIn("queue wait 3.5s", line)

    def test_the_floor_is_the_longest_fastest_pass_when_it_outlasts_the_pool_share(self):
        # Run: test_a, test_b and test_d (no pass recorded); test_c reused. Fastest passes 80 + 30 = 110s over
        # 2 slots is 55s, under test_a's 80s, so even an unloaded close cannot finish before 80s.
        row = {"ts": 3, "wall_s": 200.0, "queue_s": 0.0, "width": 2,
               "proofs": [_ran("test_a.py", 120.0), _ran("test_b.py", 40.0), _ran("test_c.py", 0.0, reused=True),
                          _ran("test_d.py", 5.0)]}
        line = runner._budget_line(row, self.HISTORY + [row])
        self.assertTrue(line.startswith("harness_tests: close wall: 200.0s, floor 80s from fastest recorded passes "
                                        "(110.0s summed over 2 slots, longest test_a.py 80.0s; "
                                        "1 of 3 run proofs with no recorded pass); "), line)

    def test_the_floor_is_the_pool_share_when_it_outlasts_the_longest_proof(self):
        row = {"ts": 3, "wall_s": 90.0, "queue_s": 0.0, "width": 1,
               "proofs": [_ran("test_a.py", 90.0), _ran("test_b.py", 31.0), _ran("test_c.py", 11.0)]}
        line = runner._budget_line(row, self.HISTORY)
        self.assertIn("close wall: 90.0s, floor 120s from fastest recorded passes (120.0s summed over 1 slot, "
                      "longest test_a.py 80.0s; 0 of 3 run proofs with no recorded pass)", line)

    def test_no_floor_without_a_width_or_a_recorded_pass(self):
        row = {"ts": 3, "wall_s": 9.0, "queue_s": 0.0, "proofs": [_ran("test_a.py", 9.0)]}
        self.assertIn("close wall: 9.0s, floor unknown (no pool width recorded)", runner._budget_line(row, self.HISTORY))
        row["width"] = 4
        self.assertIn("close wall: 9.0s, floor unknown (no run proof has a recorded pass)", runner._budget_line(row, []))

    def test_budget_line_lists_at_most_five_slowest(self):
        row = {"wall_s": 1.0, "queue_s": 0.0,
               "proofs": [{"proof": "test_p%d.py" % n, "run_s": 11.0 + n} for n in range(8)]}
        line = runner._budget_line(row, [])
        self.assertEqual(sum(1 for n in range(8) if "test_p%d.py" % n in line), 5)
        self.assertIn("test_p7.py", line)
        self.assertNotIn("test_p0.py", line)


class CloseRecord(RepoCase):
    def test_close_prints_the_budget_line_and_records_its_mode(self):
        self.plant()
        self.commit("harness")
        patch.object(runner, "RUNS_PATH", None, create=True).start()
        rc, out = self.close()
        self.assertEqual(rc, 0, out)
        self.assertIn("harness_tests: close wall: ", out)
        self.assertNotIn("close budget", out)
        rows = json.loads((self.repo / "harness_runs.json").read_text())["runs"]
        self.assertEqual(rows[-1]["mode"], "close")
        self.assertTrue(rows[-1]["proofs"])


class TiersRows(Planted):
    def test_tiers_rows(self):
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        write(self.repo, ".claude/settings.json", "{}\n")
        write(self.repo, ".claude/tools/alpha.py", "A = 1\n")
        write(self.repo, ".claude/tools/beta.py", "B = 1\n")
        proofs = {"test_alpha.py": "# exercises alpha.py\n", "test_orphan.py": "import sys\n",
                  "test_scan.py": "import sys\n", "test_serial.py": "# exercises beta.py\n",
                  "test_heavy.py": "# exercises beta.py\n", "test_new.py": "# exercises beta.py\n",
                  "test_off.py": "# exercises beta.py\n"}
        for name, text in proofs.items():
            write(self.repo, ".claude/tests/" + name, text)
        git(self.repo, "add", "-A")
        git(self.repo, "add", "-f", ".claude/tests")
        git(self.repo, "commit", "-q", "-m", "seed")
        stamp = runner.STAMP_PATH
        with open(os.path.join(os.path.dirname(stamp), "harness_proof_durations.json"), "w", encoding="utf-8") as fh:
            json.dump({"test_alpha.py": 4.0, "test_orphan.py": 20.0, "test_scan.py": 40.0, "test_serial.py": 31.0,
                       "test_heavy.py": 9.0, "test_off.py": 1.0}, fh)
        with patch.object(runner, "_DIR_SCANNING", ("test_scan.py",)), \
                patch.object(runner, "SERIAL", {"test_serial.py": "owns the machine"}), \
                patch.object(runner, "HEAVY", {"test_heavy.py": "slow"}), \
                patch.object(runner, "EXCLUDED", {"test_off.py": "off"}):
            rows = {r["proof"]: r for r in runner.tiers(self.repo)}
        self.assertEqual(set(rows), set(proofs))
        self.assertEqual((rows["test_alpha.py"]["seconds"], rows["test_alpha.py"]["budget"],
                          rows["test_alpha.py"]["runs"]), (4.0, "inner", "bound"))
        self.assertEqual((rows["test_orphan.py"]["budget"], rows["test_orphan.py"]["runs"]), ("commit", "bound"))
        self.assertEqual((rows["test_scan.py"]["budget"], rows["test_scan.py"]["runs"]), ("close", "always"))
        self.assertEqual(rows["test_serial.py"]["runs"], "serial")
        self.assertEqual(rows["test_heavy.py"]["runs"], "heavy")
        self.assertEqual(rows["test_off.py"]["runs"], "excluded")
        self.assertEqual(rows["test_new.py"]["seconds"], "unrecorded")
        self.assertEqual(rows["test_alpha.py"]["trigger"]["examples"], [".claude/tools/alpha.py"])
        self.assertEqual(rows["test_orphan.py"]["trigger"]["count"], 0)
        self.assertEqual(rows["test_orphan.py"]["trigger"]["examples"], [])
        self.assertIn("no-trigger", rows["test_orphan.py"]["flags"])
        self.assertIn("slow-always", rows["test_scan.py"]["flags"])
        self.assertEqual(rows["test_alpha.py"]["flags"], [])
        text = runner.format_tiers(list(rows.values()))
        self.assertIn("test_orphan.py", text)
        self.assertIn("none", text)


class CorpusBinding(Planted):
    def test_a_corpus_scanning_proof_binds_every_file_under_its_corpus(self):
        write(self.repo, ".claude/guards/unnamed.md", "rule\n")
        write(self.repo, ".claude/tools/elsewhere.py", "X = 1\n")
        write(self.repo, ".claude/tests/test_corpus.py", "import sys\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "add", "-f", ".claude/tests")
        with patch.object(runner, "_CORPUS_SCANNING", {"test_corpus.py": (".claude/guards",)}):
            bound = runner.bindings(self.repo, [".claude/guards/unnamed.md", ".claude/tools/elsewhere.py"])
        names = {t: [os.path.basename(p) for p in ps] for t, ps in bound.items()}
        self.assertIn("test_corpus.py", names[".claude/guards/unnamed.md"])
        self.assertNotIn("test_corpus.py", names[".claude/tools/elsewhere.py"])


class FlakyLog(Planted):
    def test_a_trim_keeps_every_row_the_flaky_count_reads(self):
        # 450 rows inside the reader's window and 50 older: the trim drops only the older ones.
        log = os.path.join(self.repo, "harness_flaky.jsonl")
        now = time.time()
        with open(log, "w", encoding="utf-8", newline="\n") as fh:
            for n in range(50):
                fh.write(json.dumps({"ts": now - 40 * 86400 - n, "proof": "test_old.py"}) + "\n")
            for n in range(450):
                fh.write(json.dumps({"ts": now - n * 3000, "proof": "test_recent.py"}) + "\n")
        runner._record_flaky("test_recent.py", "FAIL x", 1.0)
        self.assertEqual(runner._flaky_history(), {"test_recent.py": 451})
        with open(log, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().splitlines()), 451)

    def test_two_writers_both_survive_the_cap(self):
        log = os.path.join(self.repo, "harness_flaky.jsonl")
        with open(log, "w", encoding="utf-8", newline="\n") as fh:
            for n in range(400):
                fh.write(json.dumps({"ts": n, "proof": "old%d" % n}) + "\n")
        real_replace = os.replace
        in_replace = threading.Event()

        def slow_replace(src, dst):
            if not in_replace.is_set():
                in_replace.set()
                time.sleep(0.6)
            return real_replace(src, dst)

        first = threading.Thread(target=runner._record_flaky, args=("first.py", "FAIL x", 1.0))
        second = threading.Thread(target=runner._record_flaky, args=("second.py", "FAIL y", 1.0))
        with patch.object(os, "replace", slow_replace), patch.object(os, "getpid", threading.get_ident):
            first.start()
            self.assertTrue(in_replace.wait(10))
            second.start()
            first.join()
            second.join()
        with open(log, encoding="utf-8") as fh:
            names = [json.loads(line)["proof"] for line in fh.read().splitlines()]
        self.assertIn("first.py", names)
        self.assertIn("second.py", names)
        self.assertLessEqual(len(names), 400)


if __name__ == "__main__":
    unittest.main()
