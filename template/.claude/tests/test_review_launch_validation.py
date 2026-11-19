"""Re-runnable proof: a review launch validates its subject before it spends a seat.

`sidecar_fanout.py --review` defaults the claims and quote root to the one workdir every job names,
and refuses, naming every bad entry in one message, a roster that carries lens keys instead of
mandate ids, a blob map entry that is a gitlink or a missing object, and `mustRead` or context paths
that do not exist.

    python3 -m pytest .claude/tests/test_review_launch_validation.py
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".claude" / "tools"))
import review_result  # noqa: E402
import sidecar_fanout as fanout  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, check=True).stdout.strip()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.repo = self.dir / "subject"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        (self.repo / "a.md").write_text("one\ntwo\n", encoding="utf-8")
        git(self.repo, "add", "a.md")
        git(self.repo, "commit", "-q", "-m", "init")
        self.blob = git(self.repo, "rev-parse", "HEAD:a.md")
        self.commit = git(self.repo, "rev-parse", "HEAD")
        self.context = self.dir / "ctx.md"
        self.context.write_text("context\n", encoding="utf-8")
        self.prompt = self.dir / "p.md"
        self.prompt.write_text("review it\n", encoding="utf-8")

    def job(self, label="one", **over):
        job = {"label": label, "kind": "reviewer", "alias": "opus", "promptFile": str(self.prompt),
               "mandates": ["plc-instruction-quality"], "contextFiles": [str(self.context)],
               "mustRead": [{"path": "a.md", "lines": 2}], "workdir": str(self.repo)}
        job.update(over)
        return job

    def write(self, name, value):
        path = self.dir / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    @staticmethod
    def plenty(plan, _data=None, **_kw):
        """A plan with room, so placement never reads this machine's credentials, which a runner lacks."""
        now = time.time()
        window = {"name": "seven_day", "usedPercent": 10.0, "resetsAt": now + 3 * 24 * 3600,
                  "windowDurationMins": 7 * 24 * 60, "pressure": 0.2, "band": "Surplus"}
        return {"schemaVersion": 1, "transport": plan, "costModel": "plan-quota", "sourceKind": "live",
                "observedAt": "2027-01-15T08:00:00Z", "status": "available",
                "quota": {"windows": [window], "routingWindow": "seven_day", "bindingWindow": "seven_day",
                          "routingBand": "Surplus", "dispatchBand": "Surplus"}}

    def launch(self, jobs, *flags):
        """`--review --dry-run` through main(): (exit code, stderr)."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.object(fanout.provider_bands, "read_capacity", self.plenty):
            code = fanout.main([self.write("jobs.json", jobs), "--review", "--dry-run",
                                "--out-dir", str(self.dir / "out"), *flags])
        return code, err.getvalue()


class SubjectRoot(Fixture):
    def test_one_shared_workdir_is_the_default_root(self):
        jobs = [self.job("a"), self.job("b")]
        self.assertEqual(review_result.review_root(jobs, None), str(self.repo))

    def test_explicit_root_wins(self):
        self.assertEqual(review_result.review_root([self.job()], "elsewhere"), "elsewhere")

    def test_mixed_workdirs_without_root_name_both(self):
        other = self.dir / "other"
        with self.assertRaises(ValueError) as caught:
            review_result.review_root([self.job("a"), self.job("b", workdir=str(other))], None)
        self.assertIn(str(self.repo), str(caught.exception))
        self.assertIn(str(other), str(caught.exception))

    def test_a_job_without_workdir_leaves_the_root_unset(self):
        self.assertIsNone(review_result.review_root([self.job("a"), self.job("b", workdir=None)], None))

    def test_mixed_workdirs_fail_the_launch_before_any_seat(self):
        code, err = self.launch([self.job("a"), self.job("b", workdir=str(self.dir))])
        self.assertEqual(code, 2)
        self.assertIn("different workdirs", err)


class LaunchProblems(Fixture):
    def problems(self, jobs=None, **kw):
        return review_result.launch_problems(jobs or [self.job()], root=str(self.repo), **kw)

    def test_sound_launch_has_no_problems(self):
        roster = self.write("roster.json", [{"key": "plc-instruction-quality", "mandates": ["plc-instruction-quality"]}])
        blobs = self.write("blobs.json", {"a.md": self.blob})
        self.assertEqual(self.problems(roster=roster, blobs=blobs), [])

    def test_lens_keys_in_place_of_mandate_ids(self):
        roster = self.write("roster.json", [{"key": "lane-code-reviewer", "mandates": ["lane-code-reviewer", "no-such"]}])
        found = self.problems(roster=roster)
        self.assertEqual(len(found), 2)
        self.assertTrue(any("lane-code-reviewer" in line and "lens key" in line for line in found))
        self.assertTrue(any("'no-such'" in line for line in found))

    def test_gitlink_and_missing_object_in_the_blob_map(self):
        blobs = self.write("blobs.json", {"Jmodot": self.commit, "gone.md": "0" * 40, "a.md": self.blob})
        found = self.problems(blobs=blobs)
        self.assertEqual(len(found), 2)
        self.assertTrue(any(line.startswith("--ledger-blobs Jmodot") and "gitlink" in line for line in found))
        self.assertTrue(any(line.startswith("--ledger-blobs gone.md") and "missing" in line for line in found))

    def test_missing_mustread_and_context_paths_are_all_named(self):
        job = self.job(mustRead=[{"path": "a.md", "lines": 2}, {"path": "nope.md", "lines": 9}],
                       contextFiles=[str(self.context), str(self.dir / "absent.md")])
        found = self.problems([job])
        self.assertEqual(len(found), 2)
        self.assertTrue(any("nope.md" in line for line in found))
        self.assertTrue(any("absent.md" in line for line in found))


class Launch(Fixture):
    def test_each_mistake_refuses_before_launch_with_every_entry_named(self):
        roster = self.write("roster.json", [{"key": "lane-code-reviewer", "mandates": ["lane-code-reviewer"]}])
        blobs = self.write("blobs.json", {"Jmodot": self.commit})
        job = self.job(mustRead=[{"path": "nope.md", "lines": 1}])
        code, err = self.launch([job], "--ledger-stage", "plan", "--ledger-subject", "s",
                                "--ledger-roster", roster, "--ledger-blobs", blobs)
        self.assertEqual(code, 2)
        for needle in ("nope.md", "lane-code-reviewer", "--ledger-blobs Jmodot"):
            self.assertIn(needle, err)

    def test_sound_launch_proceeds_with_the_workdir_as_root(self):
        code, err = self.launch([self.job()])
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
