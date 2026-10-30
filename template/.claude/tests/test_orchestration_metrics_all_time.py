#!/usr/bin/env python3
"""Re-runnable proof: `orchestration_metrics.py --all-time` sweeps only THIS project's
sessions, not every project on the machine.

`_candidate_project_dirs()` slugified cwd with `:`/path-separator substitution only, but
Claude Code's own project-dir naming also maps `_` to `-`. Any cwd containing an underscore
(this repo's own path does: `Game_Dev`) matched zero project dirs, so the "no match" fallback
fired and returned EVERY project dir under `~/.claude/projects` -- `--all-time` silently
pooled Workflow rows from unrelated projects into a report that documents itself as
"across this project's whole history". Observed 2026-09-18, session dfe017b3
(sa-architecture-sweep F1): 631 candidate dirs, 314 sessions swept, 110 actually this
project's.

Each case builds a throwaway `~/.claude/projects` tree (HOME/USERPROFILE redirected) with
two sessions under a project dir whose name is the correctly-slugified cwd (underscore
included), plus one session under an unrelated peer project dir, then runs the real CLI
with that cwd.

    python3 .claude/tests/test_orchestration_metrics_all_time.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")


def slugify(cwd):
    return cwd.replace(":", "-").replace(os.sep, "-").replace("/", "-").replace("_", "-")


def write_workflow_run(session, run_id, label):
    os.makedirs(os.path.join(session, "workflows"), exist_ok=True)
    os.makedirs(os.path.join(session, "subagents", "workflows", run_id), exist_ok=True)
    run = {"runId": run_id, "workflowName": "all-time-proof", "status": "completed",
           "logs": ["PINS " + json.dumps({label: "medium"})],
           "workflowProgress": [{"type": "workflow_agent", "label": label,
                                 "agentId": run_id + "-a1", "model": "claude-sonnet-5",
                                 "state": "done"}]}
    with open(os.path.join(session, "workflows", run_id + ".json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh)


def build_home(home, cwd_dir):
    own_project = os.path.join(home, ".claude", "projects", slugify(os.path.abspath(cwd_dir)))
    write_workflow_run(os.path.join(own_project, "session-one"), "wf_own-001", "own:session-one")
    write_workflow_run(os.path.join(own_project, "session-two"), "wf_own-002", "own:session-two")

    peer_project = os.path.join(home, ".claude", "projects", "C--unrelated-other-project")
    write_workflow_run(os.path.join(peer_project, "session-peer"), "wf_peer-001", "peer:should-not-appear")
    return own_project


def run_cli(home, cwd_dir, *args):
    env = dict(os.environ, HOME=home, USERPROFILE=home, CLAUDE_PROJECT_DIR=home,
               PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("CLAUDE_CONFIG_DIR", None)  # a real second-account dir would override the planted home
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, env=env,
                       cwd=cwd_dir, encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class AllTimeProjectScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        # The bug only reproduces when cwd contains '_', mirroring this repo's own path.
        self.cwd_dir = os.path.join(self.home, "Work_Dir", "My_Project")
        os.makedirs(self.cwd_dir)
        build_home(self.home, self.cwd_dir)

    def test_all_time_includes_both_sessions_of_this_project(self):
        rc, out = run_cli(self.home, self.cwd_dir, "--all-time")
        self.assertEqual(0, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("own:session-one", out, out)
        self.assertIn("own:session-two", out, out)

    def test_all_time_excludes_an_unrelated_peer_project(self):
        rc, out = run_cli(self.home, self.cwd_dir, "--all-time")
        self.assertEqual(0, rc, out)
        self.assertNotIn("peer:should-not-appear", out, out)

    def test_all_time_prints_its_scope_boundary(self):
        rc, out = run_cli(self.home, self.cwd_dir, "--all-time")
        self.assertEqual(0, rc, out)
        self.assertIn("NOT the account's global usage page", out, out)

    def test_all_time_names_rows_already_archived(self):
        archive = os.path.join(self.home, ".claude", "orchestration_metrics.jsonl")
        with open(archive, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"run": "wf_own-001", "label": "own:session-one"}) + "\n")
        rc, out = run_cli(self.home, self.cwd_dir, "--all-time")
        self.assertEqual(0, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("skipping 1 agent record(s) already in the archive ledger", out, out)
        self.assertIn("own:session-two", out, out)
        self.assertNotIn("own:session-one", out, out)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1))
