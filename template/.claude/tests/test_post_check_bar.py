"""post_check honors the finding bar: a below-bar finding is never held, never a failed check and never a refold
target; the report names the run's rigor and lists the below-bar ids. The archive merge keeps the strongest tag."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import post_check  # noqa: E402
import session_digest  # noqa: E402


def row(fid, action="FIX", **extra):
    return dict({"id": fid, "action": action, "category": "bug", "critical": False, "file": "src/a.py:1",
                 "description": fid, "rationale": "r", "old": None, "new": None}, **extra)


def deferred(fid, klass):
    return {"id": fid, "class": klass, "reason": "fixture"}


def job(applied=(), deferred_rows=()):
    return {"digestValue": {"applied": [{"id": i} for i in applied], "deferred": list(deferred_rows)}}


def item(fid, lens, minrigor):
    return {"id": fid, "lens": lens, "label": "review:" + lens, "index": 0,
            "finding": {"agent": lens, "action": "FIX", "category": "bug", "critical": False,
                        "file": "design.md:374", "description": fid, "rationale": "r", "minRigor": minrigor}}


class ArchiveMergeBarTests(unittest.TestCase):
    def test_archive_merge_keeps_the_strongest_minrigor(self):
        archive = {"status": "completed", "items": [item("F1", "rt-boundary", "thorough"),
                                                    item("F2", "rt-failuremode", "normal")],
                   "merges": [{"id": "M1", "merged_from": ["F1", "F2"]}]}
        with mock.patch.object(session_digest, "build_workflow_result_archive", return_value=archive):
            rows, _ = post_check._archive_rows(Path("."), merge=True)
        merged = [r for r in rows if r["id"] == "M1"]
        self.assertEqual(len(merged), 1, rows)
        self.assertEqual(merged[0]["minRigor"], "normal")


class HeldBarTests(unittest.TestCase):
    def test_below_bar_deferral_is_not_held(self):
        rows = [row("F1", minRigor="thorough")]
        held = post_check.held_rows(rows, [job(deferred_rows=[deferred("F1", "below-bar")])], rigor="light")
        self.assertEqual(held, [])

    def test_below_bar_class_on_a_counting_row_is_held(self):
        rows = [row("F1", minRigor="light")]
        held = post_check.held_rows(rows, [job(deferred_rows=[deferred("F1", "below-bar")])], rigor="light")
        self.assertEqual([(entry["id"], entry["class"]) for entry in held], [("F1", "below-bar")])

    def test_unfolded_below_bar_fix_is_not_held_unfolded(self):
        self.assertEqual(post_check.held_rows([row("F1", minRigor="thorough")], [], rigor="light"), [])

    def test_below_bar_ask_is_still_held(self):
        rows = [row("F1", action="ASK", question="Keep it?", options=["keep"], minRigor="thorough")]
        held = post_check.held_rows(rows, [], rigor="light")
        self.assertEqual([(entry["id"], entry["class"]) for entry in held], [("F1", "ask")])

    def test_no_resolvable_rigor_counts_every_finding(self):
        held = post_check.held_rows([row("F1", minRigor="thorough")], [], rigor=None)
        self.assertEqual([(entry["id"], entry["class"]) for entry in held], [("F1", "unfolded")])

    def test_report_buckets_cover_every_row(self):
        rows = [row("F1", minRigor="light"), row("F2", minRigor="normal"), row("F3", minRigor="thorough"),
                row("F4", action="ASK", question="q", options=["a"], minRigor="thorough")]
        jobs = [job(applied=["F1"], deferred_rows=[deferred("F2", "below-bar"), deferred("F3", "below-bar")])]
        ids = [r["id"] for r in rows]
        for rigor in ("light", "normal", "thorough"):
            with self.subTest(rigor=rigor):
                held = post_check.held_rows(rows, jobs, rigor=rigor)
                below = post_check.below_bar_ids(rows, jobs, held, rigor)
                buckets = [entry["id"] for entry in held] + below + ["F1"]
                self.assertEqual(sorted(buckets), sorted(ids), (rigor, held, below))


class ReportBarTests(unittest.TestCase):
    def test_report_names_rigor_and_below_bar_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = root / "records"
            records.mkdir()
            (records / "bar-task.json").write_text(json.dumps({"task_id": "bar-task", "session_id": "bar-session",
                                                               "rigor": "light"}), encoding="utf-8")
            findings = root / "findings.json"
            findings.write_text(json.dumps({"findings": [row("F1", minRigor="thorough"),
                                                         row("F2", minRigor="light")]}), encoding="utf-8")
            out = root / "report.json"
            env = {"HARNESS_TASK_RECORD_DIR": str(records), "CLAUDE_PROJECT_DIR": str(root)}
            with mock.patch.dict(os.environ, env):
                post_check.main(["review", "--findings", str(findings), "--root", str(root),
                                 "--task", "bar-task", "--out", str(out)])
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["rigor"], "light")
            self.assertEqual(report["belowBar"], ["F1"])
            held_ids = [entry["id"] for entry in report["held"]]
            self.assertNotIn("F1", held_ids)
            self.assertIn("F2", held_ids)
            self.assertNotIn("F1", report["nextRound"]["refold"]["held"])


if __name__ == "__main__":
    unittest.main()
