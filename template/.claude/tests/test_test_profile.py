"""The per-test profile tool ranks, totals and reports TRX problems by name."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / ".claude/tools/test_profile.py"
SPEC = importlib.util.spec_from_file_location("test_profile", TOOL)
test_profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(test_profile)

NS = "http://microsoft.com/schemas/VisualStudio/TeamTest/2010"


def trx(rows):
    """rows: (className, method, seconds, outcome, message|None)."""
    results, defs = [], []
    for i, (cls, method, secs, outcome, message) in enumerate(rows):
        tid = f"id-{i}"
        info = ""
        if message is not None:
            info = f"<Output><ErrorInfo><Message>{message}</Message></ErrorInfo></Output>"
        results.append(
            f'<UnitTestResult testId="{tid}" testName="{cls}.{method}" '
            f'duration="00:00:{secs:09.6f}" outcome="{outcome}">{info}</UnitTestResult>')
        defs.append(f'<UnitTest id="{tid}"><TestMethod className="{cls}" name="{method}" /></UnitTest>')
    return (f'<?xml version="1.0" encoding="utf-8"?><TestRun xmlns="{NS}">'
            f'<Results>{"".join(results)}</Results>'
            f'<TestDefinitions>{"".join(defs)}</TestDefinitions></TestRun>')


LOGIC = "{{PROJECT_NAME}}.Tests.Logic.Movement.BallisticTests"
CAST = "{{PROJECT_NAME}}.Tests.Integration.Casting.CastTests"
DUNG = "{{PROJECT_NAME}}.Tests.Integration.Dungeon.FloorTests"


class TestProfile(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, name, rows):
        (self.dir / name).write_text(trx(rows), encoding="utf-8")

    def fixture(self):
        self.write("logic.trx", [(LOGIC, "A", 0.5, "Passed", None), (LOGIC, "B", 0.1, "Passed", None)])
        self.write("cast.trx", [(CAST, "C", 4.0, "Passed", None),
                                (CAST, "D", 2.0, "Failed", "Expected 1 but was 2")])
        self.write("dung.trx", [(DUNG, "E", 3.0, "Passed", None)])

    def test_slowest_n_order(self):
        self.fixture()
        report = test_profile.build_report(self.dir, top=3)
        self.assertEqual([t["name"] for t in report["slowest"]],
                         [f"{CAST}.C", f"{DUNG}.E", f"{CAST}.D"])

    def test_per_segment_and_class_sums(self):
        self.fixture()
        report = test_profile.build_report(self.dir, top=5)
        self.assertEqual(report["segments"], {"Casting": 6.0, "Dungeon": 3.0, "Logic": 0.6})
        self.assertAlmostEqual(report["classes"][CAST], 6.0)
        self.assertEqual(report["test_count"], 5)

    def test_parameterized_case_joins_its_class(self):
        # GdUnit4 writes a parameterized case as className=<Class>.<Method>, name=<Method>:<i> (<arg>).
        self.write("param.trx", [(f"{CAST}.P", "P:1 (3)", 1.5, "Passed", None), (CAST, "C", 4.0, "Passed", None)])
        report = test_profile.build_report(self.dir, top=5)
        self.assertEqual(set(report["classes"]), {CAST})
        self.assertAlmostEqual(report["classes"][CAST], 5.5)
        self.assertIn(f"{CAST}.P:1 (3)", [t["name"] for t in report["slowest"]])

    def test_segment_rules(self):
        seg = test_profile.segment_of
        self.assertEqual(seg("{{PROJECT_NAME}}.Tests.Integration.Casting.X"), "Casting")
        self.assertEqual(seg("{{PROJECT_NAME}}.Tests.Logic.Movement.X"), "Logic")
        self.assertEqual(seg("{{PROJECT_NAME}}.Tests.Sanity.X"), "Sanity")
        self.assertEqual(seg("Elsewhere.X"), "Elsewhere")

    def test_share_columns(self):
        self.fixture()
        share = test_profile.build_report(self.dir, top=1)["shares"]
        self.assertEqual(sorted(share), ["1", "10", "5"])
        self.assertAlmostEqual(share["1"], 4.0 / 9.6)
        self.assertAlmostEqual(share["10"], 4.0 / 9.6)

    def test_failure_message_extracted(self):
        self.fixture()
        failed = test_profile.build_report(self.dir, top=1)["failed"]
        self.assertEqual(failed, [{"name": f"{CAST}.D", "message": "Expected 1 but was 2"}])

    def test_malformed_trx_reported_by_name(self):
        self.fixture()
        bad = self.dir / "broken.trx"
        bad.write_text("<TestRun><Results>", encoding="utf-8")
        report = test_profile.build_report(self.dir, top=3)
        self.assertEqual([p["path"] for p in report["problems"]], [str(bad)])
        self.assertEqual(report["test_count"], 5)

    def test_empty_dir_reported_by_name(self):
        report = test_profile.build_report(self.dir, top=3)
        self.assertEqual(len(report["problems"]), 1)
        self.assertIn(str(self.dir), report["problems"][0]["path"])
        self.assertEqual(report["test_count"], 0)

    def write_unit(self, unit, rows=None):
        unit_dir = self.dir / unit
        unit_dir.mkdir()
        (unit_dir / "wrapper.out").write_text("STATUS=HANG\n", encoding="utf-8")
        if rows is not None:
            (unit_dir / f"{unit}.trx").write_text(trx(rows), encoding="utf-8")
        return unit_dir

    def test_unit_dir_without_trx_reported(self):
        self.write_unit("Integration_B1", [(CAST, "C", 4.0, "Passed", None)])
        hung = self.write_unit("Integration_B2")
        report = test_profile.build_report(self.dir, top=3)
        self.assertEqual([p["path"] for p in report["problems"]], [str(hung)])
        self.assertEqual(report["test_count"], 1)

    def test_retry_pass_supersedes_first_pass(self):
        self.write_unit("Integration_B3", [(CAST, "C", 4.0, "Failed", "flake"), (CAST, "D", 2.0, "Passed", None)])
        self.write_unit("Integration_B3_retry", [(CAST, "C", 1.0, "Passed", None), (CAST, "D", 2.0, "Passed", None)])
        self.write_unit("Logic")
        self.write_unit("Logic_retry", [(LOGIC, "A", 0.5, "Passed", None)])
        report = test_profile.build_report(self.dir, top=5)
        self.assertEqual(report["test_count"], 3)
        self.assertAlmostEqual(report["total_seconds"], 3.5)
        self.assertEqual(report["failed"], [])
        self.assertEqual(report["problems"], [])

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)

    def test_cli_json_exit_zero(self):
        self.fixture()
        done = self.run_cli(str(self.dir), "--top", "2", "--json")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(json.loads(done.stdout)["slowest"]), 2)

    def test_cli_text_exit_one_on_malformed(self):
        self.fixture()
        bad = self.dir / "broken.trx"
        bad.write_text("nope", encoding="utf-8")
        done = self.run_cli(str(self.dir))
        self.assertEqual(done.returncode, 1)
        self.assertIn(str(bad), done.stdout)
        self.assertIn("Casting", done.stdout)

    def test_cli_missing_dir_exit_two(self):
        self.assertEqual(self.run_cli(str(self.dir / "absent")).returncode, 2)


if __name__ == "__main__":
    unittest.main()
