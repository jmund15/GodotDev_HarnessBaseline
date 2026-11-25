"""Proofs for `hooks/job_inbox_deliver.py`: a dispatcher's words in `.claude/scratch/inbox/<key>.md` reach the running job."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hook_call  # noqa: E402

CLAUDE = Path(__file__).resolve().parents[1]
HOOK = CLAUDE / "hooks" / "job_inbox_deliver.py"
HEAD = "Message from your dispatcher (binds like your brief):"
AGENT = "ae4084af70f433908"


def _blocks(*texts):
    return "".join("## 2026-11-24T10:00:00Z from the dispatcher\n\n%s\n\n" % text for text in texts)


class JobInboxDeliverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.inbox = self.root / ".claude" / "scratch" / "inbox"
        self.inbox.mkdir(parents=True)

    def run_hook(self, payload, sidecar=False, root=None, patch_open=False):
        env = {"CLAUDE_PROJECT_DIR": str(root or self.root)}
        if sidecar:
            env["CLAUDE_CODE_SIDECAR"] = "1"
        if patch_open:
            real_open = open

            def refuse(path, *args, **kwargs):
                if str(path).endswith(".md"):
                    raise PermissionError(13, "denied", str(path))
                return real_open(path, *args, **kwargs)

            with mock.patch("builtins.open", refuse):
                code, out, err, hso = _hook_call.call(HOOK, payload, env=env)
        else:
            code, out, err, hso = _hook_call.call(HOOK, payload, env=env)
        self.assertEqual(0, code, err)
        self.assertNotIn("Traceback", err)
        return hso

    def plant(self, key, text):
        with open(self.inbox / (key + ".md"), "ab") as fh:
            fh.write(text.encode("utf-8"))

    def agent_call(self, agent=AGENT, **extra):
        return {"hook_event_name": "PostToolUse", "tool_name": "Read", "session_id": "s-main", "agent_id": agent,
                "cwd": str(self.root), **extra}

    def test_a_native_job_receives_its_inbox_under_the_dispatcher_heading(self):
        self.plant(AGENT, _blocks("widen the scope to the revision pass"))
        hso = self.run_hook(self.agent_call())
        self.assertEqual("PostToolUse", hso["hookEventName"])
        self.assertTrue(hso["additionalContext"].startswith(HEAD))
        self.assertIn("widen the scope to the revision pass", hso["additionalContext"])

    def test_only_new_bytes_arrive_across_calls(self):
        self.plant(AGENT, _blocks("first word"))
        self.assertIn("first word", self.run_hook(self.agent_call())["additionalContext"])
        self.assertEqual({}, self.run_hook(self.agent_call()))
        self.plant(AGENT, _blocks("second word"))
        text = self.run_hook(self.agent_call())["additionalContext"]
        self.assertIn("second word", text)
        self.assertNotIn("first word", text)
        self.assertEqual({}, self.run_hook(self.agent_call()))

    def test_a_sidecar_child_is_keyed_by_its_session_id(self):
        self.plant("child-sid-1", _blocks("sidecar word"))
        call = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "child-sid-1"}
        self.assertIn("sidecar word", self.run_hook(call, sidecar=True)["additionalContext"])

    def test_an_agent_id_outranks_the_sidecar_session_key(self):
        self.plant("s-main", _blocks("session word"))
        self.plant(AGENT, _blocks("agent word"))
        text = self.run_hook(self.agent_call(), sidecar=True)["additionalContext"]
        self.assertIn("agent word", text)
        self.assertNotIn("session word", text)

    def test_the_main_session_delivers_nothing_and_writes_nothing(self):
        self.plant("s-main", _blocks("not for the main session"))
        before = sorted(os.listdir(self.inbox))
        call = {"hook_event_name": "PostToolUse", "tool_name": "Read", "session_id": "s-main"}
        self.assertEqual({}, self.run_hook(call))
        self.assertEqual(before, sorted(os.listdir(self.inbox)))

    def test_one_job_never_reads_another_jobs_inbox(self):
        self.plant("a0000000000000001", _blocks("for the first job"))
        self.assertEqual({}, self.run_hook(self.agent_call(agent="a0000000000000002")))

    def test_a_key_that_is_a_path_is_refused(self):
        self.plant("escape", _blocks("outside"))
        self.assertEqual({}, self.run_hook(self.agent_call(agent="../inbox/escape")))

    def test_a_malformed_payload_passes_silently(self):
        for raw in ("{not json", "", "[]", "null", '"text"', '{"agent_id": 7}'):
            with self.subTest(raw=raw):
                self.assertEqual({}, self.run_hook(raw))

    def test_an_unreadable_inbox_fails_open_and_keeps_its_bytes_for_the_next_call(self):
        self.plant(AGENT, _blocks("kept"))
        self.assertEqual({}, self.run_hook(self.agent_call(), patch_open=True))
        self.assertIn("kept", self.run_hook(self.agent_call())["additionalContext"])

    def test_an_inbox_that_is_a_directory_fails_open(self):
        (self.inbox / (AGENT + ".md")).mkdir()
        self.assertEqual({}, self.run_hook(self.agent_call()))

    def test_a_missing_project_dir_fails_open(self):
        self.assertEqual({}, self.run_hook(self.agent_call(), root=Path(self.temp.name) / "absent"))

    def test_an_unfinished_line_waits_for_its_newline(self):
        self.plant(AGENT, "## stamp\n\nhalf a sent")
        self.assertNotIn("half", self.run_hook(self.agent_call()).get("additionalContext", ""))
        self.plant(AGENT, "ence\n\n")
        self.assertIn("half a sentence", self.run_hook(self.agent_call())["additionalContext"])

    def test_a_replaced_shorter_inbox_is_read_from_its_start(self):
        self.plant(AGENT, _blocks("a long first message " * 5))
        self.run_hook(self.agent_call())
        (self.inbox / (AGENT + ".md")).write_bytes(_blocks("new").encode())
        self.assertIn("new", self.run_hook(self.agent_call())["additionalContext"])

    def test_a_message_past_the_client_cap_arrives_whole_over_several_calls(self):
        lines = ["line %04d %s\n" % (i, "x" * 60) for i in range(400)]
        self.plant(AGENT, "## stamp\n\n" + "".join(lines) + "\n")
        seen = []
        for _ in range(10):
            hso = self.run_hook(self.agent_call())
            if not hso:
                break
            text = hso["additionalContext"]
            self.assertLessEqual(len(text), 10_000)
            seen.append(text[len(HEAD):])
        self.assertGreater(len(seen), 1)
        joined = "".join(seen)
        for line in lines:
            self.assertEqual(1, joined.count(line))

    def test_a_job_in_a_linked_worktree_reads_the_main_checkouts_inbox(self):
        worktree = Path(self.temp.name) / "wt"
        worktree.mkdir()
        gitdir = self.root / ".git" / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: %s\n" % gitdir.as_posix(), encoding="utf-8")
        self.plant(AGENT, _blocks("main inbox word"))
        hso = self.run_hook(self.agent_call(), root=worktree)
        self.assertIn("main inbox word", hso["additionalContext"])

    def test_the_hook_runs_from_its_registered_command(self):
        self.plant(AGENT, _blocks("spawned word"))
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)}
        env.pop("CLAUDE_CODE_SIDECAR", None)
        done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(self.agent_call()), text=True,
                              capture_output=True, env=env, timeout=60)
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("spawned word", json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"])
        bad = subprocess.run([sys.executable, str(HOOK)], input="{not json", text=True, capture_output=True,
                             env=env, timeout=60)
        self.assertEqual(0, bad.returncode, bad.stderr)
        self.assertEqual("", bad.stdout.strip())

    def test_the_base_registers_the_hook_on_every_tool(self):
        base = json.loads((CLAUDE / "settings.base.json").read_text(encoding="utf-8"))
        rows = [entry for entry in base["hooks"]["PostToolUse"]
                if any("job_inbox_deliver.py" in hook["command"] for hook in entry["hooks"])]
        self.assertEqual(1, len(rows))
        self.assertNotIn("matcher", rows[0])


if __name__ == "__main__":
    unittest.main()
