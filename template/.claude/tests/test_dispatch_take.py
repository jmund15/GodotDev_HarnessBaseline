#!/usr/bin/env python3
"""Proof for `dispatch_result.py take`: one call prints every fact a returned author job's session needs.

The session's judgment (which commits land, the rating, whether to retire) stays its own; `take` prints the
facts around it: the job's turn reports, its commits (full shas, landed or not), the proof asserts the job
removed and whether each one still stands, new files with their baseline row, the changed proof files (run on
request), whether another job still runs with no watcher armed, and the retire verdict. Every case runs the CLI
against a real git repository and a worktree on its own branch, with a fan-out out dir planted in the shape the
launcher writes it (the stream events are the shapes a live run's `.record.json.out` holds).

    python3 .claude/tests/test_dispatch_take.py
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
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parent / "hooks"))
TOOL = TOOLS / "dispatch_result.py"

REPORT = "Done: the gate is fixed.\n" + "\n".join("detail line %d" % i for i in range(6))
REPLY = "No more work needed: that denial was already handled."
STREAM = [
    {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "reading the brief"},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "x"}}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
    {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": REPORT}]}},
    {"type": "user", "message": {"role": "user", "content": [
        {"type": "text", "text": "Stop hook feedback:\nAuto mode denied: a command."}]}},
    {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": REPLY}]}},
    {"type": "result", "result": REPLY},
]
STREAM_TEXT = "\n".join(json.dumps(row) for row in STREAM) + "\n"

TEST_A = """import unittest


class A(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)
        self.assertEqual(2, 2)
        self.assertEqual(3, 3)
"""
# test_a loses `assertEqual(3, 3)` (moved to test_b, so it stands) and `assertEqual(2, 2)` (dropped).
TEST_A_AFTER = TEST_A.replace("        self.assertEqual(2, 2)\n", "").replace("        self.assertEqual(3, 3)\n", "")
TEST_B = """import unittest


class B(unittest.TestCase):
    def test_moved(self):
        self.assertEqual(3, 3)
"""
TEST_BAD = """import sys
sys.exit(1)
"""


def git(cwd, *args, check=True):
    done = subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and done.returncode:
        raise AssertionError("git %s failed: %s" % (" ".join(args), done.stderr))
    return done.stdout.strip()


def put(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def take(out, *args, env=None):
    done = subprocess.run([sys.executable, str(TOOL), "take", str(out), *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    return done.returncode, done.stdout, done.stderr


class Take(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dispatch_take_"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        put(self.repo, ".claude/tests/test_a.py", TEST_A)
        put(self.repo, ".claude/baseline.lock.json", json.dumps({"files": {
            ".claude/tools/old.py": {"status": "tracked"}}}))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        self.wt = self.tmp / "wt"
        git(self.repo, "worktree", "add", "-q", "-b", "scratch/job", str(self.wt), "HEAD")
        put(self.wt, ".claude/tests/test_a.py", TEST_A_AFTER)
        put(self.wt, ".claude/tests/test_b.py", TEST_B)
        put(self.wt, ".claude/tools/new_tool.py", "X = 1\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "first change")
        put(self.wt, ".claude/tools/second.py", "Y = 2\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "second change")
        self.shas = git(self.wt, "rev-list", "--reverse", "main..HEAD").split()
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.plant_job("job", ended=True, workdir=self.wt)

    def tearDown(self):
        git(self.repo, "worktree", "remove", "--force", str(self.wt), check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plant_job(self, label, ended, workdir=None, stream=STREAM_TEXT, attempt=1, code="0"):
        record = self.out / (label + (".attempt%d" % attempt if attempt > 1 else "") + ".record.json")
        record.write_text(json.dumps({"label": label}), encoding="utf-8")
        Path(str(record) + ".out").write_text(stream, encoding="utf-8")
        Path(str(record) + ".pid").write_text("1", encoding="utf-8")
        if ended:
            Path(str(record) + ".exit").write_text(code, encoding="utf-8")
        if workdir:
            inv = self.out / "fanout-1.inventory.json"
            jobs = json.loads(inv.read_text(encoding="utf-8"))["jobs"] if inv.is_file() else []
            jobs.append({"label": label, "workdir": str(workdir), "brief": str(self.tmp / "brief.md")})
            inv.write_text(json.dumps({"jobs": jobs}), encoding="utf-8")

    def land(self, *shas):
        """Cherry-pick onto main after an unrelated commit: a pick whose parent is main's tip keeps its sha."""
        put(self.repo, "unrelated.txt", "x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "unrelated")
        git(self.repo, "cherry-pick", *shas)

    def run_take(self, *extra):
        env = dict(os.environ, HARNESS_HOOK_STATE_DIR=str(self.tmp / "state"), CLAUDE_CODE_SESSION_ID="takeproof-1")
        return take(self.out, "--label", "job", "--root", str(self.repo), *extra, env=env)

    def test_one_call_prints_the_facts(self):
        code, out, err = self.run_take()
        self.assertIn("REPORT", out, err)
        self.assertIn("Done: the gate is fixed.", out)
        self.assertIn("REPLY", out)
        self.assertIn("No more work needed", out)
        self.assertIn("exit=0", out)
        for sha in self.shas:
            self.assertIn(sha, out)
        self.assertLess(out.index(self.shas[0]), out.index(self.shas[1]), "oldest first")
        for sha in self.shas:
            self.assertIn("new " + sha, out)

    def test_removed_assert_is_kept_when_moved_and_missing_when_dropped(self):
        code, out, _ = self.run_take()
        self.assertIn("  MISSING self.assertEqual(2, 2)", out)
        self.assertNotIn("MISSING self.assertEqual(3, 3)", out)
        self.assertIn("removed 2", out)
        self.assertIn("kept 1", out)
        self.assertEqual(code, 1, "a missing assert is a finding the exit code carries")

    def test_new_files_show_their_baseline_row(self):
        put(self.wt, ".claude/baseline.lock.json", json.dumps({"files": {
            ".claude/tools/old.py": {"status": "tracked"}, ".claude/tools/new_tool.py": {"status": "local"}}}))
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "row")
        _, out, _ = self.run_take()
        rows = {line.split()[1]: line for line in out.splitlines() if line.startswith("NEW ")}
        self.assertIn("lock=local", rows[".claude/tools/new_tool.py"])
        self.assertIn("lock=none", rows[".claude/tools/second.py"])

    def test_landed_commits_are_named_landed_and_the_rest_new(self):
        self.land(self.shas[0])
        _, out, _ = self.run_take()
        self.assertIn("landed " + self.shas[0], out)
        self.assertIn("new " + self.shas[1], out)
        self.assertNotIn("new " + self.shas[0], out)

    def test_proofs_run_only_on_request_and_report_each_verdict(self):
        put(self.wt, ".claude/tests/test_bad.py", TEST_BAD)
        put(self.wt, ".claude/tests/test_good.py", "print('fine')\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "proofs")
        _, listed, _ = self.run_take()
        self.assertIn("PROOF .claude/tests/test_bad.py not-run", listed)
        _, ran, _ = self.run_take("--run-proofs")
        self.assertIn("PROOF .claude/tests/test_good.py PASS", ran)
        self.assertIn("PROOF .claude/tests/test_bad.py FAIL", ran)

    def test_uncommitted_work_is_named(self):
        put(self.wt, ".claude/tools/left_behind.py", "Z = 3\n")
        _, out, _ = self.run_take()
        self.assertIn("DIRTY", out)
        self.assertIn(".claude/tools/left_behind.py", out)

    def test_a_running_peer_with_no_watcher_prints_the_rearm_call(self):
        self.plant_job("peer", ended=False)
        _, out, _ = self.run_take()
        self.assertIn("RUNNING 1: peer", out)
        self.assertIn("WATCHER NOT ARMED", out)
        self.assertIn("watch_records.py --session --wake --await-launch", out)

    def test_a_live_session_watcher_is_reported_armed(self):
        sys.path.insert(0, str(TOOLS))
        import watch_records
        state = self.tmp / "state"
        state.mkdir()
        (state / "takeproo.json").write_text(json.dumps({"sidecar_watchers": [
            {"process": watch_records.process_line(), "records": [], "session": True}]}), encoding="utf-8")
        self.plant_job("peer", ended=False)
        _, out, _ = self.run_take()
        self.assertIn("WATCHER armed", out)
        self.assertNotIn("NOT ARMED", out)

    def test_no_peer_needs_no_watcher(self):
        _, out, _ = self.run_take()
        self.assertIn("WATCHER not needed", out)

    def test_retire_waits_for_the_landing_then_asks_the_delete_guard(self):
        _, before, _ = self.run_take()
        self.assertIn("RETIRE not yet: 2 commit(s) are not on main", before)
        self.land(*self.shas)
        _, after, _ = self.run_take()
        self.assertRegex(after, r"RETIRE .*blocked")
        self.assertNotIn("CHERRY-PICK", after)

    def test_no_ready_cherry_pick_or_rate_command_is_printed(self):
        """Which commits land and the rating stay the session's judgment: take prints the new shas as facts."""
        _, out, _ = self.run_take()
        self.assertIn("new ", out)
        self.assertNotIn("git cherry-pick", out)
        self.assertNotIn("orchestration_metrics.py --rate", out)

    def test_a_relaunched_job_reads_its_latest_attempt_and_is_not_its_own_peer(self):
        stopped = [{"type": "result", "result": "Stopped: the plan's usage limit."}]
        self.plant_job("job", ended=True, stream="\n".join(json.dumps(r) for r in stopped) + "\n", code="10")
        self.plant_job("job", ended=False, attempt=2)
        code, _, err = self.run_take()
        self.assertEqual(code, 2, "attempt 2 still runs, so the job has not ended")
        self.assertIn("not ended", err)
        Path(str(self.out / "job.attempt2.record.json") + ".exit").write_text("0", encoding="utf-8")
        _, out, _ = self.run_take()
        self.assertIn("Done: the gate is fixed.", out)
        self.assertIn("exit=0", out)
        self.assertNotIn("usage limit", out)
        self.assertIn("WATCHER not needed", out)
        self.plant_job("peer", ended=False)
        self.plant_job("peer", ended=True, attempt=2)
        _, out, _ = self.run_take()
        self.assertIn("WATCHER not needed", out, "a superseded attempt with no exit file is not a running peer")
        os.remove(str(self.out / "peer.attempt2.record.json.exit"))
        _, out, _ = self.run_take()
        self.assertIn("RUNNING 1: peer", out)

    def test_unended_job_is_refused(self):
        self.plant_job("job", ended=False)
        os.remove(str(self.out / "job.record.json.exit"))
        code, _, err = self.run_take()
        self.assertEqual(code, 2)
        self.assertIn("not ended", err)

    def test_a_job_with_no_workdir_prints_its_reports_alone(self):
        self.plant_job("scout", ended=True)
        done = subprocess.run([sys.executable, str(TOOL), "take", str(self.out), "--label", "scout",
                               "--root", str(self.repo)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("REPORT", done.stdout)
        self.assertIn("NO WORKDIR", done.stdout)
        self.assertNotIn("COMMITS", done.stdout)

    def test_a_schema_job_reports_its_structured_answer_not_a_progress_note(self):
        """A review seat answers through the StructuredOutput tool, so its last text-only message is a mid-run
        note and the result event carries the answer. Event shapes cut from a live sa-harness-quality seat."""
        findings = {"findings": [{"agent": "sa-harness-quality", "action": "FIX", "file": "x.md:24",
                                  "description": "the marker was retired"}]}
        stream = [
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "Still reading the commit packet. No findings sent yet."}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "s1", "name": "StructuredOutput", "input": findings}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"tool_use_id": "s1", "type": "tool_result", "content": "Structured output provided successfully"}]}},
            {"type": "result", "subtype": "success", "result": json.dumps(findings), "structured_output": findings},
        ]
        self.plant_job("seat", ended=True, stream="\n".join(json.dumps(row) for row in stream) + "\n")
        done = subprocess.run([sys.executable, str(TOOL), "take", str(self.out), "--label", "seat",
                               "--root", str(self.repo)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("the marker was retired", done.stdout.split("REPORT", 1)[1])
        self.assertNotIn("Still reading", done.stdout)


class WakeLine(unittest.TestCase):
    def test_an_author_job_wake_names_the_take_call(self):
        tmp = Path(tempfile.mkdtemp(prefix="dispatch_take_wake_"))
        try:
            record = tmp / "job.record.json"
            record.write_text(json.dumps({"label": "job"}), encoding="utf-8")
            Path(str(record) + ".exit").write_text("0", encoding="utf-8")
            (tmp / "fanout-1.inventory.json").write_text(json.dumps({"jobs": [
                {"label": "job", "workdir": str(tmp / "wt")}, {"label": "scout"}]}), encoding="utf-8")
            import wake_digest
            lines = wake_digest.sidecar_lines("job", str(record), str(tmp))
            self.assertTrue(any(l.strip().startswith("take: python3 .claude/tools/dispatch_result.py take ")
                                and "--label job" in l for l in lines), lines)
            scout = tmp / "scout.record.json"
            scout.write_text(json.dumps({"label": "scout"}), encoding="utf-8")
            self.assertFalse(any("take:" in l for l in wake_digest.sidecar_lines("scout", str(scout), str(tmp))))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
