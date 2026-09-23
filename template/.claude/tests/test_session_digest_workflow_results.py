#!/usr/bin/env python3
import importlib.util
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / ".claude" / "tools" / "session_digest.py"
SPEC = importlib.util.spec_from_file_location("session_digest", TOOL)
digest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(digest)


def finding(index: int, padding: str = "") -> dict:
    return {
        "agent": "lens",
        "action": "FIX",
        "category": "bug",
        "critical": index % 7 == 0,
        "file": f"src/{index}.py:1",
        "description": f"finding-{index}{padding}",
        "rationale": f"evidence-{index}{padding}",
    }


def claim(index: int, padding: str = "") -> dict:
    return {
        "subject": f"subject-{index}",
        "polarity": "exists",
        "claim": f"claim-{index}{padding}",
        "evidence": f"evidence-{index}{padding}",
        "verification": None,
        "file": f"src/{index}.py:1",
        "bearing": "context",
        "confidence": "verified",
    }


def write_journal(run_dir: Path, rows: list[dict | str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    text = "\n".join(row if isinstance(row, str) else json.dumps(row, separators=(",", ":"))
                     for row in rows) + "\n"
    (run_dir / "journal.jsonl").write_text(text, encoding="utf-8", newline="\n")


def started(key: str, label: str, agent_id: str | None = None) -> dict:
    return {"type": "started", "key": key, "label": label,
            "agentId": agent_id or "a" + key, "phase": "Review"}


def result(key: str, value: object, agent_id: str | None = None) -> dict:
    return {"type": "result", "key": key, "agentId": agent_id or "a" + key, "result": value}


def failed(key: str, agent_id: str | None = None) -> dict:
    return {"type": "failed", "key": key, "agentId": agent_id or "a" + key}


class WorkflowResultTests(unittest.TestCase):
    def make_review(self, count: int, padding: str = "") -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        run_dir = Path(temp.name) / "wf_test"
        write_journal(run_dir, [
            {"type": "launched"},
            started("one", "review:one"),
            result("one", {"findings": [finding(i, padding) for i in range(count)]}),
        ])
        return temp, run_dir

    def test_manifest_is_result_size_independent_and_accounts_for_all_sources(self):
        small_temp, small_dir = self.make_review(1)
        large_temp, large_dir = self.make_review(500, "λ" * 4000)
        self.addCleanup(small_temp.cleanup)
        self.addCleanup(large_temp.cleanup)
        small_archive = digest.build_workflow_result_archive(small_dir, "review")
        small = digest.workflow_result_manifest(small_archive)
        large = digest.workflow_result_manifest(digest.build_workflow_result_archive(large_dir, "review"))
        self.assertEqual(small, digest.workflow_result_manifest(small_archive))
        self.assertEqual(large["counts"]["items"], 500)
        self.assertEqual(large["counts"]["sourceLenses"], 1)
        self.assertEqual(large["counts"]["omittedFromManifest"], 501)
        self.assertNotIn("items", large)
        self.assertLess(len(json.dumps(large).encode("utf-8")), 4096)
        self.assertLess(abs(len(json.dumps(large)) - len(json.dumps(small))), 256)
        self.assertEqual(large["journal"]["sha256"], hashlib.sha256(
            (large_dir / "journal.jsonl").read_bytes()).hexdigest())
        self.assertTrue(large["delivered"])

    def test_completion_order_does_not_change_started_order_source_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_order"
            write_journal(run_dir, [
                started("one", "review:one"), started("two", "review:two"),
                result("two", {"findings": [finding(2)]}),
                result("one", {"findings": [finding(1), finding(3)]}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual([(row["id"], row["lens"], row["finding"]["description"])
                              for row in archive["items"]],
                             [("F1", "one", "finding-1"), ("F2", "one", "finding-3"),
                              ("F3", "two", "finding-2")])

    def test_pages_recover_every_source_once_and_are_idempotent(self):
        temp, run_dir = self.make_review(117)
        self.addCleanup(temp.cleanup)
        archive = digest.build_workflow_result_archive(run_dir, "review")
        seen = []
        for page_number in range(1, 8):
            page = digest.workflow_result_page(archive, "items", page_number, 19)
            self.assertEqual(page, digest.workflow_result_page(archive, "items", page_number, 19))
            seen.extend(item["id"] for item in page["rows"])
        self.assertEqual(seen, [f"F{i}" for i in range(1, 118)])
        self.assertEqual(len(seen), len(set(seen)))
        self.assertTrue(page["complete"])
        self.assertIsNone(page["nextPage"])

    def test_select_recovers_exact_omitted_evidence_and_full_is_lossless(self):
        temp, run_dir = self.make_review(25)
        self.addCleanup(temp.cleanup)
        archive = digest.build_workflow_result_archive(run_dir, "review")
        selected = digest.workflow_result_select(archive, ["F25", "F1"])
        self.assertEqual([item["finding"]["description"] for item in selected["rows"]],
                         ["finding-24", "finding-0"])
        full = digest.workflow_result_full(archive)
        self.assertEqual(full["items"], archive["items"])
        self.assertEqual(full["counts"]["items"], 25)
        self.assertEqual(selected, digest.workflow_result_select(archive, ["F25", "F1"]))
        with self.assertRaisesRegex(ValueError, "duplicate workflow selector"):
            digest.workflow_result_select(archive, ["F1", "F1"])

    def test_hash_mismatch_and_missing_or_malformed_journal_fail_loudly(self):
        temp, run_dir = self.make_review(2)
        self.addCleanup(temp.cleanup)
        archive = digest.build_workflow_result_archive(run_dir, "review")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            digest.verify_workflow_journal_hash(archive, "0" * 64)
        with tempfile.TemporaryDirectory() as missing:
            with self.assertRaisesRegex(ValueError, "journal"):
                digest.build_workflow_result_archive(Path(missing) / "wf_missing", "review")
        malformed = Path(temp.name) / "wf_malformed"
        write_journal(malformed, [{"type": "launched"}, "{broken"])
        with self.assertRaisesRegex(ValueError, "malformed journal"):
            digest.build_workflow_result_archive(malformed, "review")

    def test_failed_lens_and_malformed_result_remain_named_and_undelivered(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_partial"
            write_journal(run_dir, [
                started("good", "review:good"), started("bad", "review:bad"),
                started("dead", "review:dead"),
                result("good", {"findings": [finding(1)]}),
                result("bad", {"findings": "wrong"}), failed("dead"),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual(archive["status"], "uncovered")
            self.assertFalse(archive["delivered"])
            self.assertEqual([(row["label"], row["status"]) for row in archive["lenses"]],
                             [("review:good", "completed"), ("review:bad", "uncovered"),
                              ("review:dead", "failed")])
            self.assertEqual([item["id"] for item in archive["items"]], ["F1"])
            self.assertEqual(digest.workflow_result_page(archive, "items", 1, 10)["status"],
                             "uncovered")

    def test_partial_source_skips_malformed_entry_and_keeps_valid_neighbors(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_partial_entry"
            write_journal(run_dir, [started("one", "review:one"), result("one", {
                "findings": [finding(1), None, finding(2)],
            })])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual(archive["status"], "partial")
            self.assertFalse(archive["delivered"])
            self.assertEqual(archive["counts"]["rejected"], 1)
            self.assertEqual([item["id"] for item in archive["items"]], ["F1", "F2"])

    def test_resumed_attempts_collapse_to_latest_success_per_key(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_resumed"
            write_journal(run_dir, [
                started("one", "review:one", "a-old-one"),
                started("two", "review:two", "a-old-two"),
                result("one", {"findings": [finding(1)]}, "a-old-one"),
                failed("two", "a-old-two"),
                started("one", "review:one", "a-new-one"),
                started("two", "review:two", "a-new-two"),
                result("one", {"findings": [finding(1)]}, "a-new-one"),
                result("two", {"findings": [finding(2)]}, "a-new-two"),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual(archive["status"], "completed")
            self.assertTrue(archive["delivered"])
            self.assertEqual([(row["id"], row["lens"], row["finding"]["description"])
                              for row in archive["items"]],
                             [("F1", "one", "finding-1"), ("F2", "two", "finding-2")])
            self.assertEqual([row["agentId"] for row in archive["lenses"]],
                             ["a-new-one", "a-new-two"])
            self.assertEqual(archive["counts"]["starts"], 2)
            self.assertEqual(archive["counts"]["terminals"], 2)
            self.assertEqual(archive["counts"]["attempts"], 4)
            self.assertEqual(archive["counts"]["attemptTerminals"], 4)

    def test_duplicate_or_orphan_runtime_rows_are_rejected(self):
        cases = [
            [started("one", "review:one"), started("one", "review:one")],
            [started("one", "review:one"), failed("one"), failed("one")],
            [result("orphan", {"findings": []})],
            [started("one", "review:one", "a1"), result("one", {"findings": []}, "a2")],
        ]
        for index, rows in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temp:
                run_dir = Path(temp) / "wf_invalid"
                write_journal(run_dir, rows)
                with self.assertRaises(ValueError):
                    digest.build_workflow_result_archive(run_dir, "review")

    def test_review_reports_page_and_merge_auxiliary_never_consume_f_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_reports"
            write_journal(run_dir, [
                started("one", "review:one"), started("merge", "review:consolidate"),
                result("merge", {"findings": [{"merged_from": ["F1"]}]}),
                result("one", {"findings": [finding(1)], "report": "full lens report"}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual([item["id"] for item in archive["items"]], ["F1"])
            self.assertEqual(digest.workflow_result_page(archive, "reports", 1, 20)["rows"][0]["report"],
                             "full lens report")
            self.assertEqual(archive["lenses"][1]["role"], "auxiliary")
            self.assertEqual(archive["merges"], [{"id": "M1", "merged_from": ["F1"]}])
            self.assertEqual(digest.workflow_result_page(archive, "merges", 1, 20)["rows"],
                             archive["merges"])

    def test_invalid_consolidation_partition_is_not_reported_as_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_invalid_merge"
            write_journal(run_dir, [
                started("one", "review:one"), started("merge", "review:consolidate"),
                result("one", {"findings": [finding(1), finding(2)]}),
                result("merge", {"findings": [{"merged_from": ["F1"]}]}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertNotEqual(archive["status"], "completed")
            self.assertEqual(archive["merges"], [])
            self.assertTrue(any("partition" in issue for row in archive["lenses"]
                                if row["role"] == "auxiliary" for issue in row["issues"]))

        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_explore"
            checked = {"toolsUsed": ["Read:x"], "stoppedAt": "exhausted-leads", "basis": "fixture"}
            write_journal(run_dir, [started("one", "explore:one"), result("one", {
                "claims": [claim(1), None, claim(2)], "checked": checked,
                "gaps": ["gap one", "gap two"],
            })])
            archive = digest.build_workflow_result_archive(run_dir, "explore")
            self.assertEqual(archive["status"], "partial")
            self.assertEqual([item["id"] for item in archive["items"]], ["C1", "C2"])
            self.assertEqual([item["claim"]["subject"] for item in archive["items"]],
                             ["subject-1", "subject-2"])
            self.assertEqual([row["gap"] for row in digest.workflow_result_page(
                archive, "gaps", 1, 20)["rows"]], ["gap one", "gap two"])

    def test_cli_manifest_select_page_and_full_are_callable_and_hash_checked(self):
        temp, run_dir = self.make_review(8)
        self.addCleanup(temp.cleanup)
        base = [sys.executable, str(TOOL), "--workflow-dir", str(run_dir),
                "--workflow-kind", "review"]
        manifest_run = subprocess.run(base + ["--workflow-manifest"], text=True, encoding="utf-8",
                                      capture_output=True, check=False)
        self.assertEqual(manifest_run.returncode, 0, manifest_run.stderr)
        manifest = json.loads(manifest_run.stdout)
        expected_hash = manifest["journal"]["sha256"]
        page_run = subprocess.run(base + ["--workflow-page", "items", "--page", "2", "--page-size", "3",
                                          "--expect-journal-sha256", expected_hash],
                                  text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(page_run.returncode, 0, page_run.stderr)
        self.assertEqual([row["id"] for row in json.loads(page_run.stdout)["rows"]], ["F4", "F5", "F6"])
        select_run = subprocess.run(base + ["--workflow-select", "F8", "--workflow-select", "F1",
                                            "--expect-journal-sha256", expected_hash],
                                    text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(select_run.returncode, 0, select_run.stderr)
        self.assertEqual([row["id"] for row in json.loads(select_run.stdout)["rows"]], ["F8", "F1"])
        full_run = subprocess.run(base + ["--workflow-full", "--expect-journal-sha256", expected_hash],
                                  text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(full_run.returncode, 0, full_run.stderr)
        self.assertEqual(len(json.loads(full_run.stdout)["items"]), 8)
        mismatch = subprocess.run(base + ["--workflow-page", "items", "--expect-journal-sha256", "0" * 64],
                                  text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertEqual(mismatch.stdout, "")
        self.assertIn("hash mismatch", mismatch.stderr)
        missing_hash = subprocess.run(base + ["--workflow-full"], text=True, encoding="utf-8",
                                      capture_output=True, check=False)
        self.assertNotEqual(missing_hash.returncode, 0)
        self.assertEqual(missing_hash.stdout, "")
        self.assertIn("requires --expect-journal-sha256", missing_hash.stderr)


if __name__ == "__main__":
    unittest.main()
