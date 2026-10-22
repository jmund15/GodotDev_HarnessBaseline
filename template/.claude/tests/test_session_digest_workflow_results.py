#!/usr/bin/env python3
import importlib.util
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / ".claude" / "tools" / "session_digest.py"
P19_FIXTURE = {
    "runLog": {"args": {"agents": [{"key": key} for key in (
        "plc-memory-alignment", "plc-pattern-fit", "plc-architecture-quality",
        "plc-test-readiness", "plc-evidence-grounding", "plc-instruction-quality",
        "plc-doctrine-consistency",
    )]}},
    "startOrder": [
        "plc-memory-alignment", "plc-pattern-fit", "plc-test-readiness",
        "plc-instruction-quality", "plc-doctrine-consistency",
        "plc-architecture-quality", "plc-evidence-grounding",
    ],
    "findingsByKeyBeforeInstruction": {
        "plc-memory-alignment": 7, "plc-pattern-fit": 9,
        "plc-architecture-quality": 6, "plc-test-readiness": 10,
        "plc-evidence-grounding": 9,
    },
    "engineF43": {
        "agent": "plc-instruction-quality", "action": "FIX", "category": "rule",
        "critical": True, "file": "plan §Closing steps:1",
        "description": "No step verifies the three harness doc edits. The only absence check, `rg -n \"EmissiveSprite|emissive_sprite|IsJudgedThroughSource\" --glob '!.claude/**' .`, excludes `.claude/`, so stale overlay references in skills and memory pass unchecked. Slice 7 Docs states no done-condition.",
        "rationale": "instruction_quality §10: each work-starting step needs a checkable done-condition. §14: an absence assertion fails open unless a positive control fires first. §4 and §17: cited paths and claims are checked against current code.",
    },
    "p19FoldRows": [
        {"type": "started", "key": "v2:39c2e9f757bc54cb50b256bb4dfd4d8b089a3a187341a211708395c41fbf5afa", "agentId": "a69602aa1c8d34f36", "label": "review:fold", "phase": "Fold"},
        {"type": "result", "key": "v2:39c2e9f757bc54cb50b256bb4dfd4d8b089a3a187341a211708395c41fbf5afa", "agentId": "a69602aa1c8d34f36", "result": {}},
        {"type": "started", "key": "v2:9e69cb7207819fb204608a1a064305ef3e128d55d8e6d7670e1aac4f8e55dab7", "agentId": "a31f21212e8f60340", "label": "review:fold", "phase": "Fold"},
        {"type": "result", "key": "v2:9e69cb7207819fb204608a1a064305ef3e128d55d8e6d7670e1aac4f8e55dab7", "agentId": "a31f21212e8f60340", "result": {}},
    ],
}
SPEC = importlib.util.spec_from_file_location("session_digest", TOOL)
digest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(digest)


def valid_finding(value: object) -> bool:
    return digest.review_result.finding_problem(value) is None


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


def started(key: str, label: str, agent_id: str | None = None, phase: str = "Review") -> dict:
    return {"type": "started", "key": key, "label": label,
            "agentId": agent_id or "a" + key, "phase": phase}


def result(key: str, value: object, agent_id: str | None = None) -> dict:
    return {"type": "result", "key": key, "agentId": agent_id or "a" + key, "result": value}


def failed(key: str, agent_id: str | None = None) -> dict:
    return {"type": "failed", "key": key, "agentId": agent_id or "a" + key}


class WorkflowResultTests(unittest.TestCase):
    def test_both_archives_apply_evidence_verdicts_and_ignore_prose_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source.txt'
            source.write_text('A true source sentence.', encoding='utf-8')
            for kind in ('explore', 'review'):
                rows = [claim(i) if kind == 'explore' else finding(i) for i in range(3)]
                for row, quote in zip(rows, ('A true source sentence.', 'Fabricated sentence.', None)):
                    row['evidence'] = [{'file': str(source), 'quote': quote}] if quote else []
                    row['confidence'] = 'verified'
                    row['verification'] = {'retired': 'ignored'}
                run = root / kind
                value = {('claims' if kind == 'explore' else 'findings'): rows,
                         'checked': {'toolsUsed': ['Read:source.txt'], 'stoppedAt': 'exhausted-leads', 'basis': 'read'}}
                write_journal(run, [started('one', kind + ':one', phase=kind.title()), result('one', value)])
                archive = digest.build_workflow_result_archive(run, kind)
                self.assertEqual(archive['counts']['items'], 3)
                key = 'claim' if kind == 'explore' else 'finding'
                self.assertEqual([item[key]['verdict'] for item in archive['items']],
                                 ['VERIFIED', 'UNVERIFIED', 'UNCHECKED'])
                self.assertEqual([item[key]['confidence'] for item in archive['items']],
                                 ['verified', 'unverified', 'unverified'])
                self.assertEqual(archive['verification']['counts'],
                                 dict(VERIFIED=1, UNVERIFIED=1, UNCHECKED=1))
                self.assertEqual(digest.workflow_result_full(archive)['verification'], archive['verification'])

    def test_archive_reads_the_host_subject_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / 'session/subagents/workflows/wf_root'
            (root / 'source.txt').write_text('Only in the subject root.', encoding='utf-8')
            row = claim(1)
            row.update(file='source.txt:1', evidence=[{'file': 'source.txt:1', 'quote': 'Only in the subject root.'}])
            write_journal(run, [started('one', 'explore:one', phase='Explore'), result('one', {
                'claims': [row], 'checked': {'toolsUsed': ['Read:source.txt'], 'stoppedAt': 'exhausted-leads', 'basis': 'read'}})])
            record = root / 'session/workflows/wf_root.json'
            record.parent.mkdir(parents=True)
            record.write_text(json.dumps({'args': {'root': str(root)}}), encoding='utf-8')
            self.assertEqual(digest.build_workflow_result_archive(run, 'explore')['items'][0]['claim']['verdict'], 'VERIFIED')

    def test_review_archive_keeps_the_fold_subject_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / 'session/subagents/workflows/wf_subject'
            (root / 'source.txt').write_text('Only in the fold subject.', encoding='utf-8')
            row = finding(1)
            row.update(file='source.txt:1', evidence=[{'file': 'source.txt:1', 'quote': 'Only in the fold subject.'}])
            write_journal(run, [started('one', 'review:one'), result('one', {'findings': [row]})])
            record = root / 'session/workflows/wf_subject.json'
            record.parent.mkdir(parents=True)
            record.write_text(json.dumps({'args': {'fold': {'subjects': [
                {'root': str(root), 'files': ['source.txt']}]}}}), encoding='utf-8')
            self.assertEqual(digest.build_workflow_result_archive(run, 'review')['items'][0]['finding']['verdict'], 'VERIFIED')
            other = root / 'other'
            other.mkdir()
            (other / 'source.txt').write_text('Only in the fold subject.', encoding='utf-8')
            record.write_text(json.dumps({'args': {'fold': {'subjects': [
                {'root': str(root), 'files': ['source.txt']},
                {'root': str(other), 'files': ['source.txt']}]}}}), encoding='utf-8')
            archive = digest.build_workflow_result_archive(run, 'review')
            self.assertEqual(archive['items'][0]['finding']['verdict'], 'UNCHECKED')
            self.assertTrue(archive['verification']['errors'])

    def make_review(self, count: int, padding: str = "") -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        run_dir = Path(temp.name) / "wf_test"
        write_journal(run_dir, [
            {"type": "launched"},
            started("one", "review:one"),
            result("one", {"findings": [finding(i, padding) for i in range(count)]}),
        ])
        return temp, run_dir

    def test_optional_finding_fields_follow_engine_validation(self):
        valid = finding(1)
        valid.update(refuting=True, evidence=[{"file": "src/a.py:1", "quote": "text"}])
        self.assertTrue(valid_finding(valid))
        for kind in ({"command": "git grep -n x -- src", "output": ""}, {"owner": "words"},
                     {"url": "https://example.test", "quote": "text"}):
            with self.subTest(kind=kind):
                self.assertTrue(valid_finding(dict(valid, evidence=[kind])))
        for bad in (dict(valid, refuting="true"), dict(valid, evidence="text"),
                    dict(valid, evidence=[{"file": "src/a.py:1"}]),
                    dict(valid, evidence=[{"file": 3, "quote": "text"}]),
                    dict(valid, evidence=[{"command": "git grep -n x"}])):
            with self.subTest(bad=bad):
                self.assertFalse(valid_finding(bad))

    def test_a_file_location_is_validated_with_whitespace_stripped(self):
        # One `path:line` rule: review_result.LOCATION, matched on the stripped value here and there.
        valid = finding(1)
        self.assertTrue(valid_finding(dict(valid, file="  src/a.py:1  ")))
        for bad_file in ("src/a.py", "src/a.py:0", "src/a.py: 1", "src/a.py:-1", "  "):
            with self.subTest(file=bad_file):
                self.assertFalse(valid_finding(dict(valid, file=bad_file)))

    def test_p19_engine_order_uses_run_log_and_only_engine_findings(self):
        fixture = P19_FIXTURE
        run_log = fixture["runLog"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = root / "subagents" / "workflows" / "wf_p19"
            rows = []
            source_results = {}
            invalid = finding(900)
            invalid["file"] = "not-a-path-line"
            for key, count in fixture["findingsByKeyBeforeInstruction"].items():
                source_results[key] = [finding(i) for i in range(count)]
            # The rejected entry holds F42, so the valid finding after it is F43.
            source_results["plc-instruction-quality"] = [invalid, fixture["engineF43"]]
            for key in fixture["startOrder"]:
                rows.append(started(key, "review:" + key))
                rows.append(result(key, {"findings": source_results.get(key, [])}))
            write_journal(run_dir, rows)
            run_path = root / "workflows" / "wf_p19.json"
            run_path.parent.mkdir(parents=True)
            run_path.write_text(json.dumps(run_log), encoding="utf-8", newline="\n")

            archive = digest.build_workflow_result_archive(run_dir, "review")
            selected = digest.workflow_result_select(archive, ["F43"])
            self.assertEqual(selected["rows"][0]["finding"]["description"],
                             fixture["engineF43"]["description"])
            self.assertEqual(digest.workflow_result_manifest(archive)["selectors"]["sourceOrder"],
                             "run-log args.agents order, then item-array order")

    def test_merge_partitions_only_candidate_ids_and_numbers_multi_member_groups(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_candidates"
            first, second, solo = finding(1), finding(2), finding(3)
            first["file"] = "src/shared.py:1"
            second["file"] = "src/shared.py:99"
            write_journal(run_dir, [
                started("one", "review:one"), started("two", "review:two"),
                started("merge", "review:consolidate", phase="Merge"),
                result("one", {"findings": [first, solo]}),
                result("two", {"findings": [second]}),
                result("merge", {"findings": [
                    {"merged_from": ["F1", "F3"]},
                ]}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual(archive["status"], "completed")
            self.assertEqual(archive["merges"], [{"id": "M1", "merged_from": ["F1", "F3"]}])

    def test_review_stage_rows_do_not_degrade_seat_coverage(self):
        fixture = P19_FIXTURE
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_stages"
            write_journal(run_dir, [
                started("seat", "review:seat"), result("seat", {"findings": [finding(1)]}),
                started("merge", "review:consolidate", phase="Merge"),
                result("merge", {"findings": [{"merged_from": ["F1"]}]}),
                started("verify", "review:verify", phase="Verify"), result("verify", {"digest": "verified"}),
                *fixture["p19FoldRows"],
                started("fold2", "review:fold", phase="Fold"), failed("fold2"),
                started("fold3", "review:fold", phase="Fold"),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual(archive["status"], "completed")
            self.assertEqual([(row["phase"], row["role"], row["status"])
                              for row in archive["lenses"]], [
                                  ("Review", "source", "completed"),
                                  ("Merge", "auxiliary", "uncovered"),
                                  ("Verify", "stage", "completed"),
                                  ("Fold", "stage", "completed"),
                                  ("Fold", "stage", "completed"),
                                  ("Fold", "stage", "failed"),
                                  ("Fold", "stage", "failed"),
                              ])
            self.assertEqual(archive["counts"]["completed"], 4)
            self.assertEqual(archive["counts"]["failed"], 2)
            self.assertEqual(archive["counts"]["uncovered"], 1)
            self.assertEqual(archive["counts"]["missingTerminals"], 1)

    def test_engine_merge_and_fold_labels_keep_a_folded_run_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_engine_labels"
            write_journal(run_dir, [
                started("seat", "review:seat"), result("seat", {"findings": [finding(1)]}),
                started("merge", "review:merge", phase="Merge"),
                result("merge", {"findings": [{"merged_from": ["F1"]}]}),
                started("fold", "review:fold", phase="Fold"), result("fold", {"digest": "folded"}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual(archive["status"], "completed")
            self.assertEqual([(row["label"], row["role"]) for row in archive["lenses"]],
                             [("review:seat", "source"), ("review:merge", "auxiliary"),
                              ("review:fold", "stage")])

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

    def test_launched_seat_order_numbers_sources_before_the_run_record_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_inrun"
            write_journal(run_dir, [
                started("two", "review:two"), started("one", "review:one"),
                result("two", {"findings": [finding(2)]}),
                result("one", {"findings": [finding(1), finding(3)]}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review", seat_order=["one", "two"])
            self.assertEqual(archive["idOrder"], "engine")
            self.assertEqual([(row["id"], row["lens"]) for row in archive["items"]],
                             [("F1", "one"), ("F2", "one"), ("F3", "two")])

    def test_completion_order_does_not_change_started_order_source_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_order"
            write_journal(run_dir, [
                started("one", "review:one"), started("two", "review:two"),
                result("two", {"findings": [finding(2)]}),
                result("one", {"findings": [finding(1), finding(3)]}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual(archive["idOrder"], "start-order")
            self.assertEqual(digest.workflow_result_manifest(archive)["selectors"]["sourceOrder"],
                             "started-agent order, then item-array order")
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

    def test_select_returns_every_row_of_an_id_the_digest_holds_twice(self):
        temp, run_dir = self.make_review(3)
        self.addCleanup(temp.cleanup)
        archive = digest.build_workflow_result_archive(run_dir, "review")
        archive["items"].append(dict(archive["items"][1]))
        selected = digest.workflow_result_select(archive, ["F2", "F1"])
        self.assertEqual([row["id"] for row in selected["rows"]], ["F2", "F2", "F1"])
        self.assertEqual(selected["rows"][0], selected["rows"][1])

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
            self.assertEqual([item["id"] for item in archive["items"]], ["F1", "F3"])

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
                started("one", "review:one"), started("merge", "review:consolidate", phase="Merge"),
                result("merge", {"findings": [{"merged_from": ["F1"]}]}),
                result("one", {"findings": [finding(1)], "report": "full lens report"}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual([item["id"] for item in archive["items"]], ["F1"])
            self.assertEqual(digest.workflow_result_page(archive, "reports", 1, 20)["rows"][0]["report"],
                             "full lens report")
            self.assertEqual(archive["lenses"][1]["role"], "auxiliary")
            self.assertEqual(archive["merges"], [])
            self.assertEqual(digest.workflow_result_page(archive, "merges", 1, 20)["rows"],
                             archive["merges"])

    def test_invalid_consolidation_partition_is_not_reported_as_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_invalid_merge"
            write_journal(run_dir, [
                started("one", "review:one"), started("merge", "review:consolidate", phase="Merge"),
                result("one", {"findings": [finding(1), finding(2)]}),
                result("merge", {"findings": [{"merged_from": ["F1"]}]}),
            ])
            archive = digest.build_workflow_result_archive(run_dir, "review")
            self.assertEqual(archive["merges"], [])
            self.assertEqual(archive["status"], "completed")
            self.assertTrue(any("candidate" in issue for row in archive["lenses"]
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
            self.assertEqual([item["id"] for item in archive["items"]], ["C1", "C3"])
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

    def test_workflow_full_falls_back_to_the_archive_and_states_the_replay_gap(self):
        temp, run_dir = self.make_review(8)
        self.addCleanup(temp.cleanup)
        base = [sys.executable, str(TOOL), "--workflow-dir", str(run_dir), "--workflow-kind", "review"]
        manifest = json.loads(subprocess.run(base + ["--workflow-manifest"], text=True, encoding="utf-8",
                                             capture_output=True, check=True).stdout)
        full_run = subprocess.run(base + ["--workflow-full", "--expect-journal-sha256", manifest["journal"]["sha256"]],
                                  text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(full_run.returncode, 0, full_run.stderr)
        full = json.loads(full_run.stdout)
        self.assertEqual(full["source"], "archive")
        self.assertTrue(full["replayGap"].startswith("workflow_replay: transcript directory "), full["replayGap"])
        self.assertEqual(len(full["items"]), 8)
        self.assertNotIn("result", full)

    def test_workflow_full_returns_the_local_replay_of_a_recorded_run(self):
        fixture = ROOT / ".claude" / "tests" / "fixtures" / "workflow_replay" / "session"
        run_id = "wf_7c3b636b-5e2"
        with tempfile.TemporaryDirectory(prefix="digest-replay-") as tmp:
            session = Path(tmp) / "session"
            shutil.copytree(fixture, session)
            run_dir = session / "subagents" / "workflows" / run_id
            base = [sys.executable, str(TOOL), "--workflow-dir", str(run_dir), "--workflow-kind", "explore"]
            manifest_run = subprocess.run(base + ["--workflow-manifest"], text=True, encoding="utf-8",
                                          capture_output=True, check=False)
            self.assertEqual(manifest_run.returncode, 0, manifest_run.stderr)
            expected_hash = json.loads(manifest_run.stdout)["journal"]["sha256"]
            full_run = subprocess.run(base + ["--workflow-full", "--expect-journal-sha256", expected_hash],
                                      text=True, encoding="utf-8", capture_output=True, check=False)
            self.assertEqual(full_run.returncode, 0, full_run.stderr)
            full = json.loads(full_run.stdout)
            recorded = json.loads((session / "workflows" / (run_id + ".json")).read_text(encoding="utf-8"))
            self.assertEqual(full["source"], "replay")
            self.assertEqual(full["result"], recorded["result"])
            self.assertEqual(full["journal"]["sha256"], expected_hash)
            self.assertNotIn("items", full)


if __name__ == "__main__":
    unittest.main()
