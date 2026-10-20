"""J1's executable claim tracer; isolated repositories, actual CLI and commit events.

    python3 .claude/tests/test_conformance_j1.py
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

CLAUDE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLAUDE / "tools"))
sys.path.insert(0, str(CLAUDE / "tests"))
import extract_numbers as en
from test_extract_numbers import git, write, mkrepo, _rm

DOC = ".claude/rules/claim.md"
IMPL = ".claude/tools/emitter.py"
PROOF = ".claude/tests/test_emitter.py"
SENTENCE = "The emitter prints accepted."
SCOPE = {"paths": [".claude/rules/*.md"],
         "behavior_paths": [".claude/rules/*.md", ".claude/tools/*.py"],
         "rows_dir": ".claude/instruction_rows"}


class ClaimTracer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="conformance_j1_")
        self.repo = Path(mkrepo(self.tmp, "repo", {
            DOC: "# Claim\n\n" + SENTENCE + "\n",
            IMPL: 'def emit():\n    return "accepted"\n',
            PROOF: ('import sys, pathlib, unittest\n'
                    'sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))\n'
                    'from emitter import emit\n'
                    'class Emit(unittest.TestCase):\n'
                    '    def test_accepted(self):\n'
                    '        self.assertEqual(emit(), "accepted")\n'
                    'unittest.main(verbosity=2)\n'),
        }, scope=SCOPE))

    def tearDown(self):
        _rm(self.tmp)

    def row(self, klass="behavior", sentence=SENTENCE, **changes):
        row = {"path": DOC, "sentence": en.norm(sentence), "hash": en.sentence_hash(en.norm(sentence)),
               "section": "Claim", "class": klass, "source": "author:J1",
               "classificationQuote": "The emitter prints accepted: an emitted-output assertion."}
        if klass == "behavior":
            row.update(claimId="emitter.accepted", implementation=[
                {"path": IMPL, "symbol": "emit", "quote": 'return "accepted"'}], proofs=[
                {"path": PROOF, "caseId": "Emit.test_accepted", "selector": {"type": "unittest-case"},
                 "assertion": 'self.assertEqual(emit(), "accepted")'}])
        row.update(changes)
        return row

    def rows(self, *rows):
        write(str(self.repo), ".claude/instruction_rows/behaviors.jsonl",
              "".join(json.dumps(r) + "\n" for r in rows))

    def report(self, **kw):
        return en.behavior_report(self.repo, paths=[DOC], **kw)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(CLAUDE / "tools/extract_numbers.py"),
                               "--repo", str(self.repo), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def receipt(self, status="pass", **kw):
        binding = en.behavior_proofs(self.repo, [DOC])
        case = binding["cases"][PROOF][0]
        receipt = {"proof": PROOF, "caseId": "Emit.test_accepted", "status": status,
                   "inputKey": case["inputKey"], "evidence": "actual unittest result"}
        receipt.update(kw)
        return {"observations": [receipt]}

    def hook(self, command="git commit -m fixture", extra=None):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.repo), PYTHONIOENCODING="utf-8")
        env.pop("GIT_INDEX_FILE", None)
        env.update(extra or {})
        return subprocess.run([sys.executable, str(CLAUDE / "hooks/duplicate_commit_check.py")],
                              input=json.dumps({"tool_name": "Bash", "cwd": str(self.repo),
                                                "tool_input": {"command": command}}),
                              capture_output=True, text=True, encoding="utf-8", cwd=self.repo, env=env)

    def test_census_and_numeric_compatibility(self):
        text = ('---\nname: review-seat\n'
                'description: Workflow engine delegate for the review tool profile; engines select it, '
                'never pick it for a manual Agent call.\n---\n'
                '# Claim\n\nIt never silently accepts them.\n\n'
                '```powershell\n'
                '# defaults to the newest godot_test.log under TestResults/ and TestResults/runs/** (cwd), '
                'else the appdata live log\n```\n'
                '| Policy | Behavior |\n|---|---|\n| hook | rejects an empty input |\n')
        units = en.behavior_units(DOC, text)
        for quote in ("description:", "# Claim", "It never", "defaults to", "rejects an empty"):
            self.assertTrue(any(quote in u.raw for u in units), quote)
        self.assertFalse(any("description:" in u.raw for u in en.units(DOC, text)))
        self.assertFalse(any("defaults to" in u.raw for u in en.units(DOC, text)))
        self.assertEqual(en.behavior_units(DOC, "It never silently accepts them.\n")[0].hash,
                         en.units(DOC, "It never silently accepts them.\n")[0].hash)
        write(str(self.repo), DOC, text)
        p = self.cli("check", "--claims", "behavior", "--paths", DOC, "--json")
        self.assertEqual(p.returncode, 1, p.stderr)
        result = json.loads(p.stdout)
        self.assertEqual(result["counts"]["units"], len(result["units"]))
        self.assertEqual(result["counts"]["uncovered"], len(result["units"]))
        numeric = self.cli("check", "--paths", DOC, "--json")
        self.assertEqual(numeric.returncode, 0, numeric.stderr)
        self.assertEqual(json.loads(numeric.stdout)["counts"]["flagged_tokens"], 0)

    def test_source_extraction_is_conservative_and_honest(self):
        source = ('"""The checker rejects empty input.\nAnd emits a diagnostic."""\n'
                  'limit = "data"  # example, not automatic enforcement\n'
                  'message = f"Unavailable: {limit!r}"\n'
                  'result = 42\n')
        units = en.behavior_units(IMPL, source)
        for quote in ("checker rejects", "example, not", "Unavailable:"):
            self.assertTrue(any(quote in u.raw for u in units), quote)
        self.assertFalse(any("result = 42" in u.raw for u in units))
        self.assertEqual(len({(u.line_start, u.line_end) for u in units}), len(units))
        with self.assertRaises(ValueError):
            en.behavior_units(IMPL, '"unterminated')
        with self.assertRaises(ValueError):
            en.behavior_units("unknown.bin", "not empty")

    def test_unclassified_and_unbound_are_distinct(self):
        result = self.report()
        self.assertEqual(result["status"], "uncovered")
        self.assertTrue(any(r["reason"] == "unclassified unit" for r in result["uncovered"]))
        self.rows(self.row(proofs=[]))
        result = self.report()
        self.assertEqual(result["claims"][0]["status"], "unbound")
        self.assertEqual(result["counts"]["claims"], 1)

    def test_procedure_and_nonclaim_do_not_invent_proofs(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        for klass in ("procedure", "nonclaim"):
            self.rows(self.row(klass=klass, section="__module__"))
            result = self.report()
            self.assertEqual(result["status"], "pass", result)
            self.assertEqual(result["claims"], [])
            self.assertEqual(en.behavior_proofs(self.repo, [DOC])["proofs"], [])

    def test_bound_case_mutation_restore_and_receipt_freshness(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        self.rows(self.row(section="__module__"))
        self.assertEqual(self.report()["claims"][0]["status"], "uncovered")
        p = subprocess.run([sys.executable, "-B", str(self.repo / PROOF)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        receipts = self.receipt(evidence=p.stderr)
        self.assertEqual(self.report(proof_results=receipts)["status"], "pass")
        write(str(self.repo), IMPL, 'def emit():\n    return "rejected"\n')
        self.assertEqual(self.report(proof_results=receipts)["claims"][0]["status"], "stale")
        # Disable bytecode so equal-length rapid fixture rewrites really exercise current bytes.
        p = subprocess.run([sys.executable, "-B", str(self.repo / PROOF)], capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertNotEqual(p.returncode, 0)
        failed = self.receipt(status="fail", evidence=p.stderr)
        self.assertEqual(self.report(proof_results=failed)["claims"][0]["status"], "fail")
        write(str(self.repo), IMPL, 'def emit():\n    return "accepted"\n')
        self.assertEqual(self.report(proof_results=self.receipt())["status"], "pass")
        for path in (PROOF, ".claude/skills/project_subsystems/adaptation.json"):
            before = (self.repo / path).read_text(encoding="utf-8")
            write(str(self.repo), path, before + "\n")
            self.assertEqual(self.report(proof_results=receipts)["claims"][0]["status"], "stale")
            write(str(self.repo), path, before)

    def test_current_doc_bytes_revoke_receipt_despite_same_normalized_hash(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        self.rows(self.row(section="__module__"))
        receipt = self.receipt()
        self.assertEqual(self.report(proof_results=receipt)["claims"][0]["status"], "pass")
        write(str(self.repo), DOC, "The emitter prints ACCEPTED.\n")
        self.assertEqual(en.behavior_units(DOC, "The emitter prints ACCEPTED.\n")[0].hash,
                         self.row()["hash"])
        self.assertEqual(self.report(proof_results=receipt)["claims"][0]["status"], "stale")

    def test_missing_skipped_and_failed_observations_cannot_pass(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        self.rows(self.row(section="__module__"))
        self.assertEqual(self.report(proof_results={"status": "pass"})["claims"][0]["status"], "uncovered")
        for status in ("skip", "timeout", "unavailable"):
            self.assertEqual(self.report(proof_results=self.receipt(status))["claims"][0]["status"], "uncovered")
        receipts = self.receipt("fail")
        receipts["observations"].append(self.receipt()["observations"][0])
        self.assertEqual(self.report(proof_results=receipts)["claims"][0]["status"], "fail")

    def test_wording_orphans_and_malformed_rows_are_not_clean(self):
        self.rows(self.row())
        write(str(self.repo), DOC, "# Claim\n\nThe emitter prints silently.\n")
        result = self.report()
        self.assertEqual(result["claims"][0]["status"], "stale")
        self.assertTrue(any(r["reason"] == "unclassified unit" for r in result["uncovered"]))
        write(str(self.repo), DOC, "# Claim\n")
        self.assertEqual(self.report()["claims"][0]["status"], "stale")
        self.rows(self.row(), self.row())
        self.assertEqual(self.report()["status"], "fail")
        self.rows(self.row(hash="0" * 16))
        self.assertEqual(self.report()["status"], "fail")

    def test_case_and_proof_removal_and_structural_only_binding(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        self.rows(self.row(section="__module__"))
        receipts = self.receipt()
        write(str(self.repo), PROOF, "# case removed\n")
        result = self.report(proof_results=receipts)
        self.assertIn(result["claims"][0]["status"], ("uncovered", "stale"))
        (self.repo / PROOF).unlink()
        self.assertEqual(self.report()["claims"][0]["status"], "uncovered")
        self.rows(self.row(proofs=[{"path": "echo pass", "caseId": "x", "selector": {"type": "regex"},
                                   "assertion": "pin"}]))
        self.assertEqual(self.report()["status"], "fail")

    def test_deduplication_dependencies_and_binding_subset(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        self.rows(self.row(section="__module__"))
        first = en.behavior_proofs(self.repo, [IMPL])
        self.assertEqual(first["proofs"], [PROOF])
        key = first["cases"][PROOF][0]["inputKey"]
        otherdoc = ".claude/rules/other.md"
        write(str(self.repo), otherdoc, "The emitter accepts input.\n")
        another = self.row(path=otherdoc, sentence="The emitter accepts input.",
                           hash=en.sentence_hash(en.norm("The emitter accepts input.")), claimId="emitter.input")
        self.rows(self.row(section="__module__"), another)
        selected = en.behavior_proofs(self.repo, [IMPL])
        self.assertEqual(selected["proofs"], [PROOF])
        self.assertEqual(len(selected["cases"][PROOF]), 1)
        unrelated = self.row("nonclaim", path=".claude/rules/unrelated.md", sentence="Example data.",
                             hash=en.sentence_hash(en.norm("Example data.")))
        self.rows(self.row(section="__module__"), another, unrelated)
        self.assertEqual(selected["cases"][PROOF][0]["inputKey"],
                         en.behavior_proofs(self.repo, [IMPL])["cases"][PROOF][0]["inputKey"])
        self.assertNotEqual(key, selected["cases"][PROOF][0]["inputKey"])

    def test_scope_errors_unreadable_and_legacy_debt_partition(self):
        result = self.report()
        self.assertEqual(result["scope"]["affected"], [])
        self.assertTrue(result["scope"]["initialDebt"])
        write(str(self.repo), DOC, "It now emits an error.\n")
        result = self.report()
        self.assertTrue(result["scope"]["changedUnits"])
        (self.repo / DOC).write_bytes(b"\xff")
        self.assertEqual(self.report()["status"], "uncovered")
        write(str(self.repo), ".claude/skills/project_subsystems/adaptation.json", "{}")
        self.assertEqual(self.report()["status"], "uncovered")
        self.assertEqual(self.cli("check", "--claims", "behavior", "--json").returncode, 2)

    def test_cli_add_list_stub_and_staged_bytes(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        p = self.cli("stub", "--claims", "behavior", "--paths", DOC)
        self.assertEqual(p.returncode, 0, p.stderr)
        stub = json.loads(p.stdout.strip())
        self.assertEqual(stub["class"], None)
        self.assertEqual(stub["hash"], self.row()["hash"])
        candidate = Path(self.tmp) / "candidate.jsonl"
        candidate.write_text(json.dumps(self.row(section="__module__")) + "\n", encoding="utf-8")
        p = self.cli("add", "--claims", "behavior", str(candidate))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.cli("add", "--claims", "behavior", str(candidate)).returncode, 1)
        p = self.cli("list", "--claims", "behavior", DOC, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["units"][0]["class"], "behavior")
        git(str(self.repo), "add", "-A")
        write(str(self.repo), DOC, "Changed only in the worktree.\n")
        self.assertEqual(self.report(staged=True)["claims"][0]["status"], "uncovered")
        self.assertEqual(self.report()["claims"][0]["status"], "stale")

    def test_reached_inputs_config_and_row_only_changes_select_claims(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        self.rows(self.row(section="__module__"))
        write(str(self.repo), ".claude/tools/helper.py", 'VALUE = "accepted"\n')
        write(str(self.repo), IMPL, 'from helper import VALUE\ndef emit():\n    return VALUE\n')
        self.rows(self.row(section="__module__", implementation=[
            {"path": IMPL, "symbol": "emit", "quote": "return VALUE"}]))
        git(str(self.repo), "add", "-A")
        git(str(self.repo), "commit", "-q", "-m", "bind")
        receipt = self.receipt()
        write(str(self.repo), ".claude/tools/helper.py", 'VALUE = "wrong"\n')
        report = en.behavior_report(self.repo, paths=[".claude/tools/helper.py"], proof_results=receipt)
        self.assertEqual(report["scope"]["affected"], ["emitter.accepted"])
        self.assertEqual(report["claims"][0]["status"], "stale")
        self.assertEqual(en.behavior_proofs(self.repo, [".claude/tools/helper.py"])["proofs"], [PROOF])
        write(str(self.repo), ".claude/tools/helper.py", 'VALUE = "accepted"\n')
        config = self.repo / ".claude/skills/project_subsystems/adaptation.json"
        config.write_bytes(config.read_bytes() + b"\n")
        report = en.behavior_report(self.repo, proof_results=receipt)
        self.assertEqual(report["scope"]["affected"], ["emitter.accepted"])
        self.assertEqual(report["claims"][0]["status"], "stale")
        self.rows(self.row(section="__module__", implementation=[
            {"path": IMPL, "symbol": "emit", "quote": "return VALUE"}], source="author:updated"))
        bindings = en.behavior_proofs(self.repo, [".claude/instruction_rows/behaviors.jsonl"])
        self.assertEqual(bindings["proofs"], [PROOF])
        report = en.behavior_report(self.repo, paths=[".claude/instruction_rows/behaviors.jsonl"],
                                    proof_results=receipt)
        self.assertEqual(report["scope"]["affected"], ["emitter.accepted"])

    def test_external_identity_and_unreadable_rows_are_uncovered(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        impl = {"path": IMPL, "symbol": "emit", "quote": 'return "accepted"',
                "external": {"version": "unavailable", "sourceIdentity": "unverified"}}
        self.rows(self.row(section="__module__", implementation=[impl]))
        self.assertNotEqual(self.report(proof_results=self.receipt())["status"], "pass")
        (self.repo / ".claude/instruction_rows/behaviors.jsonl").write_bytes(b"\xff")
        self.assertEqual(self.report()["status"], "uncovered")

    def test_missing_doc_and_unsupported_source_are_uncovered_not_orphan_only(self):
        self.rows(self.row())
        (self.repo / DOC).unlink()
        report = self.report()
        self.assertTrue(any("missing" in r["reason"] for r in report["uncovered"]))
        config = dict(SCOPE, behavior_paths=["*.bin"])
        write(str(self.repo), ".claude/skills/project_subsystems/adaptation.json",
              json.dumps({"instruction_scope": config}))
        write(str(self.repo), "unknown.bin", "unextractable")
        report = en.behavior_report(self.repo, ["unknown.bin"])
        self.assertEqual(report["status"], "uncovered")

    def test_existing_case_observation_store_not_file_level_cache(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        self.rows(self.row(section="__module__"))
        runner = en._proof_runner()
        previous = runner.STAMP_PATH
        runner.STAMP_PATH = str(self.repo / ".claude/logs/harness_tests_stamp.json")
        try:
            passes = Path(runner._beside_stamp(runner.PASSES_PATH, "harness_proof_passes.json"))
            passes.parent.mkdir(parents=True, exist_ok=True)
            passes.write_text(json.dumps({PROOF: {"key": "ordinary-file-pass", "ts": 1}}))
            self.assertEqual(self.report()["claims"][0]["status"], "uncovered")
            passes.write_text(json.dumps({PROOF: self.receipt()}))
            self.assertEqual(self.report()["status"], "pass")
            passes.write_text(json.dumps({PROOF: {"key": "ordinary-file-pass", "ts": 1}}))
            Path(runner.runs_path()).write_text(json.dumps({"runs": [self.receipt("fail")]}))
            self.assertEqual(self.report()["claims"][0]["status"], "fail")
        finally:
            runner.STAMP_PATH = previous

    def test_commit_controls_are_not_denied(self):
        from _must_allow_cases import must_allow_cases
        fixtures, cleanup = must_allow_cases("duplicate_commit_check")
        try:
            for label, payload, cwd, _allow in fixtures:
                env = dict(os.environ, CLAUDE_PROJECT_DIR=cwd, PYTHONIOENCODING="utf-8")
                env.pop("GIT_INDEX_FILE", None)
                p = subprocess.run([sys.executable, str(CLAUDE / "hooks/duplicate_commit_check.py")],
                                   input=json.dumps(payload), capture_output=True, text=True,
                                   encoding="utf-8", env=env, cwd=cwd)
                self.assertEqual(p.returncode, 0, (label, p.stderr))
                self.assertNotIn("permissionDecision", p.stdout, label)
        finally:
            cleanup()

    def test_metadata_only_census_does_not_load_proof_runner(self):
        from unittest.mock import patch
        with patch.object(en, "_proof_runner", side_effect=AssertionError("unnecessary proof inventory")):
            self.assertEqual(self.report()["status"], "uncovered")
            self.rows(self.row(proofs=[]))
            self.assertEqual(self.report()["claims"][0]["status"], "unbound")

    def test_verified_external_probe_identity_must_match(self):
        write(str(self.repo), DOC, SENTENCE + "\n")
        external = {"version": "fixture-1", "sourceIdentity": "fixture-source"}
        impl = {"path": IMPL, "symbol": "emit", "quote": 'return "accepted"', "external": external}
        self.rows(self.row(section="__module__", implementation=[impl]))
        receipt = self.receipt()
        receipt["observations"][0]["external"] = [{"path": IMPL, **external}]
        self.assertEqual(self.report(proof_results=receipt)["status"], "pass")
        receipt["observations"][0]["external"][0]["version"] = "different"
        self.assertEqual(self.report(proof_results=receipt)["claims"][0]["status"], "uncovered")

    def test_doc_advisory_fingerprints_only_selected_proof_subset(self):
        from unittest.mock import patch
        otherproof = ".claude/tests/test_other.py"
        otherdoc = ".claude/rules/other.md"
        write(str(self.repo), otherproof, (self.repo / PROOF).read_text())
        write(str(self.repo), otherdoc, "Other emitted behavior.\n")
        first = self.row(section="__module__")
        other = self.row(path=otherdoc, sentence="other emitted behavior.",
                         hash=en.sentence_hash("other emitted behavior."), section="__module__", claimId="other.output",
                         proofs=[dict(first["proofs"][0], path=otherproof)])
        write(str(self.repo), DOC, SENTENCE + "\n")
        self.rows(first, other)
        git(str(self.repo), "add", "-A")
        git(str(self.repo), "commit", "-q", "-m", "independent bindings")
        runner = en._proof_runner()
        actual = runner._PassCache.reach
        reached = []
        def observe(cache, proof):
            reached.append(Path(proof).name)
            return actual(cache, proof)
        with patch.object(runner._PassCache, "reach", observe):
            en.behavior_report(self.repo, [DOC], proof_results=[])
        self.assertEqual(reached, [Path(PROOF).name])

    def test_unavailable_receipt_file_preserves_typed_report_shape(self):
        p = self.cli("check", "--claims", "behavior", "--paths", DOC, "--proof-results",
                     str(Path(self.tmp) / "missing.json"), "--json")
        self.assertEqual(p.returncode, 2)
        result = json.loads(p.stdout)
        self.assertTrue(all(k in result for k in ("status", "counts", "units", "claims", "uncovered", "scope")))
        self.assertEqual(result["status"], "uncovered")

    def test_number_guard_allows_uncovered_scan_and_does_not_deny_behavior(self):
        write(str(self.repo), DOC, "An unclassified implicit behavior claim.\n")
        git(str(self.repo), "add", DOC)
        payload = json.dumps({"tool_name": "Bash", "cwd": str(self.repo),
                              "tool_input": {"command": "git commit -m fixture"}})
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.repo))
        env.pop("GIT_INDEX_FILE", None)
        guard = str(CLAUDE / "hooks/rule_number_guard.py")
        p = subprocess.run([sys.executable, guard], input=payload, capture_output=True, text=True,
                           cwd=self.repo, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        write(str(self.repo), ".claude/skills/project_subsystems/adaptation.json", "{}")
        git(str(self.repo), "add", "-A")
        p = subprocess.run([sys.executable, guard], input=payload, capture_output=True, text=True,
                           cwd=self.repo, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_unittest_assertion_must_belong_to_named_case(self):
        self.rows(self.row())
        write(str(self.repo), PROOF, ('import unittest\nfrom emitter import emit\n'
                                     'class Emit(unittest.TestCase):\n'
                                     '    def test_accepted(self):\n'
                                     '        self.assertTrue(True)\n'
                                     '    def test_other(self):\n'
                                     '        self.assertEqual(emit(), "accepted")\n'))
        bindings = en.behavior_proofs(self.repo, [DOC])
        self.assertEqual(bindings["proofs"], [])
        self.assertTrue(bindings["unresolved"])

    def test_advisory_missing_scope_and_mentions_are_silent(self):
        write(str(self.repo), DOC, "A new assertion.\n")
        git(str(self.repo), "add", DOC)
        write(str(self.repo), ".claude/skills/project_subsystems/adaptation.json", "{}")
        git(str(self.repo), "add", "-A")
        p = self.hook()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout), {})
        self.assertEqual(json.loads(self.hook("git log --grep commit").stdout), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
