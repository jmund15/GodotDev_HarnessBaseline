"""A session watcher sees a running Workflow run from disk, not only from the launch hook's table entry.

The run is cut from a captured one (wf_3193d0a9-1e4: its journal and its end record, shared with
test_orchestration_metrics_failed_retry.py). In the captured sessions the watcher printed NONE within
the --await-launch grace while such a run was alive: the entry the PostToolUse launch hook writes was not
in the table yet (the hook waits up to launch_grace() after the call returns, and a call denied and
re-issued lands later than the arm), and nothing else told the watcher the run existed.

    python3 .claude/tests/test_watch_records_workflow.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "tools")
FIXTURE = os.path.join(HERE, "fixtures", "om_failed_retry")
RUN = "wf_3193d0a9-1e4"
SID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
# A hang bound, never a wait: every wait is for the watcher's own poll-finished mark.
HANG_BOUND = 120


class Session:
    """A planted login: <config>/projects/p/<SID>.jsonl beside its <SID>/ session dir, and an empty hook state."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="wr_workflow_")
        self.config = os.path.join(self.root, "config")
        project = os.path.join(self.config, "projects", "p")
        self.dir = os.path.join(project, SID)
        self.state = os.path.join(self.root, "state")
        self.polls = os.path.join(self.root, "polls.log")
        os.makedirs(self.dir)
        os.makedirs(self.state)
        with open(os.path.join(project, SID + ".jsonl"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"timestamp": "2026-10-20T13:26:50.715Z"}) + "\n")
        self.run_dir = os.path.join(self.dir, "subagents", "workflows", RUN)

    def start_run(self, age=0):
        shutil.copytree(os.path.join(FIXTURE, "subagents", "workflows", RUN), self.run_dir)
        stamp = time.time() - age
        for name in os.listdir(self.run_dir):
            os.utime(os.path.join(self.run_dir, name), (stamp, stamp))

    def end_run(self):
        os.makedirs(os.path.join(self.dir, "workflows"), exist_ok=True)
        shutil.copy(os.path.join(FIXTURE, "workflows", RUN + ".json"), os.path.join(self.dir, "workflows", RUN + ".json"))

    def wait_polls(self, proc, polls=2, seconds=HANG_BOUND):
        """True once `polls` more polls finished with the watcher alive; False when it exited first."""
        target = poll_count(self.polls) + polls
        deadline = time.time() + seconds
        while time.time() < deadline:
            if poll_count(self.polls) >= target:
                return True
            if proc.poll() is not None:
                return False
            time.sleep(0.02)
        return poll_count(self.polls) >= target

    def watch(self, *flags):
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, CLAUDE_CONFIG_DIR=self.config, HARNESS_HOOK_STATE_DIR=self.state,
                   WATCH_RECORDS_POLL_SEC="0.1", PYTHONIOENCODING="utf-8", WATCH_RECORDS_POLL_MARK=self.polls)
        open(self.polls, "w").close()
        proc = subprocess.Popen([sys.executable, os.path.join(TOOLS, "watch_records.py"), "--session", "--wake", *flags],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        proc.mark = self.polls
        return proc

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def poll_count(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return len(fh.read().splitlines())
    except OSError:
        return 0


def outcome(proc, polls=2):
    """('ALIVE', '') once `polls` more polls finished with the watcher alive: a silence verdict waits for the poll that
    made the stay decision, never for the clock. (`text, err`) when it exited first."""
    target = poll_count(proc.mark) + polls
    deadline = time.time() + HANG_BOUND
    while time.time() < deadline and proc.poll() is None and poll_count(proc.mark) < target:
        time.sleep(0.02)
    if proc.poll() is not None:
        out, err = proc.communicate()
        return out.strip(), err.strip()
    proc.kill()
    proc.communicate()
    return "ALIVE", ""


class WorkflowOnDisk(unittest.TestCase):
    def setUp(self):
        self.s = Session()
        self.addCleanup(self.s.close)

    def test_running_run_with_no_table_entry_keeps_the_watcher_alive(self):
        self.s.start_run()
        self.assertEqual(outcome(self.s.watch())[0], "ALIVE")

    def test_run_that_started_inside_the_launch_grace_is_found(self):
        proc = self.s.watch("--await-launch=2")
        self.assertTrue(self.s.wait_polls(proc, 1), "the watcher polled before the run appeared")
        self.s.start_run()
        self.assertEqual(outcome(proc)[0], "ALIVE")

    def test_nothing_running_keeps_session_watcher_armed(self):
        self.assertEqual(outcome(self.s.watch())[0], "ALIVE")

    def test_ended_run_does_not_open_a_bookkeeping_turn(self):
        self.s.start_run()
        self.s.end_run()
        self.assertEqual(outcome(self.s.watch())[0], "ALIVE")

    def test_run_dir_idle_past_the_stall_window_twice_over_is_not_a_wake(self):
        self.s.start_run(age=2 * 600 + 60)
        self.assertEqual(outcome(self.s.watch())[0], "ALIVE")


if __name__ == "__main__":
    unittest.main()
