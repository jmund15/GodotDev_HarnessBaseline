#!/usr/bin/env python3
"""The push floor's rule must be reachable before deny; premises have one contract owner."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DoctrineTextTests(unittest.TestCase):
    def test_standing_clause_names_conditional_premises_and_push_reminder(self):
        text = (ROOT / "CLAUDE.core.md").read_text(encoding="utf-8")
        git = text.split("### 5–6. Git", 1)[1].split("### 9.", 1)[0]
        self.assertIn("A decision doctrine commit carries a `premises` block when it rests on a claim "
                      "(`reference/evidence_contract.md`). A push report names each decision commit no review covers.", git)
        self.assertNotIn("or the push is refused", git)

    def test_commit_push_step_and_exception_list_admit_audit(self):
        text = (ROOT / "commands/commit_push.md").read_text(encoding="utf-8")
        step = text.split("\n6. ", 1)[1].split("\n7. ", 1)[0]
        self.assertIn("/session_audit --lens sa-harness-quality", step)
        self.assertIn("already admitted", step)
        self.assertNotIn("triage.py admits harness-quality-seat --rev", step)
        self.assertNotIn("execution_depth.md", step)
        self.assertIn("review-owed line", step)
        exception = text.split("\n8. ", 1)[1]
        self.assertIn("admitted doctrine review", exception)

    def test_contract_defines_scope_and_parseable_premises_examples(self):
        text = (ROOT / "reference/evidence_contract.md").read_text(encoding="utf-8")
        self.assertIn("## Load-bearing premises", text)
        self.assertIn("is_harness_file", text)
        self.assertIn("task_record.py", text)
        self.assertIn("auto-memory", text)
        self.assertIn("plans", text)
        self.assertIn("A commit-time check verifies a present block through `verify_claims.py`", text)
        self.assertIn("lists each UNVERIFIED item", text)
        self.assertIn("An absent block asks whether the decision rests on a claim; it is not judged at push.", text)
        self.assertNotIn("no commit-time", text)
        self.assertIn("one JSON object per line", text)
        examples = re.findall(r"```premises\n(.*?)\n```", text, re.S)
        self.assertTrue(examples)
        for block in examples:
            for line in block.splitlines():
                claim = json.loads(line)
                self.assertIsInstance(claim["claim"], str)
                self.assertIsInstance(claim["evidence"], list)
        self.assertTrue(any("task" in item for block in examples for line in block.splitlines()
                            for item in json.loads(line)["evidence"]))


if __name__ == "__main__":
    unittest.main()
