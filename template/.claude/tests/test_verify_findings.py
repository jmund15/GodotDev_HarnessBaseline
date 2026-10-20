"""Quote verification covers complete findings containers without silent zeroes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / ".claude/tools/verify_claims.py"


class FindingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.md"
        self.source.write_text("Intro\nAlpha <strong>middle</strong> then after.\nOutro\n",
                               encoding="utf-8")
        self.findings = [
            {"id": "F1", "agent": "review", "action": "FIX", "category": "bug",
             "description": "quote present", "rationale": "source", "evidence": [
                 {"file": str(self.source) + ":2-2", "quote": "Alpha middle...after."}]},
            {"id": "F2", "agent": "review", "action": "FIX", "category": "bug",
             "description": "missing file", "rationale": "source", "evidence": [
                 {"file": str(self.root / "missing.md") + ":3", "quote": "missing"}]},
            {"id": "F3", "agent": "review", "action": "PLAN", "category": "rule",
             "description": "command output", "rationale": "source", "evidence": [
                 {"file": "opencode run --help", "quote": "--format json"}]},
        ]

    def put(self, name, content):
        path = self.root / name
        path.write_text(json.dumps(content) + "\n", encoding="utf-8")
        return path

    def verify(self, source, *, cwd=ROOT):
        out = self.root / "verdicts.json"
        args = [sys.executable, str(TOOL), "--findings", str(source), "--out", str(out)]
        proc = subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=30)
        self.assertNotIn("Traceback", proc.stderr)
        return proc, json.loads(out.read_text(encoding="utf-8")) if out.exists() else None

    def assert_verdicts(self, payload):
        rows = payload["verdicts"]
        self.assertEqual([row["id"] for row in rows], ["F1", "F2", "F3"])
        self.assertEqual([row["verdict"] for row in rows],
                         ["VERIFIED", "UNVERIFIED", "UNCHECKED"])
        self.assertEqual(payload["counts"]["findings"], 3)
        self.assertEqual(payload["counts"]["evidence"], 3)
        self.assertEqual(len(rows[0]["claims"]), 1)
        self.assertEqual(rows[0]["claims"][0]["citedRange"], [2, 2])

    def test_object_and_list_containers_reconcile(self):
        for name, data in (("object.json", {"findings": self.findings}),
                           ("list.json", self.findings)):
            with self.subTest(name=name):
                proc, payload = self.verify(self.put(name, data))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assert_verdicts(payload)

    def test_sidecar_envelope_and_json_result_reconcile(self):
        for name, body in (("structured.json.out", {"structured_output": {"findings": self.findings},
                                                       "is_error": False}),
                           ("json-result.json.out", {"result": json.dumps({"findings": self.findings}),
                                                      "structured_output": None, "is_error": False})):
            with self.subTest(name=name):
                proc, payload = self.verify(self.put(name, body))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assert_verdicts(payload)

    def test_workflow_spill_and_review_source_list_reconcile(self):
        for name, body in (("spill.json", {"findings": self.findings}),
                           ("review.json", {"sources": [
                               {"id": row["id"], "finding": row} for row in self.findings],
                               "view": self.findings, "coverage": {"completed": 1}})):
            with self.subTest(name=name):
                proc, payload = self.verify(self.put(name, body))
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assert_verdicts(payload)

    def test_workflow_journal_reads_full_results_not_preview(self):
        run = self.root / "workflow"
        run.mkdir()
        data = [{"type": "started", "key": "one", "agentId": "aone",
                 "label": "review:one", "phase": "Review"},
                {"type": "result", "key": "one", "agentId": "aone",
                 "result": {"findings": self.findings}}]
        (run / "journal.jsonl").write_text("\n".join(json.dumps(row) for row in data) + "\n",
                                          encoding="utf-8")
        proc, payload = self.verify(run)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_verdicts(payload)

    def test_directory_with_malformed_member_is_partial_not_clean(self):
        directory = self.root / "inputs"
        directory.mkdir()
        (directory / "a.json").write_text(json.dumps({"findings": [self.findings[0]]}), encoding="utf-8")
        (directory / "b.json").write_text("not-json", encoding="utf-8")
        proc, payload = self.verify(directory)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual([row["id"] for row in payload["verdicts"]], ["F1"])
        self.assertTrue(payload["errors"])

    def test_missing_empty_and_invalid_inputs_fail_closed(self):
        for name, content in (("missing.json", None), ("empty.json", []),
                              ("invalid.json", {"not_findings": []})):
            with self.subTest(name=name):
                path = self.root / name if content is None else self.put(name, content)
                proc, payload = self.verify(path)
                self.assertNotEqual(proc.returncode, 0)
                self.assertFalse(payload and payload.get("status") == "complete")

    def test_invalid_checked_metadata_fails_without_traceback(self):
        proc, payload = self.verify(self.put("bad-checked.json", {
            "checked": None, "findings": self.findings}))
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(payload and payload.get("status") == "complete")

    def test_missing_extensionless_local_path_is_unverified(self):
        finding = dict(self.findings[0], id="F4", evidence=[
            {"file": "CHANGELOG", "quote": "release note"}])
        proc, payload = self.verify(self.put("extensionless.json", [finding]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["verdicts"][0]["verdict"], "UNVERIFIED")

    def test_findings_without_evidence_are_complete_and_counted(self):
        asks = [dict(self.findings[0], id="F1", action="ASK", evidence=[]),
                {k: v for k, v in dict(self.findings[1], id="F2").items() if k != "evidence"}]
        proc, payload = self.verify(self.put("asks.json", {"findings": asks}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["counts"]["omittedNoEvidence"], 2)
        self.assertEqual(payload["counts"]["inputFindings"], 2)
        self.assertEqual(payload["verdicts"], [])
        proc, payload = self.verify(self.put("empty-object.json", {"findings": []}))
        self.assertEqual(proc.returncode, 2)
        self.assertFalse(payload and payload.get("status") == "complete")

    def test_directory_keeps_ids_per_source_and_skips_records_and_schemas(self):
        directory = self.root / "seats"
        directory.mkdir()
        raw = [{k: v for k, v in row.items() if k != "id"} for row in self.findings[:1]]
        for name in ("one.out.json", "two.out.json"):
            (directory / name).write_text(json.dumps({"structured_output": {"findings": raw},
                                                      "is_error": False}), encoding="utf-8")
        (directory / "one.record.json").write_text(json.dumps({"exitCode": 0}), encoding="utf-8")
        (directory / "one.record.json.out").write_text("stream text", encoding="utf-8")
        (directory / "review_schema.json").write_text(json.dumps({"type": "object"}),
                                                      encoding="utf-8")
        proc, payload = self.verify(directory)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual([(row["source"], row["id"]) for row in payload["verdicts"]],
                         [("one.out.json", "F1"), ("two.out.json", "F1")])
        self.assertEqual(payload["counts"]["findings"], 2)
        (directory / "three.out.json").write_text(json.dumps(
            {"findings": [dict(self.findings[0]), dict(self.findings[0])]}), encoding="utf-8")
        proc, payload = self.verify(directory)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("duplicate finding IDs in three.out.json: F1", " ".join(payload["errors"]))

    def test_non_path_citations_are_unchecked(self):
        rows = [dict(self.findings[0], id=f"F{index}", evidence=[{"file": cite, "quote": "text"}])
                for index, cite in enumerate(("context7:/godotengine/godot-docs", "WebSearch",
                                              "WebSearch: godot 4.6 tilemap", "memory",
                                              "memory: feedback_x"), 1)]
        proc, payload = self.verify(self.put("non-path.json", rows))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual({row["verdict"] for row in payload["verdicts"]}, {"UNCHECKED"})
        self.assertEqual(payload["counts"]["unchecked"], 5)
        self.assertNotIn("notApplicable", payload["counts"])

    def test_every_item_kind_is_read_and_a_claim_with_an_unchecked_item_is_not_verified(self):
        token = "zq" + "xjv" + "absent"
        findings = [
            dict(self.findings[0], id="K1", evidence=[
                {"command": "git ls-files -- .claude/tools/verify_claims.py",
                 "output": ".claude/tools/verify_claims.py"}]),
            dict(self.findings[0], id="K2", evidence=[
                {"command": f"git grep -n -F {token} -- .claude/tools", "output": ""}]),
            dict(self.findings[0], id="K3", evidence=[
                {"file": str(self.source), "quote": "Alpha middle...after."},
                {"url": "https://example.test/x", "quote": "external"}]),
            dict(self.findings[0], id="K4", evidence=[
                {"url": "https://example.test/x", "quote": "Alpha middle", "artifact": str(self.source)}]),
            dict(self.findings[0], id="K5", evidence=[
                {"command": "git ls-files -- .claude/tools/verify_claims.py", "output": "wrong"}]),
        ]
        proc, payload = self.verify(self.put("kinds.json", findings))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual({row["id"]: row["verdict"] for row in payload["verdicts"]},
                         {"K1": "VERIFIED", "K2": "VERIFIED", "K3": "UNCHECKED", "K4": "VERIFIED",
                          "K5": "UNVERIFIED"})
        self.assertEqual([c["kind"] for c in payload["verdicts"][2]["claims"]], ["file", "url"])
        self.assertEqual(payload["verdicts"][2]["claims"][1]["reason"], "external")

    def test_evidence_item_of_no_known_kind_fails_closed(self):
        bad = dict(self.findings[0], evidence=[{"nothing": "here"}])
        proc, payload = self.verify(self.put("nokind.json", [bad]))
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(payload and payload.get("status") == "complete")

    def test_root_resolves_relative_citations_for_a_worktree_subject(self):
        row = dict(self.findings[0], evidence=[{"file": "source.md:2", "quote": "Alpha middle...after."}])
        out = self.root / "rooted.json"
        proc = subprocess.run([sys.executable, "-c",
                               "import sys; sys.path.insert(0, %r); import verify_claims as v; "
                               "sys.exit(v.verify_findings(%r, %r, {}, False, root=%r))" % (
                                   str(ROOT / ".claude/tools"), str(self.put("rooted-in.json", [row])), str(out),
                                   str(self.root))],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["verdicts"][0]["verdict"], "VERIFIED")


    def test_repo_relative_citation_resolves_from_any_cwd(self):
        row = dict(self.findings[0], evidence=[{"file": ".claude/CLAUDE.md:1",
                                                "quote": "@CLAUDE.core.md"}])
        proc, payload = self.verify(self.put("relative.json", [row]), cwd=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(payload["verdicts"][0]["verdict"], "VERIFIED")


if __name__ == "__main__":
    unittest.main()
