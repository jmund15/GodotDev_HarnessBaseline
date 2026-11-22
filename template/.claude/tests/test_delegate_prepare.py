#!/usr/bin/env python3
"""Proof for `lens_briefs.py prepare` with `mode: "delegate"`: the ad-hoc dispatch shape's prepare route.

An ad-hoc job's jobs file used to be hand-built: each job's `workdir` a worktree made by hand first
(`git worktree add -q -b scratch/<w> .claude/worktrees/<w> HEAD`), each `promptFile` an absolute path, `role`
copied from `tier`. One call now writes the fan-out's jobs file from a short spec, creates the author jobs'
worktrees, runs the size preflight and prints the launch call and each job's return call. A bad spec refuses
before any worktree exists. Every case runs the CLI against a real git repository under its own temp root.

    python3 .claude/tests/test_delegate_prepare.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
TOOL = TOOLS / "lens_briefs.py"
sys.path.insert(0, str(TOOLS))
import model_registry  # noqa: E402

# A planted registry and ladder (copies of the live files, written under each case's temp root and named by
# HARNESS_MODEL_REGISTRY), with one fixed alias and the rung the proof pins. No registry row carries an effort
# block of its own: the ladder is the only source.
ALIAS = "sonnet"
EFFORT = "medium"
LIVE_REGISTRY = model_registry.find_registry()
LIVE_LADDER = model_registry.ladder_path()


def git(cwd, *args, check=True):
    done = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and done.returncode:
        raise AssertionError("git %s failed: %s" % (" ".join(args), done.stderr))
    return done.stdout.strip()


class Delegate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="delegate_prepare_"))
        self.root = self.tmp / "repo"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        (self.root / "f.txt").write_text("x\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self.brief = self.tmp / "brief_a.md"
        self.brief.write_text("do the thing\n", encoding="utf-8")
        self.out = self.tmp / "run"
        reference = self.tmp / "planted" / ".claude" / "reference"
        reference.mkdir(parents=True)
        (reference / LIVE_REGISTRY.name).write_bytes(LIVE_REGISTRY.read_bytes())
        (reference / LIVE_LADDER.name).write_bytes(LIVE_LADDER.read_bytes())
        self.env_before = os.environ.get("HARNESS_MODEL_REGISTRY")
        os.environ["HARNESS_MODEL_REGISTRY"] = str(reference / LIVE_REGISTRY.name)

    def tearDown(self):
        if self.env_before is None:
            os.environ.pop("HARNESS_MODEL_REGISTRY", None)
        else:
            os.environ["HARNESS_MODEL_REGISTRY"] = self.env_before
        for line in git(self.root, "worktree", "list", "--porcelain", check=False).splitlines():
            if line.startswith("worktree ") and Path(line[9:]).resolve() != self.root.resolve():
                git(self.root, "worktree", "remove", "--force", line[9:], check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def job(self, **over):
        job = {"label": "alpha", "brief": str(self.brief), "kind": "author", "tier": "executor",
               "rows": {ALIAS: EFFORT}, "pick": "cell", "spec": "judged", "worktree": True}
        job.update(over)
        return {k: v for k, v in job.items() if v is not None}

    def prepare(self, jobs, **spec):
        spec = dict({"mode": "delegate", "root": str(self.root), "jobs": jobs}, **spec)
        path = self.tmp / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        done = subprocess.run([sys.executable, str(TOOL), "prepare", "--spec", str(path), "--out", str(self.out)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        return done.returncode, done.stdout, done.stderr

    def jobs_file(self):
        return json.loads((self.out / "jobs.json").read_text(encoding="utf-8"))

    def test_an_author_job_gets_its_worktree_and_a_complete_jobs_entry(self):
        code, out, err = self.prepare([self.job()])
        self.assertEqual(code, 0, err + out)
        work = self.root / ".claude" / "worktrees" / "alpha"
        self.assertTrue(work.is_dir())
        self.assertEqual(git(work, "rev-parse", "--abbrev-ref", "HEAD"), "scratch/alpha")
        self.assertEqual(git(work, "rev-parse", "HEAD"), git(self.root, "rev-parse", "HEAD"))
        [job] = self.jobs_file()["jobs"]
        self.assertEqual(Path(job["workdir"]).resolve(), work.resolve())
        self.assertEqual(Path(job["promptFile"]).resolve(), self.brief.resolve())
        self.assertEqual((job["label"], job["kind"], job["tier"], job["role"]), ("alpha", "author", "executor", "executor"))
        self.assertEqual((job["rows"], job["pick"], job["spec"]), ({ALIAS: EFFORT}, "cell", "judged"))
        self.assertNotIn("brief", job)
        self.assertNotIn("worktree", job)

    def test_a_named_worktree_and_a_read_only_job_without_one(self):
        code, _, err = self.prepare([self.job(worktree="alpha-wt"),
                                     self.job(label="scout", kind="scout", worktree=None, tier="fanout")])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / ".claude" / "worktrees" / "alpha-wt").is_dir())
        alpha, scout = self.jobs_file()["jobs"]
        self.assertIn("alpha-wt", alpha["workdir"])
        self.assertNotIn("workdir", scout)
        self.assertEqual(git(self.root, "branch", "--list", "scratch/scout"), "")

    def test_a_gate_job_is_a_kind_a_delegate_may_name(self):
        code, _, err = self.prepare([self.job(label="gate", kind="gate", worktree=None, tier="executor")])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.jobs_file()["jobs"][0]["kind"], "gate")

    def test_spill_and_context_pass_through_and_the_launch_and_return_calls_print(self):
        context = self.tmp / "ctx.md"
        context.write_text("c\n", encoding="utf-8")
        code, out, err = self.prepare([self.job(), self.job(label="scout", kind="scout", worktree=None, tier="fanout")],
                                      spillDir=str(self.tmp / "spill"), contextPath=str(context))
        self.assertEqual(code, 0, err)
        written = self.jobs_file()
        self.assertEqual((written["spillDir"], written["contextPath"]), (str(self.tmp / "spill"), str(context)))
        self.assertIn("python3 .claude/tools/sidecar_fanout.py", out)
        self.assertIn("jobs.json", out)
        self.assertIn("--detach", out)
        self.assertIn("watch_records.py --session --wake --await-launch", out)
        self.assertIn("dispatch_result.py take", out)
        self.assertIn("--label alpha", out)
        self.assertIn("dispatch_result.py consume", out)
        self.assertIn("--label scout", out)

    def test_the_size_preflight_runs_on_the_file_it_wrote(self):
        _, out, _ = self.prepare([self.job()])
        self.assertIn("dispatch size:", out)

    def test_a_bad_spec_refuses_before_any_worktree_exists(self):
        cases = {
            "missing brief": [self.job(), self.job(label="b", brief=str(self.tmp / "nope.md"))],
            "unknown kind": [self.job(), self.job(label="b", kind="wizard")],
            "no tier": [self.job(), self.job(label="b", tier=None)],
            "rows and model": [self.job(), self.job(label="b", model="sonnet", effort="high")],
            "no pin": [self.job(), self.job(label="b", rows=None)],
            "duplicate label": [self.job(), self.job()],
            "unsafe label": [self.job(), self.job(label="a/b")],
        }
        for name, jobs in cases.items():
            with self.subTest(name):
                code, out, err = self.prepare(jobs)
                self.assertNotEqual(code, 0, out)
                self.assertTrue((err or out).strip(), name)
                self.assertFalse((self.root / ".claude" / "worktrees" / "alpha").exists(), name + ": a worktree was made")
                self.assertFalse((self.out / "jobs.json").exists(), name + ": a jobs file was written")

    def test_a_worktree_name_already_taken_refuses_and_names_it(self):
        git(self.root, "branch", "scratch/alpha")
        code, out, err = self.prepare([self.job()])
        self.assertNotEqual(code, 0)
        self.assertIn("scratch/alpha", err + out)
        self.assertFalse((self.root / ".claude" / "worktrees" / "alpha").exists())

    def test_a_model_and_effort_pair_is_accepted_in_place_of_rows(self):
        code, _, err = self.prepare([self.job(rows=None, model=ALIAS, effort=EFFORT)])
        self.assertEqual(code, 0, err)
        [job] = self.jobs_file()["jobs"]
        self.assertEqual((job["model"], job["effort"]), (ALIAS, EFFORT))
        self.assertNotIn("rows", job)


if __name__ == "__main__":
    unittest.main()
