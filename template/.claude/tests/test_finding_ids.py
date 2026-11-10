"""Every numbering site gives a returned review entry one id, rejected or not.

The vectors are planted here (cut from tests/fixtures/finding_id_vectors.json, which never ships) and
are also run through review_fanout.js by engine_finding_ids_test.js: the engine, review_result.ingest
and the journal archive post_check reads must agree, so a fold's ids are the ids the post-check checks."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/tools"))
sys.path.insert(0, str(ROOT / ".claude/tests"))
import review_result  # noqa: E402
import test_session_digest_workflow_results as journal  # noqa: E402

VECTORS = {
    "about": "One review run, two seats, five returned entries. Entry 3 of seat one is malformed and "
             "keeps its id F3 (rejected); the command-evidence entry is valid by the evidence schema. "
             "Every numbering site must give each entry the id `ids` names: review_result.py ingest, "
             "session_digest.py build_workflow_result_archive and workflows/review_fanout.js. null "
             "marks a rejected entry.",
    "seats": [
        {"key": "one", "findings": [
            {"agent": "lens", "action": "FIX", "category": "bug", "critical": False, "file": "a.md:10",
             "description": "first", "old": "old first", "new": "new first", "rationale": "why first"},
            {"agent": "lens", "action": "FIX", "category": "bug", "critical": False, "file": "a.md:20",
             "description": "command evidence", "old": "old command evidence", "new": "new command evidence",
             "rationale": "why command evidence",
             "evidence": [{"command": "git grep -n x -- a.md", "output": ""}]},
            {"agent": "lens", "action": "FIX", "category": "bug", "critical": False, "file": "b.md:3",
             "description": "no rationale", "old": "x", "new": "y"},
            {"agent": "lens", "action": "FIX", "category": "bug", "critical": False, "file": "b.md:7",
             "description": "fourth", "old": "old fourth", "new": "new fourth", "rationale": "why fourth"},
        ]},
        {"key": "two", "findings": [
            {"agent": "lens", "action": "FIX", "category": "bug", "critical": False, "file": "b.md:9",
             "description": "fifth", "old": "old fifth", "new": "new fifth", "rationale": "why fifth"},
        ]},
    ],
    "ids": [["F1", "F2", None, "F4"], ["F5"]],
    "rejected": [{"id": "F3", "seat": "one", "index": 2, "reasonContains": "rationale"}],
    "foldSubjects": [{"files": ["a.md"], "ids": ["F1", "F2"]}, {"files": ["b.md"], "ids": ["F4", "F5"]}],
}
SPEC = importlib.util.spec_from_file_location("session_digest", ROOT / ".claude/tools/session_digest.py")
digest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(digest)
EXPECTED = [i for seat in VECTORS["ids"] for i in seat if i]


def checked():
    return {"toolsUsed": ["Read:fixture"], "stoppedAt": "exhausted-leads", "basis": "fixture input was read"}


class FindingIdTests(unittest.TestCase):
    def test_ingest_numbers_every_entry_and_names_the_rejected_one(self):
        seats = [{"label": s["key"], "mandates": ["lens"], "mustRead": None,
                  "record": {"exitCode": 0, "schemaValid": True, "engagement": "proven"},
                  "payload": {"structured_output": {"checked": checked(), "gaps": [], "findings": s["findings"]},
                              "is_error": False}} for s in VECTORS["seats"]]
        result = review_result.ingest(seats)
        self.assertEqual([s["id"] for s in result["sources"]], EXPECTED)
        issues = " | ".join(i for row in result["coverage"] for i in row.get("issues", []))
        for rejected in VECTORS["rejected"]:
            self.assertIn(rejected["id"] + " ", issues)
            self.assertIn(rejected["reasonContains"], issues)

    def test_journal_archive_numbers_every_entry_and_names_the_rejected_one(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "wf_ids"
            rows = []
            for s in VECTORS["seats"]:
                rows += [journal.started(s["key"], "review:" + s["key"]),
                         journal.result(s["key"], {"findings": s["findings"]})]
            journal.write_journal(run_dir, rows)
            archive = digest.build_workflow_result_archive(
                run_dir, "review", seat_order=[s["key"] for s in VECTORS["seats"]])
        self.assertEqual([row["id"] for row in archive["items"]], EXPECTED)
        issues = " | ".join(i for lens in archive["lenses"] for i in lens.get("issues", []))
        for rejected in VECTORS["rejected"]:
            self.assertIn(rejected["id"] + " ", issues)
            self.assertIn(rejected["reasonContains"], issues)


if __name__ == "__main__":
    unittest.main()
