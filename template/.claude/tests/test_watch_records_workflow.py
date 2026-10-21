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


class Session:
    """A planted login: <config>/projects/p/<SID>.jsonl beside its <SID>/ session dir, and an empty hook state."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="wr_workflow_")
        self.config = os.path.join(self.root, "config")
        project = os.path.join(self.config, "projects", "p")
        self.dir = os.path.join(project, SID)
        self.state = os.path.join(self.root, "state")
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

    def watch(self, *flags):
        env = dict(os.environ, CLAUDE_CODE_SESSION_ID=SID, CLAUDE_CONFIG_DIR=self.config, HARNESS_HOOK_STATE_DIR=self.state,
                   WATCH_RECORDS_POLL_SEC="0.1", PYTHONIOENCODING="utf-8")
        return subprocess.Popen([sys.executable, os.path.join(TOOLS, "watch_records.py"), "--session", "--wake", *flags],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def outcome(proc, seconds):
    """('NONE'|..., text) when the watcher exits within `seconds`, else ('ALIVE', '') after stopping it."""
    try:
        out, err = proc.communicate(timeout=seconds)
        return out.strip(), err.strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return "ALIVE", ""


class WorkflowOnDisk(unittest.TestCase):
    def setUp(self):
        self.s = Session()
        self.addCleanup(self.s.close)

    def test_running_run_with_no_table_entry_keeps_the_watcher_alive(self):
        self.s.start_run()
        self.assertEqual(outcome(self.s.watch(), 3)[0], "ALIVE")

    def test_run_that_started_inside_the_launch_grace_is_found(self):
        proc = self.s.watch("--await-launch=2")
        time.sleep(0.5)
        self.s.start_run()
        self.assertEqual(outcome(proc, 5)[0], "ALIVE")

    def test_nothing_running_still_exits_none(self):
        text, _ = outcome(self.s.watch(), 6)
        self.assertEqual(text, "NONE")

    def test_ended_run_is_not_live(self):
        self.s.start_run()
        self.s.end_run()
        text, _ = outcome(self.s.watch(), 6)
        self.assertTrue(text.endswith("NONE"), text)
        self.assertNotIn("ALIVE", text)

    def test_run_dir_idle_past_the_stall_window_twice_over_is_a_dead_run(self):
        self.s.start_run(age=2 * 600 + 60)
        text, _ = outcome(self.s.watch(), 6)
        self.assertEqual(text, "NONE")


if __name__ == "__main__":
    unittest.main()
