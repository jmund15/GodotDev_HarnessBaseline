#!/usr/bin/env python3
"""Re-runnable proof: `orchestration_metrics.py --session <id>` resolves the id to its session
directory before collecting.

`find_session_dir(session_id)` exists for exactly this, but `main()` passed the raw id string to
`collect()` / `pending_report()`, which treat it as a directory path. Every `--session <id>` run
therefore printed "No Workflow runs found" or "unrated dispatches: 0" for a session that had
dispatched work: missing data read as a clean no-op. Observed 2026-09-14 on a session with 13
workflow runs and 62 agents.

Each case builds a throwaway ~/.claude/projects tree (HOME/USERPROFILE redirected) holding one
workflow run with one unrated agent, then runs the real CLI.

    python3 .claude/tests/test_orchestration_metrics_session_id.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "tools", "orchestration_metrics.py")
SID = "0a1b2c3d-4e5f-4a6b-8c7d-session-id-proof"
SID_AMBIG = "0a1b2c3d-ffff-4a6b-8c7d-ambiguous-proof"
LABEL = "session-id-proof:lane"


def build_session(home, sid, label, with_uncaptured_launch=False):
    session = os.path.join(home, ".claude", "projects", "C--proof-project", sid)
    os.makedirs(os.path.join(session, "workflows"))
    os.makedirs(os.path.join(session, "subagents", "workflows", "wf_proof-001"))
    run = {"runId": "wf_proof-001", "workflowName": "session-id-proof", "status": "completed",
           "logs": ["PINS " + json.dumps({label: "medium"})],
           "workflowProgress": [{"type": "workflow_agent", "label": label, "agentId": "aproof1",
                                 "model": "claude-sonnet-5", "state": "done"}]}
    with open(os.path.join(session, "workflows", "wf_proof-001.json"), "w", encoding="utf-8") as fh:
        json.dump(run, fh)
    rows = []
    if with_uncaptured_launch:
        rows.append({
            "type": "assistant", "cwd": home,
            "message": {"role": "assistant", "content": [{
                "type": "tool_use", "name": "Bash",
                "input": {"command": "python3 .claude/tools/sidecar_fanout.py uncaptured/jobs.json"},
            }]},
        })
    with open(os.path.join(home, ".claude", "projects", "C--proof-project", sid + ".jsonl"), "w",
              encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return session


def build_home(home):
    session = build_session(home, SID, LABEL, with_uncaptured_launch=True)
    build_session(home, SID_AMBIG, "session-id-proof:ambiguous")
    return session


def run_cli(home, *args, cwd=None):
    # CLAUDE_PROJECT_DIR anchors the archive and verdicts files: a report run archives what it
    # collects, so without this the proof's fake run lands in the real project archive.
    env = dict(os.environ, HOME=home, USERPROFILE=home, CLAUDE_PROJECT_DIR=home,
               PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    r = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, env=env,
                       cwd=cwd, encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


class SessionIdResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = self.tmp.name
        self.session = build_home(self.home)

    def test_pending_with_a_session_id_counts_its_unrated_agent(self):
        rc, out = run_cli(self.home, "--pending", "--session", SID, "--no-sidecar")
        self.assertEqual(0, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("unrated dispatches: 1", out, out)
        self.assertIn(LABEL, out, out)
        self.assertNotIn("sidecar:", out, out)
        self.assertNotIn("fanout launch", out, out)

    def test_report_with_a_session_id_finds_its_runs(self):
        rc, out = run_cli(self.home, "--session", SID, "--no-sidecar")
        self.assertEqual(0, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertNotIn("No Workflow runs found", out, out)
        self.assertIn(LABEL, out, out)

    def test_pending_names_a_label_at_run_id_verdict_key(self):
        with open(os.path.join(self.home, ".claude", "orchestration_verdicts.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({LABEL + "@wf_proof-001": "clean"}, fh)
        rc, out = run_cli(self.home, "--pending", "--session", SID, "--no-sidecar")
        self.assertEqual(0, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("unrated dispatches: 1", out, out)
        self.assertIn('"%s@wf_proof-001"' % LABEL, out, out)
        self.assertIn("wf_proof-001:" + LABEL, out, out)

    def test_existing_absolute_and_relative_session_directories_are_accepted(self):
        rc, out = run_cli(self.home, "--session", self.session, "--no-sidecar")
        self.assertEqual(0, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn(LABEL, out, out)

        relative = os.path.relpath(self.session, self.home)
        rc, out = run_cli(self.home, "--session", relative, "--no-sidecar", cwd=self.home)
        self.assertEqual(0, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn(LABEL, out, out)

    def test_a_unique_session_id_prefix_is_accepted(self):
        rc, out = run_cli(self.home, "--pending", "--session", SID[:12], "--no-sidecar")
        self.assertEqual(0, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn(LABEL, out, out)

    def test_an_ambiguous_session_id_prefix_resolves_to_nothing(self):
        rc, out = run_cli(self.home, "--pending", "--session", SID[:8], "--no-sidecar")
        self.assertEqual(1, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("Could not locate a session directory", out, out)

    def test_manifest_rejects_an_unresolved_explicit_session(self):
        seed = os.path.join(self.home, "seed.json")
        output = os.path.join(self.home, "manifest.json")
        records = os.path.join(self.home, "records")
        os.makedirs(records)
        with open(seed, "w", encoding="utf-8") as fh:
            json.dump({"jobs": [{"label": "manifest-sidecar"}]}, fh)
        with open(os.path.join(records, "manifest.record.json"), "w", encoding="utf-8") as fh:
            json.dump({"label": "manifest-sidecar", "exitCode": 0}, fh)
        rc, out = run_cli(
            self.home, "--manifest-seed", seed, "--manifest-out", output,
            "--sidecar-record-dir", records, "--session", "no-such-session")
        self.assertEqual(1, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("Could not locate a session directory", out, out)
        self.assertFalse(os.path.exists(output), out)

    def test_an_unknown_session_id_says_so(self):
        rc, out = run_cli(self.home, "--pending", "--session", "no-such-session", "--no-sidecar")
        self.assertEqual(1, rc, out)
        self.assertNotIn("Traceback", out, out)
        self.assertIn("Could not locate a session directory", out, out)


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=1))
