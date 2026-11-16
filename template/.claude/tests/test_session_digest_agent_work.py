#!/usr/bin/env python3
"""Proof for the session digest handoff's agent-work section (the agent-work section of tools/session_digest.py).

The failure this guards: a session that worked for hours after the owner's last prompt resumed from a
packet holding the owner's words, one closing message and file names, so the resuming session re-read the
transcript to learn what the agent did. The fixture is a session whose agent keeps working after the last
owner prompt: it commits, pushes, dispatches two jobs (one returns a deliverable, one has not), starts two
background jobs (one ends, one has no end notice), writes a state file and a Close doc, has a dispatch
denied, and ends on a message that states two decisions for the owner. The packet must tell a reader each
of those from the transcript, the disk and git, point at the files it did not copy, and stay inside
HANDOFF_MAX_BYTES.

Run: python3 .claude/tests/test_session_digest_agent_work.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / ".claude" / "tools" / "session_digest.py"
SID = "abcd1234-0000-4000-8000-000000000001"
OWNER_TS = "2026-10-01T10:30:00.000Z"
STATE_BODY = "STATE_BODY_SENTINEL the state file's own text is the file's, not the packet's"
FINAL_TEXT = (
    "The retry fix is in and the suite is running.\n\n"
    "## Not done\n"
    "- The integration suite has not finished; its result decides the schema change.\n"
    "- The audit-cache review has not returned.\n\n"
    "## Decisions for you\n"
    "- **Q1 - Freeze the quest schema?** I recommend yes. Default: schema stays open.\n"
    "- **Q2 - Delete the old cache folder?** No recommendation. Default: it stays.\n")


def git(repo, *args, when=None):
    env = dict(os.environ)
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.invalid", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True, env=env)


class Transcript:
    """Builds transcript rows in the shape Claude Code writes them."""

    def __init__(self, cwd):
        self.rows = []
        self.cwd = str(cwd)
        self.n = 0

    def _row(self, kind, ts, **fields):
        self.n += 1
        row = {"type": kind, "uuid": f"u{self.n}", "timestamp": ts, "sessionId": SID, "cwd": self.cwd,
               "isSidechain": False}
        row.update(fields)
        self.rows.append(row)
        return row

    def owner(self, ts, text):
        self._row("user", ts, message={"role": "user", "content": text}, userType="external")

    def text(self, ts, text):
        self._row("assistant", ts, message={"role": "assistant", "model": "m", "content": [
            {"type": "text", "text": text}]})

    def call(self, ts, name, inputs, result, error=False, result_fields=None):
        self.n += 1
        use_id = f"toolu_{self.n}"
        self._row("assistant", ts, message={"role": "assistant", "model": "m", "content": [
            {"type": "tool_use", "id": use_id, "name": name, "input": inputs}]})
        self._row("user", ts, message={"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": use_id, "content": result, "is_error": error}]},
            toolUseResult=result_fields or {"stdout": result})

    def notice(self, ts, task_id, summary):
        self._row("user", ts, message={"role": "user", "content": (
            f"<task-notification>\n<task-id>{task_id}</task-id>\n<status>completed</status>\n"
            f"<summary>{summary}</summary>\n</task-notification>")})

    def queued_notice(self, ts, task_id, summary):
        """The same notice as the client enqueues it: a `queue-operation` row, shaped like the live transcript's."""
        self.n += 1
        self.rows.append({"type": "queue-operation", "operation": "enqueue", "timestamp": ts, "sessionId": SID,
                          "content": (f"<task-notification>\n<task-id>{task_id}</task-id>\n"
                                      f"<tool-use-id>toolu_x</tool-use-id>\n<status>completed</status>\n"
                                      f"<summary>{summary}</summary>\n</task-notification>")})

    def save(self, store):
        store.mkdir(parents=True, exist_ok=True)
        path = store / f"{SID}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in self.rows) + "\n", encoding="utf-8")
        return path


def fanout_launch(jobs_dir, labels):
    lines = [f"[fanout] {label}: admitted on anthropic-secondary (sonnet, high)" for label in labels]
    lines.append(f"fan-out detached: driver pid 1; its summary goes to {jobs_dir.as_posix()}/fanout-driver.log, "
                 f"and each job still writes {jobs_dir.as_posix()}/<label>.md.")
    return "\n".join(lines)


def agent_section(packet):
    """The `## Agent work` section alone: the lines up to the next `## ` heading."""
    return re.split(r"\n## ", packet.split("## Agent work", 1)[1], maxsplit=1)[0]


def run_digest(project, config, session=SID[:8], extra=()):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
    env["CLAUDE_CONFIG_DIR"] = str(config)
    done = subprocess.run([sys.executable, str(TOOL), "--project-dir", str(project), "--session", session,
                           "--handoff", *extra], capture_output=True, text=True, encoding="utf-8", env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout


class AgentWorkPacket(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.repo = base / "repo"
        cls.repo.mkdir()
        cls.config = base / "config"
        git(cls.repo, "init", "-q", "-b", "main")
        (cls.repo / "a.txt").write_text("one\n")
        git(cls.repo, "add", "a.txt")
        git(cls.repo, "commit", "-q", "-m", "feat(cache): add the quest cache", when="2026-10-01T10:10:00+00:00")
        (cls.repo / "a.txt").write_text("two\n")
        git(cls.repo, "commit", "-q", "-a", "-m", "fix(retry): cap backoff at 30 s", when="2026-10-01T15:40:00+05:00")
        (cls.repo / "dirty.txt").write_text("not committed\n")

        jobs = cls.repo / ".claude" / "scratch" / "overnight_x" / "jobs"
        jobs.mkdir(parents=True)
        (jobs / "review-retry.md").write_text("review-retry deliverable body\n" * 50, encoding="utf-8")
        state = cls.repo / ".claude" / "scratch" / "overnight_x" / "STATE.md"
        state.write_text(STATE_BODY + "\n", encoding="utf-8")
        close = cls.repo / "vault" / "Claude" / "TODO" / "Overnight" / f"2026-10-01-{SID}.md"
        close.parent.mkdir(parents=True)
        close.write_text("# Close\nCLOSE_BODY_SENTINEL\n", encoding="utf-8")
        cls.state, cls.close = state, close

        t = Transcript(cls.repo)
        t.owner("2026-10-01T10:00:00.000Z", "Build the cache layer for the quest tracker")
        t.text("2026-10-01T10:05:00.000Z", "Starting on the cache layer now, working through the quest store.")
        t.call("2026-10-01T10:10:00.000Z", "Bash", {"command": "git commit -q -m 'feat(cache)'"}, "")
        t.owner(OWNER_TS, "Fix the retry bug, then keep going on your own until it is done")
        t.call("2026-10-01T10:40:00.000Z", "Bash", {"command": "git commit -q -F msg.txt"}, "")
        t.call("2026-10-01T10:41:00.000Z", "Bash", {"command": "git push origin main"},
               "To ../remote.git\n   aaaaaaa..bbbbbbb  main -> main")
        t.call("2026-10-01T10:42:00.000Z", "Bash", {"command": "git push origin main --force"},
               "PreToolUse:Bash hook error: BLOCKED force push", error=True)
        t.call("2026-10-01T10:45:00.000Z", "Bash",
               {"command": "python3 .claude/tools/sidecar_fanout.py jobs.json --detach"},
               fanout_launch(jobs, ["review-retry", "audit-cache"]))
        gate_out = cls.repo / ".claude" / "scratch" / "test_runs" / "gate_run_1.out"
        gate_out.parent.mkdir(parents=True)
        gate_out.write_text("running\n" * 10, encoding="utf-8")
        t.call("2026-10-01T10:45:30.000Z", "Bash", {"command": "pwsh regression_gate.ps1 -Detach"},
               "DETACHED pid=9 mode=full\r\nOUT=" + str(gate_out) + "\r\nERR=" + str(gate_out) + ".err\r\n"
               "WAIT=poll OUT until a line begins VERDICT=")
        t.call("2026-10-01T10:46:00.000Z", "Workflow", {"scriptPath": ".claude/workflows/denied_run.js"},
               "PreToolUse:Workflow hook error: Dispatch DENIED: DENIED_LABEL_SENTINEL", error=True)
        t.call("2026-10-01T10:50:00.000Z", "Bash",
               {"command": "pwsh run_suite.ps1", "run_in_background": True,
                "description": "Run the integration suite"},
               "Command running in background with ID: bgsuite1. You will be notified when it completes.",
               result_fields={"backgroundTaskId": "bgsuite1"})
        t.call("2026-10-01T10:51:00.000Z", "Bash",
               {"command": "pwsh lint.ps1", "run_in_background": True, "description": "Lint pass that ended"},
               "Command running in background with ID: bglint1. You will be notified when it completes.",
               result_fields={"backgroundTaskId": "bglint1"})
        t.notice("2026-10-01T10:55:00.000Z", "bglint1", "Background command \"Lint pass that ended\" completed (exit code 0)")
        t.call("2026-10-01T10:56:00.000Z", "Bash",
               {"command": "pwsh docs.ps1", "run_in_background": True, "description": "Docs build that ended in the queue"},
               "Command running in background with ID: bgdocs1. You will be notified when it completes.",
               result_fields={"backgroundTaskId": "bgdocs1"})
        t.queued_notice("2026-10-01T10:58:00.000Z", "bgdocs1", "Background command \"Docs build\" completed (exit code 0)")
        t.call("2026-10-01T11:00:00.000Z", "Write",
               {"file_path": str(state), "content": STATE_BODY}, "File created successfully")
        t.call("2026-10-01T11:05:00.000Z", "Write",
               {"file_path": str(close), "content": "# Close"}, "File created successfully")
        t.text("2026-10-01T11:10:00.000Z", FINAL_TEXT)
        cls.transcript = t.save(cls.config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cls.repo)))
        cls.packet = run_digest(cls.repo, cls.config)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_packet_is_bounded_and_marks_its_evidence_kind(self):
        self.assertLessEqual(len(self.packet.encode("utf-8")), 16_384)
        self.assertIn("## Agent work", self.packet)
        self.assertIn("not a completion verdict", agent_section(self.packet).splitlines()[0])

    def test_tells_what_the_agent_did_after_the_last_owner_prompt(self):
        section = agent_section(self.packet)
        self.assertIn("Since the last owner prompt (10:30)", section)
        # commits come from git, not from the agent's say-so; the early one predates the prompt
        self.assertIn("fix(retry): cap backoff at 30 s", section)
        self.assertRegex(section, r"2 total, 1 since the last owner prompt")
        self.assertIn("10:40 fix(retry)", section)
        self.assertIn("aaaaaaa..bbbbbbb", section)
        self.assertRegex(section, r"1 `git push` call")

    def test_dispatch_and_its_return_are_told_apart_and_a_denied_one_is_not_counted(self):
        section = agent_section(self.packet)
        self.assertRegex(section, r"2 launched, 1 with a return on disk")
        self.assertRegex(section, r"review-retry .*sonnet high.*returned")
        self.assertRegex(section, r"no return: audit-cache")
        self.assertNotIn("DENIED_LABEL_SENTINEL", section)
        self.assertNotIn("denied_run", section)

    def test_a_detached_job_is_listed_with_its_output_file_and_is_not_a_dispatch(self):
        section = agent_section(self.packet)
        self.assertRegex(section, r"Detached jobs . 1 launched")
        self.assertRegex(section, r"gate_run_1 . \.claude/scratch/test_runs/gate_run_1\.out . 90 B")

    def test_background_job_without_an_end_notice_is_open_and_an_ended_one_is_not(self):
        section = agent_section(self.packet)
        self.assertIn("Run the integration suite", section)
        self.assertRegex(section, r"no end notice")
        self.assertNotIn("Lint pass that ended", section)
        self.assertNotIn("Docs build that ended in the queue", section)

    def test_state_and_close_files_are_pointed_to_and_not_copied(self):
        section = agent_section(self.packet)
        self.assertIn("STATE.md", section)
        self.assertIn(f"2026-10-01-{SID}.md", section)
        self.assertRegex(section, r"STATE\.md .*on disk")
        self.assertNotIn("STATE_BODY_SENTINEL", self.packet)
        self.assertNotIn("CLOSE_BODY_SENTINEL", self.packet)

    def test_decisions_and_open_items_the_agent_stated_are_labeled_as_its_words(self):
        section = agent_section(self.packet)
        said = section[section.index("Said in the last assistant message"):]
        self.assertIn("unverified", said.splitlines()[0])
        self.assertIn("Q1 - Freeze the quest schema?", said)
        self.assertIn("Q2 - Delete the old cache folder?", said)
        self.assertIn("The integration suite has not finished", said)

    def test_with_a_close_doc_written_the_decisions_are_titles_and_a_pointer(self):
        # the Close doc owns each row's options, recommendation and default; the packet does not copy them
        section = agent_section(self.packet)
        said = section[section.index("Said in the last assistant message"):]
        self.assertIn(f"2026-10-01-{SID}.md", said.splitlines()[0])
        self.assertNotIn("Default: schema stays open", said)
        self.assertNotIn("I recommend yes", said)

    def test_git_state_reports_uncommitted_work(self):
        section = agent_section(self.packet)
        self.assertRegex(section, r"Git now .* main @[0-9a-f]{7,} .* [1-9]\d* dirty path")

    def test_full_json_keeps_the_complete_evidence(self):
        data = json.loads((self.repo / "logs" / f"session_digest_{SID[:8]}.json").read_text(encoding="utf-8"))
        work = data["agent_work"]
        self.assertEqual([d["label"] for d in work["dispatches"]], ["review-retry", "audit-cache"])
        self.assertEqual(work["git"]["commits"]["total"], 2)


class AgentWorkCherryPicks(unittest.TestCase):
    """A job's commit on its worktree branch and its cherry-pick on main are one change: count and list it once."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.repo = base / "repo"
        cls.repo.mkdir()
        repo = cls.repo
        git(repo, "init", "-q", "-b", "main")
        (repo / "a.txt").write_text("one\n")
        git(repo, "add", "a.txt")
        git(repo, "commit", "-q", "-m", "feat(cache): add the quest cache", when="2026-10-01T10:10:00+00:00")
        git(repo, "checkout", "-q", "-b", "scratch/job")
        (repo / "b.txt").write_text("retry cap\n")
        git(repo, "add", "b.txt")
        git(repo, "commit", "-q", "-m", "fix(job): cap retry at 30 s", when="2026-10-01T10:20:00+00:00")
        cls.job_hash = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=9", "HEAD"],
                                      capture_output=True, text=True, check=True).stdout.strip()
        git(repo, "checkout", "-q", "main")
        git(repo, "cherry-pick", "scratch/job", when="2026-10-01T10:30:00+00:00")
        cls.main_hash = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=9", "HEAD"],
                                       capture_output=True, text=True, check=True).stdout.strip()
        cls.tmp_repo_base = base

        t = Transcript(repo)
        t.owner("2026-10-01T10:00:00.000Z", "Land the retry cap")
        t.text("2026-10-01T10:05:00.000Z", "Working on it.")
        t.text("2026-10-01T10:50:00.000Z", "Landed.")
        t.save(base / "config" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(repo)))
        cls.packet = run_digest(repo, base / "config")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_cherry_pick_on_the_checked_out_branch_is_one_commit_listed_by_its_main_hash(self):
        section = agent_section(self.packet)
        self.assertRegex(section, r"2 total, 2 since the last owner prompt")
        self.assertEqual(section.count("fix(job): cap retry at 30 s"), 1)
        self.assertIn(self.main_hash[:7], section)
        self.assertNotIn(self.job_hash[:7], section)


class AgentWorkBounds(unittest.TestCase):
    def test_a_long_autonomous_run_stays_inside_the_cap_and_keeps_every_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            (repo / "f.txt").write_text("x\n")
            git(repo, "add", "f.txt")
            git(repo, "commit", "-q", "-m", "chore: seed", when="2026-10-01T09:00:00+00:00")
            for i in range(60):
                (repo / "f.txt").write_text(f"{i}\n")
                git(repo, "commit", "-q", "-a", "-m", "feat: step %d %s" % (i, "long subject " * 12),
                    when="2026-10-01T12:%02d:00+00:00" % i)
            jobs = repo / "jobs"
            jobs.mkdir()
            t = Transcript(repo)
            t.owner("2026-10-01T08:00:00.000Z", "Run the whole migration unattended")
            for i in range(120):
                t.call("2026-10-01T09:%02d:00.000Z" % (i % 60), "Bash", {"command": "sidecar_fanout.py j.json"},
                       fanout_launch(jobs, ["job-%03d-%s" % (i, "x" * 40)]))
            for i in range(80):
                t.call("2026-10-01T10:%02d:00.000Z" % (i % 60), "Bash",
                       {"command": "pwsh x.ps1", "run_in_background": True,
                        "description": "background step %d %s" % (i, "words " * 30)},
                       "Command running in background with ID: b%d." % i,
                       result_fields={"backgroundTaskId": "b%d" % i})
            for i in range(50):
                t.call("2026-10-01T11:%02d:00.000Z" % (i % 60), "Write",
                       {"file_path": str(repo / "scratch" / f"STATE_{i}.md"), "content": "x"}, "ok")
            t.text("2026-10-01T13:00:00.000Z", FINAL_TEXT * 8)
            t.save(base / "config" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(repo)))
            packet = run_digest(repo, base / "config")
            self.assertLessEqual(len(packet.encode("utf-8")), 16_384)
            section = agent_section(packet)
            self.assertIn("120 launched", section)
            self.assertIn("61 total", section)
            self.assertIn("80 with no end notice", section)
            self.assertIn("Q1 - Freeze the quest schema?", section)
            self.assertIn("Default: schema stays open", section)  # no Close doc: the message is the only record

    def test_a_session_outside_git_says_so_and_still_reports_the_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plain = base / "plain"
            plain.mkdir()
            t = Transcript(plain)
            t.owner("2026-10-01T08:00:00.000Z", "Do the thing")
            t.call("2026-10-01T08:10:00.000Z", "Bash", {"command": "git push origin main"},
                   "   1111111..2222222  main -> main")
            t.text("2026-10-01T08:20:00.000Z", "All set, nothing is left over.")
            t.save(base / "config" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(plain)))
            section = agent_section(run_digest(plain, base / "config"))
            self.assertRegex(section, r"git unavailable")
            self.assertIn("1111111..2222222", section)


if __name__ == "__main__":
    unittest.main()
