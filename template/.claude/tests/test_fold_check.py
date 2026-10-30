"""The shared post-fold checker fails a fold whose self-report disagrees with disk or the checker."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude/tools"))
SPEC = importlib.util.spec_from_file_location("fold_check", ROOT / ".claude/tools/fold_check.py")
fold_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fold_check)

PREFOLD = "# Plan\n\nStep 3: teh default is local.\nStep 9: pick a transport.\n"
FOLDED = "# Plan\n\nStep 3: the default is local.\nStep 9: pick a transport.\n"
UNIT_PLAN = ("# Plan\n\n## Design\n\n- **D-a: rule one.** Uses a scope hash.\n- **D-b: units.** Groups files.\n\n"
             "## Elements\n\n- **E1:** applies D-a to the ledger.\n- **E2:** prints units.\n- **E3:** cites D-a and D-b.\n")


def init_repo(root):
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q", str(root)], check=True)


class Fixture:
    """One plan in a git checkout, its blob snapshot, a cited source and a fold digest over three findings."""

    def __init__(self, root, name="plan"):
        init_repo(root)
        self.root = root
        self.rel = f"{name}.md"
        self.plan = root / self.rel
        self.plan.parent.mkdir(parents=True, exist_ok=True)
        self.verdicts = root / f"{name.replace('/', '_')}_fold_verdicts.json"
        self.digest_path = root / f"{name.replace('/', '_')}_digest.json"
        self.manifest = root / f"{name.replace('/', '_')}_snapshot.json"
        self.snap(PREFOLD)
        self.source = root / "src.txt"
        self.source.write_text("def x():\n    return None\n", encoding="utf-8")

    def snap(self, pre, post=FOLDED):
        """Snapshot `pre` as the pre-fold bytes, then leave `post` on disk."""
        self.plan.write_text(pre, encoding="utf-8")
        self.manifest.write_text(json.dumps(fold_check.snapshot_blobs(self.root, [self.rel])), encoding="utf-8")
        self.plan.write_text(post, encoding="utf-8")

    def subject(self):
        return fold_check.load_subject(self.root, self.manifest, [self.rel])

    def rows(self, prefix="F"):
        return [
            {"id": f"{prefix}1", "action": "FIX", "file": f"{self.plan}:3", "description": "typo",
             "old": "teh default", "new": "the default",
             "evidence": [{"file": f"{self.plan}:3", "quote": "teh default is local"}]},
            {"id": f"{prefix}2", "action": "ASK", "file": f"{self.plan}:4", "description": "which?",
             "evidence": [{"file": str(self.source), "quote": "return None"}]},
            {"id": f"{prefix}3", "action": "PLAN", "file": f"{self.plan}:4", "description": "spike"},
        ]

    def digest(self, prefix="F", **over):
        a, b, c = (f"{prefix}{n}" for n in (1, 2, 3))
        value = {
            "applied": [{"id": a, "change": "typo fixed", "source": "finding"}],
            "deferred": [{"id": b, "class": "taste-fork", "reason": "taste fork"},
                         {"id": c, "class": "spike", "reason": "spike"}],
            "changedPaths": [self.rel],
        }
        value.update(over)
        return value

    def write(self, digest):
        self.digest_path.write_text(json.dumps(digest), encoding="utf-8")
        return ["--subject", f"{self.root}::{self.manifest}::{self.rel}", "--digest", str(self.digest_path)]


class FoldCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fx = Fixture(self.root)
        self.out = self.root / "fold_check.json"

    def findings(self, rows, name="findings.json", container="findings"):
        path = self.root / name
        path.write_text(json.dumps({container: rows} if container else rows), encoding="utf-8")
        return str(path)

    def check(self, findings, *jobs):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = fold_check.main(["--findings", findings, *[a for job in jobs for a in job],
                                    "--out", str(self.out)])
        report = json.loads(self.out.read_text(encoding="utf-8")) if self.out.exists() else None
        if report and "failures" in report:
            for failure in report["failures"]:
                self.assertIsInstance(failure["ids"], list)
                self.assertIsInstance(failure["files"], list)
                self.assertIn(failure["kind"], fold_check.FAILURE_SCOPE)
        return code, report, err.getvalue()

    def kinds(self, report):
        return sorted({failure["kind"] for failure in report["failures"]})

    def test_failure_scope_catalog_and_emitted_scope(self):
        import re
        kinds = set(re.findall(r'fail\("([\w-]+)"', Path(fold_check.__file__).read_text(encoding="utf-8")))
        self.assertTrue(kinds <= set(fold_check.FAILURE_SCOPE))
        expected = {
            "fold-not-applied": [self.fx.rel], "fold-applied-unverified": [self.fx.rel],
            "fold-old-missing": [self.fx.rel], "fold-test-order": [self.fx.rel],
            "fold-untested-code": [self.fx.rel], "fold-unchanged-file": [],
            "fold-out-of-subject": [], "fold-id-coverage": [self.fx.rel], "fold-routing": [],
            "fold-invalid-shape": [self.fx.rel], "fold-snapshot-missing": [self.fx.rel],
            "source-incomplete": [], "ledger-record-failed": [self.fx.rel],
        }
        for kind, files in expected.items():
            with self.subTest(kind=kind):
                failure = fold_check.scoped_failure(kind, "bad", self.fx.subject(), self.fx.rows(), id="F1")
                self.assertEqual(failure["files"], files)
                self.assertEqual(failure["ids"], ["F1"])
        unknown = fold_check.scoped_failure("future-kind", "bad", self.fx.subject(), [])
        self.assertEqual(unknown["files"], [self.fx.rel])

    def test_a_fileless_application_holds_only_its_own_id(self):
        other = self.fx.root / "other.md"
        other.write_text("# Other\n", encoding="utf-8")
        rel = self.fx.rel
        self.fx.plan.write_text(PREFOLD, encoding="utf-8")
        manifest = self.fx.root / "two_snapshot.json"
        manifest.write_text(json.dumps(fold_check.snapshot_blobs(self.fx.root, [rel, "other.md"])), encoding="utf-8")
        self.fx.plan.write_text(FOLDED, encoding="utf-8")
        rows = self.fx.rows() + [{"id": "F4", "action": "FIX", "description": "fileless fix"}]
        digest = self.fx.digest(applied=[{"id": "F1", "change": "typo fixed", "source": "finding"},
                                         {"id": "F4", "change": "edit", "source": "fold"}],
                                deferred=[{"id": "F2", "class": "taste-fork", "reason": "taste fork"},
                                          {"id": "F3", "class": "spike", "reason": "spike"}])
        self.fx.digest_path.write_text(json.dumps(digest), encoding="utf-8")
        job = ["--subject", f"{self.fx.root}::{manifest}::{rel},other.md", "--digest", str(self.fx.digest_path)]
        code, report, _ = self.check(self.findings(rows), job)
        unattributed = [f for f in report["failures"] if f["kind"] == "fold-application-unattributed"]
        self.assertEqual([(f["ids"], f["files"]) for f in unattributed], [(["F4"], [])])
        self.assertFalse([f for f in report["failures"] if "F1" in f["ids"]])

    def test_ruling_validates_rewritten_text_and_seats_its_unit(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "an owner-chosen default"), encoding="utf-8")
        rows = self.fx.rows()
        rows[0]["agent"] = "harness-quality"
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "ruling"
        code, report, err = self.check(self.findings(rows), self.fx.write(digest))
        self.assertEqual(code, 0, err + json.dumps(report))
        self.assertIn("F1", report["nextRound"][0]["seats"][0]["units"])

    def test_prefixed_delta_marks_only_its_file_and_unprefixed_marks_all(self):
        other = Fixture(self.root, "other")
        subject = fold_check.Subject(self.root, [self.fx.rel, other.rel],
                                    {"kind": "blobs", "files": {**self.fx.subject().snapshot["files"],
                                                               **other.subject().snapshot["files"]}})
        digest = self.fx.digest(designDelta=[self.fx.rel + ": changed seam"])
        job = {"subject": subject, "digestValue": digest}
        block = fold_check.next_round(job, self.fx.rows(), [])
        self.assertIn("seam", block["fileUnits"][self.fx.rel])
        self.assertNotIn("seam", block["fileUnits"].get(other.rel, []))
        digest["designDelta"] = ["unnamed seam"]
        block = fold_check.next_round(job, self.fx.rows(), [])
        self.assertTrue(all("seam" in block["fileUnits"][p] for p in subject.files))

    def test_normal_fold_passes_with_equal_independent_verdicts(self):
        code, report, err = self.check(self.findings(self.fx.rows()), self.fx.write(self.fx.digest()))
        self.assertEqual(code, 0, err + json.dumps(report))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["failures"], [])
        independent = {row["id"]: row["verdict"] for row in report["independentVerdicts"]}
        self.assertEqual(independent, {"F1": "VERIFIED", "F2": "VERIFIED"})
        self.assertEqual([(row["id"], row["disposition"]) for row in report["dispositions"]],
                         [("F1", "applied"), ("F2", "deferred"), ("F3", "deferred")])

    def test_the_owner_input_and_own_design_holds_pass(self):
        rows = self.fx.rows()
        rows.append({"id": "F4", "action": "PLAN", "agent": "plc-test-readiness", "file": f"{self.fx.plan}:4",
                     "question": "which failing test?", "description": "name the failing test"})
        digest = self.fx.digest(
            deferred=[{"id": "F2", "class": "owner-input", "reason": "owner-held input"},
                      {"id": "F3", "class": "own-design", "reason": "open decision"},
                      {"id": "F4", "class": "own-design", "reason": "the open decision is the session's"}])
        code, report, err = self.check(self.findings(rows), self.fx.write(digest))
        self.assertEqual(code, 0, err + json.dumps(report))
        self.assertEqual([(row["id"], row["disposition"]) for row in report["dispositions"]],
                         [("F1", "applied"), ("F2", "deferred"), ("F3", "deferred"), ("F4", "deferred")])

    def test_plan_quote_is_checked_against_the_snapshot_bytes(self):
        _code, report, _err = self.check(self.findings(self.fx.rows()), self.fx.write(self.fx.digest()))
        f1 = next(row for row in report["independentVerdicts"] if row["id"] == "F1")
        self.assertEqual(f1["verdict"], "VERIFIED")

    def test_a_wrong_independent_verdict_on_a_deferred_id_fails_nothing(self):
        rows = self.fx.rows()
        rows[1]["evidence"][0]["quote"] = "return Something"
        code, report, err = self.check(self.findings(rows), self.fx.write(self.fx.digest()))
        self.assertEqual(code, 0, err + json.dumps(report["failures"]))
        self.assertEqual({row["id"]: row["verdict"] for row in report["independentVerdicts"]}["F2"], "UNVERIFIED")

    def test_false_mechanical_check_fails(self):
        self.fx.plan.write_text(FOLDED.replace("Step 9", "Step nine"), encoding="utf-8")
        rows = self.fx.rows()
        rows[0]["old"], rows[0]["new"] = "pick a transport", "pick the transport"
        code, report, _ = self.check(self.findings(rows), self.fx.write(self.fx.digest()))
        self.assertEqual(code, 1)
        self.assertIn("fold-not-applied", self.kinds(report))

    def test_fold_written_change_passes_without_the_findings_new_text(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "a local default"), encoding="utf-8")
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "fold"
        code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(digest))
        self.assertNotIn("fold-not-applied", self.kinds(report))
        self.assertEqual(code, 0)

    def test_fold_written_change_still_removes_the_old_text(self):
        self.fx.plan.write_text(PREFOLD.replace("Step 9", "Step 9 now"), encoding="utf-8")
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "fold"
        code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(digest))
        self.assertEqual(code, 1)
        self.assertIn("fold-not-applied", self.kinds(report))

    def test_an_applied_entry_with_no_source_is_read_as_the_finding_s_own_fix(self):
        digest = self.fx.digest()
        digest["applied"][0].pop("source")
        code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(digest))
        self.assertEqual(code, 0, json.dumps(report["failures"]))

    def next_round(self, rows, digest):
        _code, report, _err = self.check(self.findings(rows), self.fx.write(digest))
        (block,) = report["nextRound"]
        return block

    def test_next_round_is_inline_when_a_findings_own_fix_adds_no_fact(self):
        block = self.next_round(self.fx.rows(), self.fx.digest())
        self.assertEqual(block["seats"], [])
        self.assertTrue(block["inline"])

    def test_an_applied_entry_with_an_unknown_source_is_read_as_the_finding_s_own_fix(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "a local default"), encoding="utf-8")
        rows = self.fx.rows()
        rows[0]["agent"] = "plc-pattern-fit, plc-evidence-grounding"
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "Finding"
        block = self.next_round(rows, digest)
        (seat,) = [s for s in block["seats"] if s["role"] == "re-review"]
        self.assertEqual(seat["units"], ["F1"])

    def test_next_round_re_reviews_a_fold_written_unit_by_its_citing_mandates(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "a local default"), encoding="utf-8")
        rows = self.fx.rows()
        rows[0]["agent"] = "plc-pattern-fit, plc-evidence-grounding"
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "fold"
        block = self.next_round(rows, digest)
        (seat,) = [s for s in block["seats"] if s["role"] == "re-review"]
        self.assertEqual((seat["units"], seat["mandates"]), (["F1"], ["plc-evidence-grounding", "plc-pattern-fit"]))
        self.assertFalse(block["inline"])

    def test_next_round_expands_a_lens_seat_key_to_its_catalog_mandates(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "a local default"), encoding="utf-8")
        rows = self.fx.rows()
        rows[0]["agent"] = "sa-intuitiveness-testability"
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "fold"
        block = self.next_round(rows, digest)
        (seat,) = [s for s in block["seats"] if s["role"] == "re-review"]
        self.assertEqual(seat["mandates"], ["cq.I", "tq"])

    def test_next_round_does_not_widen_a_file_less_unit_to_every_file_less_finding(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "a local default"), encoding="utf-8")
        rows = self.fx.rows()
        rows[0]["agent"] = "plc-pattern-fit"
        rows[2]["agent"] = "plc-unrelated-lens"
        del rows[0]["file"], rows[2]["file"]
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "fold"
        block = self.next_round(rows, digest)
        (seat,) = [s for s in block["seats"] if s["role"] == "re-review"]
        self.assertEqual(seat["mandates"], ["plc-pattern-fit"])

    def test_next_round_re_reviews_a_unit_whose_check_failed(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "a local default"), encoding="utf-8")
        block = self.next_round(self.fx.rows(), self.fx.digest())
        self.assertEqual([s["units"] for s in block["seats"] if s["role"] == "re-review"], [["F1"]])

    def test_adapted_change_passes_without_new_text_and_skips_re_review(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "a local default"), encoding="utf-8")
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "adapted"
        code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(digest))
        self.assertEqual(code, 0, json.dumps(report["failures"]))
        (block,) = report["nextRound"]
        self.assertEqual([s for s in block["seats"] if s["role"] == "re-review"], [])

    def test_adapted_change_still_removes_the_old_text(self):
        self.fx.plan.write_text(PREFOLD.replace("Step 9", "Step 9 now"), encoding="utf-8")
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "adapted"
        code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(digest))
        self.assertIn("fold-not-applied", self.kinds(report))

    def test_a_kept_verbatim_quote_of_the_old_text_does_not_fail_the_fold(self):
        quote = 'Quoted row: "teh default is local."\n'
        self.fx.snap(PREFOLD + quote, FOLDED + quote)
        code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(self.fx.digest()))
        self.assertEqual(code, 0, json.dumps(report["failures"]))

    def test_a_pair_for_another_file_carried_into_the_plan_fails_as_old_missing(self):
        rows = self.fx.rows()
        rows[0]["old"], rows[0]["new"] = "Exception: one cell.", "Exception: a cell or a parenthetical."
        self.fx.plan.write_text(FOLDED + '`other.md` line "Exception: one cell." becomes '
                                         '"Exception: a cell or a parenthetical."\n', encoding="utf-8")
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "adapted"
        code, report, _ = self.check(self.findings(rows), self.fx.write(digest))
        self.assertEqual(code, 1)
        self.assertIn("fold-old-missing", self.kinds(report))

    def test_an_old_text_another_applied_id_introduced_is_not_missing(self):
        rows = self.fx.rows()
        rows[1].update(action="FIX", old="the default is local", new="the default is remote",
                       evidence=[], description="second pass")
        rows[0]["new"] = "the default is local"
        self.fx.plan.write_text(PREFOLD.replace("teh default is local", "the default is remote"), encoding="utf-8")
        digest = self.fx.digest(applied=[{"id": "F1", "change": "a", "source": "adapted"},
                                         {"id": "F2", "change": "b", "source": "finding"}],
                                deferred=[{"id": "F3", "class": "spike", "reason": "spike"}])
        _code, report, _ = self.check(self.findings(rows), self.fx.write(digest))
        self.assertNotIn("fold-old-missing", self.kinds(report))

    def test_an_adapted_change_that_keeps_the_old_text_and_extends_its_line_passes(self):
        self.fx.plan.write_text(PREFOLD.replace("is local.", "is local. Remote runs override it."),
                                encoding="utf-8")
        digest = self.fx.digest()
        digest["applied"][0]["source"] = "adapted"
        code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(digest))
        self.assertEqual(code, 0, json.dumps(report["failures"]))

    def test_next_round_sends_added_facts_to_grounding(self):
        self.fx.plan.write_text(FOLDED + "Retry 5 times; see `tools/x.py`.\n", encoding="utf-8")
        block = self.next_round(self.fx.rows(), self.fx.digest())
        (seat,) = [s for s in block["seats"] if s["role"] == "grounding"]
        self.assertEqual(seat["lines"], ["Retry 5 times; see `tools/x.py`."])

    def test_next_round_lists_nothing_unaccounted_for_a_transcribed_fix(self):
        block = self.next_round(self.fx.rows(), self.fx.digest())
        self.assertEqual(block["unaccounted"], [])

    def test_next_round_accounts_an_applied_first_option_whatever_its_question(self):
        rows = self.fx.rows()
        rows[2].update(question="Which transport?",
                       options=["Step 9: use the sidecar transport.", "Step 9: use the native transport."])
        self.fx.plan.write_text(FOLDED.replace("Step 9: pick a transport.", "Step 9: use the sidecar transport."),
                                encoding="utf-8")
        digest = self.fx.digest(applied=[{"id": "F1", "change": "typo fixed", "source": "finding"},
                                         {"id": "F3", "change": "option 0", "source": "finding"}],
                                deferred=[{"id": "F2", "class": "taste-fork", "reason": "taste fork"}])
        block = self.next_round(rows, digest)
        self.assertEqual(block["unaccounted"], [])

    def test_next_round_lists_added_text_no_finding_proposed_whatever_the_fold_reports(self):
        # A fold marked `finding` whose empty designDelta hides design it added: the lines come
        # from the diff, so the fold's own report never sets what the session reads.
        self.fx.plan.write_text(
            FOLDED.replace("the default is local.", "the default is local and cached per session.")
            + "Step 10: route every read through the new cache.\n", encoding="utf-8")
        block = self.next_round(self.fx.rows(), self.fx.digest(designDelta=[]))
        self.assertEqual(block["unaccounted"], ["Step 3: the default is local and cached per session.",
                                                "Step 10: route every read through the new cache."])

    def test_next_round_sends_a_design_delta_to_coherence(self):
        block = self.next_round(self.fx.rows(), self.fx.digest(designDelta=["Home: x moves to y"]))
        (seat,) = [s for s in block["seats"] if s["role"] == "coherence"]
        self.assertEqual(seat["units"], ["Home: x moves to y"])

    def measure(self, pre, post):
        (self.root / "reviewed.md").write_text(pre, encoding="utf-8")
        (self.root / "current.md").write_text(post, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = fold_check.main(["--measure", str(self.root / "reviewed.md"), str(self.root / "current.md"),
                                    "--out", str(self.out)])
        return code, json.loads(self.out.read_text(encoding="utf-8"))

    def test_measure_names_changed_units_and_the_unchanged_units_that_cite_them(self):
        code, change = self.measure(UNIT_PLAN, UNIT_PLAN.replace("Uses a scope hash.", "Uses a merge base."))
        self.assertEqual(code, 0)
        self.assertEqual(change["units"], 8)
        self.assertEqual(change["changedUnits"], ["D-a"])
        self.assertEqual(change["reach"], {"D-a": ["E1", "E3"]})
        self.assertEqual(change["unitsReached"], 2)
        self.assertEqual((change["changedLines"], change["lines"]), (1, 8))

    def test_measure_counts_an_added_unit_and_a_removed_one(self):
        post = UNIT_PLAN.replace("- **E2:** prints units.\n", "") + "- **E4:** cites E1.\n"
        _code, change = self.measure(UNIT_PLAN, post)
        self.assertEqual(change["changedUnits"], ["E2", "E4"])
        self.assertEqual(change["reach"], {"E2": [], "E4": []})

    def test_measure_takes_numbered_steps_and_headings_as_units(self):
        plan = ("# Plan\n\n## Steps\n\n1. Pick a transport.\n2. Use the transport step 1 picked.\n"
                "3. Log it.\n\n## Verify\n\nRun step 3's log check.\n")
        _code, change = self.measure(plan, plan.replace("Pick a transport.", "Pick a transport per job."))
        self.assertEqual(change["changedUnits"], ["Steps 1"])
        self.assertEqual(change["reach"], {"Steps 1": ["Steps 2"]})
        _code, change = self.measure(plan, plan.replace("Run step 3's log check.", "Run step 3's check twice."))
        self.assertEqual(change["changedUnits"], ["Verify"])

    def test_measure_reports_changed_lines_no_unit_holds(self):
        _code, change = self.measure("Intro line.\n\n# Plan\n", "Intro line, revised.\n\n# Plan\n")
        self.assertEqual((change["changedUnits"], change["unplacedLines"]), ([], 1))

    def test_measure_of_a_code_file_reads_lines_and_hunks(self):
        for name, text in (("reviewed.py", "a\nb\nc\nd\n"), ("current.py", "a\nB\nc\nd\ne\n")):
            (self.root / name).write_text(text, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = fold_check.main(["--measure", str(self.root / "reviewed.py"), str(self.root / "current.py"),
                                    "--out", str(self.out)])
        change = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((code, change["changedLines"], change["hunks"]), (0, 2, 2))

    def test_two_subject_files_sharing_a_basename_resolve_against_their_own_snapshot_bytes(self):
        for rel, text in (("a/SKILL.md", "alpha body\n"), ("b/SKILL.md", "beta body\n")):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text(text, encoding="utf-8")
        manifest = self.root / "two_snapshot.json"
        manifest.write_text(json.dumps(fold_check.snapshot_blobs(self.root, ["a/SKILL.md", "b/SKILL.md"])),
                            encoding="utf-8")
        subject = fold_check.load_subject(self.root, manifest, ["a/SKILL.md", "b/SKILL.md"])
        rows = [{"id": "F1", "evidence": [{"file": "a/SKILL.md", "quote": "alpha body"}]},
                {"id": "F2", "evidence": [{"file": "b/SKILL.md", "quote": "beta body"}]}]
        verdicts, _path = fold_check.independent_verdicts(rows, [subject], str(self.out))
        self.assertEqual(verdicts, {"F1": "VERIFIED", "F2": "VERIFIED"})

    def test_independent_verdicts_read_non_file_evidence_items(self):
        (self.root / "a.md").write_text("alpha body\n", encoding="utf-8")
        manifest = self.root / "one_snapshot.json"
        manifest.write_text(json.dumps(fold_check.snapshot_blobs(self.root, ["a.md"])), encoding="utf-8")
        subject = fold_check.load_subject(self.root, manifest, ["a.md"])
        rows = [{"id": "F1", "evidence": [{"file": "a.md", "quote": "alpha body"},
                                          {"url": "https://example.test/x", "quote": "alpha"}]},
                {"id": "F2", "evidence": [{"command": "python3 -c 1", "output": ""}]}]
        verdicts, _path = fold_check.independent_verdicts(rows, [subject], str(self.out))
        self.assertEqual(verdicts, {"F1": "UNCHECKED", "F2": "UNCHECKED"})

    def test_measure_of_an_unchanged_plan_is_zero(self):
        _code, change = self.measure(UNIT_PLAN, UNIT_PLAN)
        self.assertEqual((change["changedUnits"], change["changedLines"], change["unitsReached"]), ([], 0, 0))

    def test_next_round_carries_the_change_measure_and_no_seat_count(self):
        self.fx.plan.write_text(FOLDED + "Retry 5 times.\n", encoding="utf-8")
        block = self.next_round(self.fx.rows(), self.fx.digest())
        self.assertNotIn("panel", block)
        self.assertEqual(block["change"]["changedLines"], 2)

    def test_unchanged_plan_claim_fails(self):
        self.fx.plan.write_text(PREFOLD, encoding="utf-8")
        rows = self.fx.rows()
        rows[0].pop("old")
        rows[0].pop("new")
        code, report, _ = self.check(self.findings(rows), self.fx.write(self.fx.digest()))
        self.assertEqual(code, 1)
        self.assertEqual(self.kinds(report), ["fold-unchanged-file"])

    def test_an_applied_id_the_caller_ruled_on_may_leave_its_file_unchanged(self):
        self.fx.plan.write_text(PREFOLD, encoding="utf-8")
        rows = self.fx.rows()
        rows[0].pop("old")
        rows[0].pop("new")
        code, report, _ = self.check(self.findings(rows), self.fx.write(self.fx.digest()), ["--ruled", "F1"])
        self.assertEqual(code, 0, json.dumps(report))
        self.assertNotIn("fold-unchanged-file", self.kinds(report))

    def test_an_applied_id_with_unverified_evidence_fails_whatever_the_digest_says(self):
        rows = self.fx.rows()
        rows[0]["evidence"][0]["quote"] = "not in the plan"
        code, report, _ = self.check(self.findings(rows), self.fx.write(self.fx.digest()))
        self.assertEqual(code, 1)
        self.assertIn("fold-applied-unverified", self.kinds(report))

    def test_an_applied_id_the_caller_ruled_on_needs_no_verified_evidence(self):
        rows = self.fx.rows()
        rows[0]["evidence"][0]["quote"] = "not in the plan"
        code, report, _ = self.check(self.findings(rows), self.fx.write(self.fx.digest()), ["--ruled", "F1"])
        self.assertNotIn("fold-applied-unverified", self.kinds(report), json.dumps(report))

    def test_a_plan_with_options_is_applied_whatever_its_question(self):
        row = {"id": "F1", "action": "PLAN", "question": "Which one?", "options": ["do A", "do B"]}
        self.assertIsNone(fold_check.hold_class(row))
        self.assertEqual(fold_check.hold_class(dict(row, options=[])), "open-plan")
        self.assertEqual(fold_check.hold_class({"id": "F2", "action": "ASK", "question": "?"}), "ask")

    def test_split_plans_route_each_finding_to_one_plan(self):
        other = Fixture(self.root, "other")
        rows = self.fx.rows() + [dict(row, id=row["id"].replace("F", "G"))
                                 for row in other.rows()]
        for row in rows[3:]:
            row["id"] = "F" + str(int(row["id"][1:]) + 3)
        digest_b = other.digest(prefix="F")
        for key in ("applied", "deferred"):
            for entry in digest_b[key]:
                entry["id"] = "F" + str(int(entry["id"][1:]) + 3)
        code, report, err = self.check(self.findings(rows), self.fx.write(self.fx.digest()),
                                       other.write(digest_b))
        self.assertEqual(code, 0, err + json.dumps(report))
        files = {row["id"]: row["file"] for row in report["dispositions"]}
        self.assertEqual(files["F1"], self.fx.rel)
        self.assertEqual(files["F4"], other.rel)
        self.assertEqual(len(report["dispositions"]), 6)

    def test_a_cited_path_suffix_routes_to_the_one_plan_it_names(self):
        other = Fixture(self.root, "other")
        rows = [{"id": "F1", "file": "plan.md:3"}, {"id": "F2", "file": "other.md:5"},
                {"id": "F3", "file": str(self.fx.plan)}, {"id": "F4", "file": "an.md:1"}]
        routed, unrouted, ambiguous = fold_check.route(rows, [self.fx.subject(), other.subject()])
        self.assertEqual(routed, {"F1": 0, "F2": 1, "F3": 0})
        self.assertEqual((unrouted, ambiguous), (["F4"], []))

    def test_an_absolute_citation_routes_case_insensitively_where_normcase_does_nothing(self):
        subject = self.fx.subject()
        cited = str(self.fx.plan).upper().replace(self.fx.plan.name.upper(), self.fx.plan.name)
        with unittest.mock.patch.object(fold_check.os.path, "normcase", lambda value: value):
            self.assertEqual(subject.hits(cited), [self.fx.rel])

    def test_route_gives_every_shared_routing_vector_its_expected_result(self):
        table = json.loads((ROOT / ".claude/tests/fold_routing_vectors.json").read_text(encoding="utf-8"))
        for case in table["cases"]:
            with self.subTest(file=case["file"]):
                subjects = [fold_check.Subject(ROOT / case.get("subjectRoot", "."), [plan], {"kind": "blobs", "files": {plan: None}})
                            for plan in case.get("plans", table["plans"])]
                cited = case["file"].replace("<root>", ROOT.as_posix())
                routed, unrouted, ambiguous = fold_check.route([{"id": "F1", "file": cited}], subjects)
                got = routed.get("F1", "unrouted" if unrouted else "ambiguous" if ambiguous else None)
                self.assertEqual(got, case["expect"])

    def test_file_less_and_ambiguous_findings_fail_and_an_outside_file_is_held(self):
        nested = Fixture(self.root, "sub/plan")
        rows = self.fx.rows()
        rows.append({"id": "F4", "action": "FIX", "file": str(self.root / "elsewhere.md:1"),
                     "description": "no plan"})
        rows.append({"id": "F5", "action": "FIX", "file": "plan.md:2",
                     "description": "names one subject's file exactly and the other's by suffix"})
        rows.append({"id": "F6", "action": "FIX", "description": "cites no file, two subjects"})
        code, report, _ = self.check(self.findings(rows), self.fx.write(self.fx.digest()),
                                     nested.write({"applied": [], "deferred": [], "changedPaths": []}))
        self.assertEqual(code, 1)
        self.assertIn("fold-routing", self.kinds(report))
        state = {row["id"]: row["disposition"] for row in report["dispositions"]}
        self.assertEqual(state["F4"], "out-of-subject")
        self.assertEqual(state["F5"], "missing")
        self.assertEqual(state["F6"], "unrouted")
        routing = next(f for f in report["failures"] if f["kind"] == "fold-routing")
        self.assertEqual(sorted(routing["ids"]), ["F6"])

    def test_review_view_expands_to_one_disposition_per_source(self):
        rows = self.fx.rows(prefix="M")
        rows[0]["merged_from"] = ["F1", "F2"]
        rows[1]["id"], rows[1]["merged_from"] = "F3", ["F3"]
        rows[2]["id"], rows[2]["merged_from"] = "F4", ["F4"]
        digest = self.fx.digest()
        mapping = {"F1": "M1", "F2": "F3", "F3": "F4"}
        for key in ("applied", "deferred"):
            for entry in digest[key]:
                entry["id"] = mapping[entry["id"]]
        review = {"view": rows, "sources": [
            {'id': source, 'finding': dict(row, id=source)} for row in rows
            for source in row['merged_from']]}
        path = self.root / "review.json"
        path.write_text(json.dumps(review), encoding="utf-8")
        code, report, err = self.check(str(path), self.fx.write(digest))
        self.assertEqual(code, 0, err + json.dumps(report))
        self.assertEqual([(row["id"], row["viewId"], row["disposition"])
                          for row in report["dispositions"]],
                         [("F1", "M1", "applied"), ("F2", "M1", "applied"),
                          ("F3", "F3", "deferred"), ("F4", "F4", "deferred")])

    def workflow_dir(self, digest, rows, extra_prompt_rows=None, framed=False, fenced=False, prompt_file=None):
        run = self.root / "wf"
        run.mkdir(exist_ok=True)
        prompt = "\n".join([
            "FOLD STAGE of a review. Read t.md with the Read tool and follow it.",
            f"Root: {self.root}",
            f"Snapshot: {self.fx.manifest}",
            f"Files: {self.fx.rel}",
            f"Findings file: {self.root / 'fold_findings.json'}",
            f"Verdicts file: {self.fx.verdicts}",
            f"Digest file: {self.fx.digest_path}",
            *([f"Prompt file: {prompt_file}"] if prompt_file else []),
            "",
            f"CONSOLIDATED FINDINGS ({len(rows)} ids: {', '.join(r['id'] for r in rows)}):",
            *(["```json", json.dumps(rows).replace("`", "\\u0060"), "```"] if fenced else [json.dumps(rows)]),
            "",
            "=== ENGINE CONTRACT ===",
        ])
        seat = {"type": "user", "message": {"role": "user", "content": "Review the plan."}}
        events = []
        if framed:
            relay = ("[Workflow harness — user request] The harness relays, verbatim and indented below, the user"
                     " request that triggered this workflow run.\n  /plan_check plan.md")
            events.append({"type": "user", "message": {"role": "user", "content": relay}})
            prompt = ("[Workflow harness — computed task] The task text below was computed at runtime by a workflow"
                      " script. The computed task text follows:\n" + "\n".join("  " + l for l in prompt.splitlines()))
        events.append({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}})
        (run / "agent-seat1.jsonl").write_text(json.dumps(seat) + "\n", encoding="utf-8")
        (run / "agent-fold1.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        journal = [
            {"type": "started", "key": "k1", "agentId": "seat1", "label": "review:one", "phase": "Review"},
            {"type": "result", "key": "k1", "agentId": "seat1", "result": {"findings": [
                dict(row, agent='plc-pattern-fit', category='bug', rationale='fixture') for row in rows]}},
            {"type": "started", "key": "k2", "agentId": "fold1", "label": "review:fold", "phase": "Fold"},
            {"type": "result", "key": "k2", "agentId": "fold1", "result": digest},
        ]
        (run / "journal.jsonl").write_text("".join(json.dumps(r) + "\n" for r in journal), encoding="utf-8")
        return run

    def check_workflow(self, run):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = fold_check.main(["--workflow-dir", str(run), "--out", str(self.out)])
        report = json.loads(self.out.read_text(encoding="utf-8")) if self.out.exists() else None
        if report and "failures" in report:
            for failure in report["failures"]:
                self.assertIsInstance(failure["ids"], list)
                self.assertIsInstance(failure["files"], list)
                self.assertIn(failure["kind"], fold_check.FAILURE_SCOPE)
        return code, report, err.getvalue()

    def test_workflow_dir_reads_findings_fenced_as_data(self):
        digest = self.fx.digest()
        self.fx.write(digest)
        rows = self.fx.rows()
        rows[2]["description"] = "run `/pr_pipeline` then"
        code, report, err = self.check_workflow(self.workflow_dir(digest, rows, fenced=True))
        self.assertEqual(code, 0, err + json.dumps(report and report["failures"]))

    def test_workflow_dir_reads_the_engine_prompt_and_returned_digest(self):
        digest = self.fx.digest()
        self.fx.write(digest)
        code, report, err = self.check_workflow(self.workflow_dir(digest, self.fx.rows()))
        self.assertEqual(code, 0, err + json.dumps(report))
        self.assertEqual([row["id"] for row in report["dispositions"]], ["F1", "F2", "F3"])
        self.assertEqual(report["jobs"], [{"root": str(self.root), "files": [self.fx.rel], "newPaths": [],
                                           "snapshot": str(self.fx.manifest), "agentId": "fold1"}])

    def test_workflow_dir_excepts_the_persisted_prompt_file(self):
        prompt_file = self.root / 'fold_prompt.md'
        prompt_file.write_text('brief', encoding='utf-8')
        digest = self.fx.digest()
        digest['changedPaths'] = [self.fx.rel, str(prompt_file)]
        self.fx.write(digest)
        code, report, err = self.check_workflow(self.workflow_dir(digest, self.fx.rows(), prompt_file=prompt_file))
        self.assertEqual(code, 0, err + json.dumps(report and report["failures"]))

    def test_a_claimed_scratch_path_is_the_fold_s_working_space_not_a_subject_change(self):
        scratch = self.root / ".claude" / "scratch" / "run" / "fold_prompt.md"
        scratch.parent.mkdir(parents=True)
        scratch.write_text("brief", encoding="utf-8")
        digest = self.fx.digest()
        digest["changedPaths"] = [self.fx.rel, str(scratch), ".claude/scratch/run/helper.py"]
        self.fx.write(digest)
        code, report, err = self.check_workflow(self.workflow_dir(digest, self.fx.rows()))
        self.assertEqual(code, 0, err + json.dumps(report and report["failures"]))
        digest["changedPaths"] = [self.fx.rel, ".claude/scratchpad.py"]
        self.fx.write(digest)
        code, report, err = self.check_workflow(self.workflow_dir(digest, self.fx.rows()))
        self.assertIn("fold-out-of-subject", self.kinds(report), err)

    def test_workflow_dir_unwraps_the_runtime_task_frame(self):
        digest = self.fx.digest()
        self.fx.write(digest)
        code, report, err = self.check_workflow(self.workflow_dir(digest, self.fx.rows(), framed=True))
        self.assertEqual(code, 0, err + json.dumps(report))
        self.assertEqual([row["id"] for row in report["dispositions"]], ["F1", "F2", "F3"])
        self.assertEqual(report["jobs"], [{"root": str(self.root), "files": [self.fx.rel], "newPaths": [],
                                           "snapshot": str(self.fx.manifest), "agentId": "fold1"}])

    def test_workflow_dir_ignores_the_fold_authors_findings_copy(self):
        digest = self.fx.digest()
        self.fx.write(digest)
        rows = self.fx.rows()
        tampered = json.loads(json.dumps(rows))
        tampered[1]["evidence"][0]["quote"] = "def x():"
        (self.root / "fold_findings.json").write_text(json.dumps(tampered), encoding="utf-8")
        rows[1]["evidence"][0]["quote"] = "return Something"
        code, report, _ = self.check_workflow(self.workflow_dir(digest, rows))
        self.assertEqual(code, 0, json.dumps(report["failures"]))
        self.assertEqual({row["id"]: row["verdict"] for row in report["independentVerdicts"]}["F2"], "UNVERIFIED")

    def test_workflow_dir_without_a_fold_agent_is_unusable(self):
        run = self.workflow_dir(self.fx.digest(), self.fx.rows())
        (run / "agent-fold1.jsonl").unlink()
        code, _report, err = self.check_workflow(run)
        self.assertEqual(code, 2)
        self.assertIn("no fold agent", err)

    def test_unusable_inputs_exit_two(self):
        cases = {
            "missing findings": (str(self.root / "none.json"), self.fx.write(self.fx.digest())),
            "empty findings": (self.findings([], name="empty.json"), self.fx.write(self.fx.digest())),
            "unpaired job": (self.findings(self.fx.rows()),
                             ["--subject", f"{self.root}::{self.fx.manifest}::{self.fx.rel}"]),
        }
        for label, (findings, job) in cases.items():
            with self.subTest(label):
                if self.out.exists():
                    self.out.unlink()
                code, _report, _ = self.check(findings, job)
                self.assertEqual(code, 2)


    def test_path_predicates_give_every_shared_vector_its_expected_result(self):
        table = json.loads((ROOT / ".claude/tests/fold_routing_vectors.json").read_text(encoding="utf-8"))["paths"]
        for path in table["test"]:
            self.assertTrue(fold_check.is_test_path(path), path)
        for path in table["notTest"]:
            self.assertFalse(fold_check.is_test_path(path), path)
        for path in table["code"]:
            self.assertTrue(fold_check.is_code_path(path), path)
        for path in table["notCode"]:
            self.assertFalse(fold_check.is_code_path(path), path)

    def test_a_snapshot_blob_holds_the_pre_fold_bytes_the_author_cannot_rewrite(self):
        subject = self.fx.subject()
        self.assertEqual(subject.pre_bytes(self.fx.rel).decode("utf-8").replace("\r\n", "\n"), PREFOLD)
        self.fx.plan.write_text("rewritten by the author\n", encoding="utf-8")
        self.assertEqual(subject.post_bytes(self.fx.rel).decode("utf-8").replace("\r\n", "\n"),
                         "rewritten by the author\n")
        again = fold_check.load_subject(self.root, self.fx.manifest, [self.fx.rel])
        self.assertEqual(again.pre_bytes(self.fx.rel).decode("utf-8").replace("\r\n", "\n"), PREFOLD)

    def test_a_finding_citing_a_file_outside_every_subject_is_held_not_failed(self):
        # A review reads more than its fold may edit (a peer-dirty file kept out of the subject, a
        # commit message, a task record): such a finding goes to the session, and the fold passes.
        rows = self.fx.rows()
        rows.append({"id": "F4", "action": "FIX", "file": "other/elsewhere.py:3", "description": "x"})
        code, report, _ = self.check(self.findings(rows), self.fx.write(self.fx.digest()))
        self.assertNotIn("fold-routing", self.kinds(report))
        self.assertEqual(code, 0, report["failures"])
        state = {row["id"]: row["disposition"] for row in report["dispositions"]}
        self.assertEqual(state["F4"], "out-of-subject")

    def run_with_originals(self, extra):
        """The engine path: fold jobs hold the routed views, and the originals also hold what the engine did not route."""
        self.fx.write(self.fx.digest())
        job = {"subject": self.fx.subject(), "digest": str(self.fx.digest_path), "rows": self.fx.rows(), "ruled": []}
        return fold_check.run(None, [job], str(self.out), original=self.fx.rows() + extra)

    def test_an_original_outside_every_subject_is_held_when_the_fold_views_omit_it(self):
        report = self.run_with_originals([{"id": "F4", "action": "FIX", "file": "other/elsewhere.py:3",
                                           "description": "x"}])
        self.assertNotIn("fold-routing", self.kinds(report))
        state = {row["id"]: row["disposition"] for row in report["dispositions"]}
        self.assertEqual(state["F4"], "out-of-subject")

    def test_an_original_the_views_omit_still_fails_when_a_subject_could_take_it(self):
        report = self.run_with_originals([
            {"id": "F4", "action": "FIX", "file": f"{self.fx.plan}:3", "description": "inside the subject"},
            {"id": "F5", "action": "FIX", "description": "cites no file"}])
        routing = [f for f in report["failures"] if f["kind"] == "fold-routing"]
        self.assertEqual(sorted(i for f in routing for i in f["ids"]), ["F4", "F5"])

    def test_a_subject_whose_manifest_lacks_a_file_is_unusable(self):
        code, _report, err = self.check(
            self.findings(self.fx.rows()),
            ["--subject", f"{self.root}::{self.fx.manifest}::{self.fx.rel},missing.md", "--digest",
             str(self.fx.digest_path)])
        self.assertEqual(code, 2)
        self.assertIn("missing.md", err)



class FoldReliabilityTests(unittest.TestCase):
    """The fold reports judgment only: the checker computes every fact, and a slip holds only its own id."""
    setUp = FoldCheckTests.setUp
    findings = FoldCheckTests.findings
    check = FoldCheckTests.check
    kinds = FoldCheckTests.kinds
    next_round = FoldCheckTests.next_round

    def slim(self, applied=None, deferred=None, **over):
        value = {"applied": [{"id": "F1", "change": "typo fixed", "source": "finding"}] if applied is None else applied,
                 "deferred": [{"id": "F2", "class": "taste-fork", "reason": "owner"},
                              {"id": "F3", "class": "spike", "reason": "spike"}] if deferred is None else deferred,
                 "designDelta": [], "changedPaths": [self.fx.rel]}
        value.update(over)
        return value

    def test_a_digest_with_no_decisions_verdicts_verdict_path_or_mechanical_checks_passes(self):
        code, report, err = self.check(self.findings(self.fx.rows()), self.fx.write(self.slim()))
        self.assertEqual(code, 0, err + json.dumps(report))
        self.assertEqual([(row["id"], row["disposition"], row["taken"]) for row in report["dispositions"]],
                         [("F1", "applied", True), ("F2", "deferred", False), ("F3", "deferred", False)])

    def test_a_deferred_merged_member_whose_view_verdict_differs_from_its_own_passes(self):
        rows = self.fx.rows(prefix="M")
        rows[0]["merged_from"] = ["F1", "F2"]
        rows[1]["id"], rows[1]["merged_from"] = "F3", ["F3"]
        rows[2]["id"], rows[2]["merged_from"] = "F4", ["F4"]
        sources = [{"id": source, "finding": dict(row, id=source)} for row in rows for source in row["merged_from"]]
        sources[1]["finding"]["evidence"] = [{"file": f"{self.fx.plan}:3", "quote": "not in the plan"}]
        path = self.root / "review.json"
        path.write_text(json.dumps({"view": rows, "sources": sources}), encoding="utf-8")
        digest = self.slim(applied=[], deferred=[{"id": "M1", "class": "taste-fork", "reason": "owner"},
                                                 {"id": "F3", "class": "owner-input", "reason": "owner"},
                                                 {"id": "F4", "class": "spike", "reason": "spike"}])
        code, report, err = self.check(str(path), self.fx.write(digest))
        self.assertEqual(code, 0, err + json.dumps(report["failures"]))
        self.assertEqual({row["id"]: row["verdict"] for row in report["independentVerdicts"]}["F2"], "UNVERIFIED")

    def test_an_absent_routed_id_is_held_unreported_and_fails_nothing_else(self):
        digest = self.slim(deferred=[{"id": "F2", "class": "taste-fork", "reason": "owner"}])
        code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(digest))
        self.assertEqual(self.kinds(report), ["fold-id-coverage"])
        (failure,) = report["failures"]
        self.assertEqual(failure["ids"], ["F3"])
        self.assertEqual(fold_check.FAILURE_SCOPE["fold-id-coverage"], "id")
        self.assertEqual({row["id"]: row["disposition"] for row in report["dispositions"]}["F3"], "missing")

    def test_an_unknown_or_repeated_id_is_reported_and_ignored(self):
        digest = self.slim(deferred=[{"id": "F2", "class": "taste-fork", "reason": "owner"},
                                     {"id": "F3", "class": "spike", "reason": "spike"},
                                     {"id": "F3", "class": "spike", "reason": "again"},
                                     {"id": "F9", "class": "spike", "reason": "unknown"}])
        _code, report, _ = self.check(self.findings(self.fx.rows()), self.fx.write(digest))
        self.assertTrue(all(failure["ids"] == [] for failure in report["failures"]), report["failures"])
        self.assertEqual({row["id"]: row["disposition"] for row in report["dispositions"]},
                         {"F1": "applied", "F2": "deferred", "F3": "deferred"})

    def test_an_applied_id_whose_old_is_gone_and_whose_new_is_reworded_is_adapted_for_the_next_round(self):
        self.fx.plan.write_text(FOLDED.replace("the default", "a local default"), encoding="utf-8")
        code, report, err = self.check(self.findings(self.fx.rows()), self.fx.write(self.slim()))
        self.assertEqual(code, 0, err + json.dumps(report["failures"]))
        (block,) = report["nextRound"]
        (seat,) = [s for s in block["seats"] if s["role"] == "re-review"]
        self.assertEqual(seat["units"], ["F1"])
        self.assertIn("adapted:F1", block["fileUnits"][self.fx.rel])

    def test_an_applied_id_whose_old_is_still_present_is_not_applied_and_holds_only_itself(self):
        rows = self.fx.rows()
        rows[1].update(action="FIX", old="pick a transport", new="pick the transport", evidence=[],
                       description="second edit")
        digest = self.slim(applied=[{"id": "F1", "change": "typo", "source": "finding"},
                                    {"id": "F2", "change": "pick", "source": "finding"}],
                           deferred=[{"id": "F3", "class": "spike", "reason": "spike"}])
        code, report, _ = self.check(self.findings(rows), self.fx.write(digest))
        self.assertEqual(code, 1)
        (failure,) = report["failures"]
        self.assertEqual((failure["kind"], failure["ids"]), ("fold-not-applied", ["F2"]))
        self.assertEqual(fold_check.FAILURE_SCOPE["fold-not-applied"], "id")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


class FoldPromptPathTests(unittest.TestCase):
    def test_prompt_sits_beside_the_engine_named_digest(self):
        self.assertEqual(Path(fold_check.fold_prompt_path("s/fold_digest.json")), Path("s/fold_prompt.md"))
        self.assertEqual(Path(fold_check.fold_prompt_path("s/fold2_digest.json")), Path("s/fold2_prompt.md"))

    def test_other_digest_names_have_no_prompt(self):
        for name in ("digest.json", "my_fold_digest.json", "fold_digest.json.bak", "fold_notes.md"):
            self.assertIsNone(fold_check.fold_prompt_path("s/" + name), name)


class PathTableTests(unittest.TestCase):
    def test_the_test_roots_follow_the_project_adaptation(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude = Path(tmp)
            (claude / "skills" / "project_subsystems").mkdir(parents=True)
            (claude / "skills" / "project_subsystems" / "adaptation.json").write_text(
                json.dumps({"tests_root": "Specs"}), encoding="utf-8")
            self.assertEqual(fold_check.test_dirs(claude), ("Specs", ".claude/tests"))

    def test_a_cs_file_and_every_harness_script_kind_are_code_and_a_proof_is_not(self):
        for path, code in ((".claude/tools/x.py", True), (".claude/settings.base.json", True), ("Src/A.cs", True),
                           (".claude/workflows/x.mjs", True), (".claude/tests/test_x.py", False), ("doc.md", False),
                           (".claude/tools/X.PY", True), ("Src/B.CS", True)):
            with self.subTest(path):
                self.assertIs(fold_check.is_code_path(path), code)

    def test_a_worktree_prefixed_citation_hits_the_subject_file_of_its_own_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / ".claude" / "worktrees" / "sync-pr101"
            rel = ".claude/tools/post_check.py"
            subject = fold_check.Subject(root, [rel], {"kind": "blobs", "files": {rel: None}})
            cases = {
                "relative": ".claude/worktrees/sync-pr101/.claude/tools/post_check.py",
                "absolute": (root / rel).as_posix(),
            }
            for label, cited in cases.items():
                with self.subTest(form=label):
                    self.assertEqual(subject.hits(cited), [rel])
                    self.assertEqual(subject.exact_hits(cited), [rel])

    def test_a_citation_naming_another_worktree_does_not_hit_this_subject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / ".claude" / "worktrees" / "sync-pr101"
            rel = ".claude/tools/post_check.py"
            subject = fold_check.Subject(root, [rel], {"kind": "blobs", "files": {rel: None}})
            self.assertEqual(subject.hits(".claude/worktrees/other-wt/.claude/tools/post_check.py"), [])

    def test_importing_fold_check_does_not_load_the_proof_runner(self):
        probe = "import sys; sys.path.insert(0, sys.argv[1]); import fold_check; print('harness_tests' in sys.modules)"
        done = subprocess.run([sys.executable, "-c", probe, str(ROOT / ".claude/tools")],
                              capture_output=True, text=True, check=True)
        self.assertEqual(done.stdout.strip(), "False", done.stderr)


class CodeSubjectTests(unittest.TestCase):
    """A rev subject in a temp git repo: a code file, its test path and a fix commit history."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.art_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.art_tmp.cleanup)
        self.art = Path(self.art_tmp.name)
        git(self.root, "init", "-q")
        (self.root / "src.py").write_text("def f():\n    return 1\n", encoding="utf-8", newline="\n")
        git(self.root, "add", "src.py")
        git(self.root, "commit", "-q", "-m", "base")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.manifest = self.art / "snap.json"
        self.manifest.write_text(json.dumps(fold_check.snapshot_rev(self.root, self.base)), encoding="utf-8")
        self.out = self.art / "report.json"
        self.digest_path = self.art / "digest.json"

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message, *paths):
        git(self.root, "add", *paths)
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD")

    def fix(self):
        self.write("src.py", "def f():\n    return 2\n")

    def write_test_file(self):
        self.write("Tests/test_src.py", "from src import f\n\n\ndef test_f():\n    assert f() == 2\n")

    def row(self):
        return {"id": "F1", "action": "FIX", "file": "src.py:2", "description": "wrong value",
                "old": "return 1", "new": "return 2"}

    def digest(self, **over):
        value = {"applied": [{"id": "F1", "change": "value", "source": "finding"}], "deferred": [],
                 "designDelta": [], "changedPaths": ["src.py"]}
        value.update(over)
        return value

    def check(self, digest, files=("src.py",), new_paths=("Tests/test_src.py",)):
        findings = self.art / "findings.json"
        findings.write_text(json.dumps({"findings": [self.row()]}), encoding="utf-8")
        self.digest_path.write_text(json.dumps(digest), encoding="utf-8")
        spec = f"{self.root}::{self.manifest}::{','.join(files)}::{','.join(new_paths)}"
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = fold_check.main(["--findings", str(findings), "--subject", spec, "--digest",
                                    str(self.digest_path), "--out", str(self.out)])
        report = json.loads(self.out.read_text(encoding="utf-8")) if self.out.exists() else None
        if report and "failures" in report:
            for failure in report["failures"]:
                self.assertIsInstance(failure["ids"], list)
                self.assertIsInstance(failure["files"], list)
                self.assertIn(failure["kind"], fold_check.FAILURE_SCOPE)
        return code, report, err.getvalue()

    def kinds(self, report):
        return sorted({failure["kind"] for failure in report["failures"]})

    def test_a_fix_with_no_test_in_the_change_set_fails_as_untested_code(self):
        self.fix()
        self.commit("fix", "src.py")
        code, report, err = self.check(self.digest())
        self.assertEqual(code, 1, err)
        self.assertEqual(self.kinds(report), ["fold-untested-code"])

    def test_a_failed_git_diff_against_the_base_fails_closed(self):
        self.fix()
        self.commit("fix", "src.py")
        self.manifest.write_text(json.dumps({"kind": "rev", "base": "no-such-rev"}), encoding="utf-8")
        code, report, err = self.check(self.digest())
        self.assertEqual(code, 1, err)
        self.assertIn("fold-snapshot-missing", self.kinds(report))

    def test_a_no_test_reason_passes_the_check_and_sends_the_unit_to_re_review(self):
        self.fix()
        self.commit("fix", "src.py")
        digest = self.digest()
        digest["applied"][0]["noTest"] = "comment-only"
        code, report, err = self.check(digest)
        self.assertEqual(code, 0, err + json.dumps(report["failures"]))
        (block,) = report["nextRound"]
        (seat,) = [s for s in block["seats"] if s["role"] == "re-review"]
        self.assertEqual(seat["units"], ["F1"])

    def test_a_test_commit_before_the_fix_commit_passes(self):
        self.write_test_file()
        test_sha = self.commit("test: f is 2", "Tests/test_src.py")
        self.fix()
        fix_sha = self.commit("fix: f is 2", "src.py")
        digest = self.digest(changedPaths=["src.py", "Tests/test_src.py"], commits=[
            {"sha": test_sha, "ids": ["F1"], "kind": "test"}, {"sha": fix_sha, "ids": ["F1"], "kind": "fix"}])
        code, report, err = self.check(digest)
        self.assertEqual(code, 0, err + json.dumps(report["failures"]))

    def test_a_fix_commit_before_its_test_commit_fails_as_test_order(self):
        self.fix()
        fix_sha = self.commit("fix: f is 2", "src.py")
        self.write_test_file()
        test_sha = self.commit("test: f is 2", "Tests/test_src.py")
        digest = self.digest(changedPaths=["src.py", "Tests/test_src.py"], commits=[
            {"sha": fix_sha, "ids": ["F1"], "kind": "fix"}, {"sha": test_sha, "ids": ["F1"], "kind": "test"}])
        code, report, _ = self.check(digest)
        self.assertEqual(code, 1)
        self.assertEqual(self.kinds(report), ["fold-test-order"])

    def test_a_commit_labelled_test_that_touches_no_test_path_fails(self):
        self.fix()
        sha = self.commit("fix", "src.py")
        self.write_test_file()
        self.commit("test", "Tests/test_src.py")
        digest = self.digest(commits=[{"sha": sha, "ids": ["F1"], "kind": "test"}])
        code, report, _ = self.check(digest)
        self.assertIn("fold-test-order", self.kinds(report))

    def test_a_write_outside_the_subject_fails_even_when_the_digest_omits_it(self):
        self.write_test_file()
        self.commit("test", "Tests/test_src.py")
        self.fix()
        self.write("other.py", "x = 1\n")
        self.commit("fix", "src.py", "other.py")
        code, report, _ = self.check(self.digest())
        self.assertEqual(code, 1)
        self.assertIn("fold-out-of-subject", self.kinds(report))

    def test_another_file_under_tests_is_outside_the_subject(self):
        self.write_test_file()
        self.write("Tests/test_other.py", "x = 1\n")
        self.fix()
        self.commit("all", "src.py", "Tests/test_src.py", "Tests/test_other.py")
        code, report, _ = self.check(self.digest())
        self.assertEqual(code, 1)
        self.assertIn("fold-out-of-subject", self.kinds(report))
        detail = next(f["detail"] for f in report["failures"] if f["kind"] == "fold-out-of-subject")
        self.assertIn("Tests/test_other.py", detail)
        self.assertNotIn("Tests/test_src.py", detail)

    def test_a_non_markdown_change_is_measured_as_lines_and_hunks(self):
        measure = fold_check.change_measure(["a", "b", "c", "d"], ["a", "B", "c", "d", "e"], markdown=False)
        self.assertEqual((measure["changedLines"], measure["hunks"], measure["changedUnits"]), (2, 2, []))

    def test_a_blob_subject_s_change_set_is_its_declared_paths_only(self):
        init = Fixture(self.root, "plan")
        (self.root / "Tests").mkdir(exist_ok=True)
        (self.root / "Tests" / "test_new.py").write_text("x = 1\n", encoding="utf-8")
        undeclared = fold_check.load_subject(self.root, init.manifest, [init.rel], [])
        self.assertNotIn("Tests/test_new.py", undeclared.change_set(),
                         "a test file no subject declared may be a peer's edit in a shared checkout")
        self.assertNotIn("Tests/test_new.py", undeclared.allowed)
        declared = fold_check.load_subject(self.root, init.manifest, [init.rel], ["Tests/test_new.py"])
        self.assertIn("Tests/test_new.py", declared.change_set())
        self.assertIn("Tests/test_new.py", declared.allowed)


if __name__ == "__main__":
    unittest.main()
