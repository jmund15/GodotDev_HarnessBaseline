"""Proofs for `tools/job_inbox.py`: `send` keys a dispatcher's words to the job that will read them."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

CLAUDE = Path(__file__).resolve().parents[1]
TOOL = CLAUDE / "tools" / "job_inbox.py"
sys.path.insert(0, str(CLAUDE / "hooks"))

SESSION = "0b1c2d3e-0000-4000-8000-00000000aaaa"
OTHER_SESSION = "9f9f9f9f-0000-4000-8000-00000000bbbb"
STARTED = {"type": "started", "key": "v2:e2c4ef3f863b044b32eb4463000a5142cfe612007cfd69458c6c107d0bdf6cca",
           "agentId": "ae4084af70f433908", "label": "review:sa-harness-quality", "phase": "Review"}


def _load():
    spec = importlib.util.spec_from_file_location("job_inbox_under_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class JobInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        (self.root / ".claude" / "scratch").mkdir(parents=True)
        self.projects = self.base / "login" / "projects"
        self.state = self.base / "state"
        self.state.mkdir()
        self.old_state = os.environ.get("HARNESS_HOOK_STATE_DIR")
        os.environ["HARNESS_HOOK_STATE_DIR"] = str(self.state)
        self.addCleanup(self.restore_state)
        self.tool = _load()

    def restore_state(self):
        if self.old_state is None:
            os.environ.pop("HARNESS_HOOK_STATE_DIR", None)
        else:
            os.environ["HARNESS_HOOK_STATE_DIR"] = self.old_state

    def journal(self, run, rows, session=SESSION, age=0):
        path = self.projects / "C--repo" / session / "subagents" / "workflows" / run / "journal.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))

    def sidecar_entry(self, label, record):
        """The shape `dispatch_table.register_sidecars` writes, trimmed from a live session's state file."""
        entry = {"kind": "sidecar", "id": "%s:%s:toolu_01" % (record.parent, label), "record": str(record),
                 "fanout_dir": str(record.parent), "task_id": None, "launched_at": 1795459329.96,
                 "jobs": [{"label": label, "model": "opus", "effort": "high", "launcher": "anthropic_secondary",
                           "record": str(record), "fanout_dir": None, "kind": "sidecar"}],
                 "call": "toolu_01", "writes": False}
        import _hook_state
        path = _hook_state.state_path(SESSION)
        try:
            state = json.loads(Path(path).read_text(encoding="utf-8"))
        except OSError:
            state = {}
        state.setdefault("dispatch_table_pending", []).append(entry)
        Path(path).write_text(json.dumps(state), encoding="utf-8")

    def resolve(self, target, session=SESSION):
        return self.tool.resolve_key(target, session, roots=[self.projects])

    def test_a_label_resolves_to_its_agent_id_from_the_sessions_journal(self):
        self.journal("wf_a", [{"type": "launched"}, STARTED])
        self.assertEqual(("ae4084af70f433908", "native"), self.resolve("review:sa-harness-quality"))

    def test_the_newest_run_of_a_label_wins(self):
        self.journal("wf_old", [dict(STARTED, agentId="a0000000000000001")], age=3600)
        self.journal("wf_new", [dict(STARTED, agentId="a0000000000000002")], age=5)
        self.assertEqual("a0000000000000002", self.resolve("review:sa-harness-quality")[0])

    def test_a_label_the_journal_repeats_resolves_to_its_last_row(self):
        self.journal("wf_a", [dict(STARTED, agentId="a0000000000000001"), dict(STARTED, agentId="a0000000000000002")])
        self.assertEqual("a0000000000000002", self.resolve("review:sa-harness-quality")[0])

    def test_another_sessions_journal_is_not_searched(self):
        self.journal("wf_a", [STARTED], session=OTHER_SESSION)
        self.assertEqual(("label-review_sa-harness-quality", "label"), self.resolve("review:sa-harness-quality"))

    def test_an_agent_id_keys_as_itself(self):
        self.assertEqual(("ae4084af70f433908", "native"), self.resolve("ae4084af70f433908"))

    def test_a_sidecar_label_resolves_to_the_session_id_in_its_record(self):
        record = self.base / "out" / "lx-pure.record.json"
        record.parent.mkdir()
        record.write_text(json.dumps({"label": "lx-pure", "sessionId": "e3dd0dc7-7dc7-46b3-9f74-fe0b5e9c6d7f"}),
                          encoding="utf-8")
        self.sidecar_entry("lx-pure", record)
        self.assertEqual(("e3dd0dc7-7dc7-46b3-9f74-fe0b5e9c6d7f", "sidecar"), self.resolve("lx-pure"))

    def test_a_running_sidecar_resolves_through_its_stream_before_its_record_exists(self):
        record = self.base / "out" / "lx-proc.record.json"
        record.parent.mkdir()
        (self.base / "out" / "lx-proc.stream").write_text(
            '{"type":"system","subtype":"hook_started"}\n'
            '{"type":"system","subtype":"init","session_id":"5d5d5d5d-1111-4222-8333-444444444444"}\n',
            encoding="utf-8")
        self.sidecar_entry("lx-proc", record)
        self.assertEqual(("5d5d5d5d-1111-4222-8333-444444444444", "sidecar"), self.resolve("lx-proc"))

    def test_a_sidecar_with_no_session_id_yet_falls_back_to_its_label(self):
        record = self.base / "out" / "lx-new.record.json"
        record.parent.mkdir()
        self.sidecar_entry("lx-new", record)
        self.assertEqual(("label-lx-new", "label"), self.resolve("lx-new"))

    def test_an_unknown_label_keys_as_label_dash_label(self):
        self.assertEqual(("label-seat-one", "label"), self.resolve("seat-one"))

    def test_no_session_still_falls_back_to_the_label(self):
        self.assertEqual(("label-seat-one", "label"), self.resolve("seat-one", session=None))

    def test_the_label_key_matches_the_filename_a_seat_reads(self):
        self.assertEqual("label-a_b_c", self.tool.label_key("a:b/c"))

    def test_send_appends_a_stamped_block_and_each_send_adds_to_the_file(self):
        path = self.tool.send(self.root, "ae4084af70f433908", "widen the scope")
        self.tool.send(self.root, "ae4084af70f433908", "and keep the tests")
        self.assertEqual(self.root / ".claude" / "scratch" / "inbox" / "ae4084af70f433908.md", path)
        text = path.read_text(encoding="utf-8")
        self.assertLess(text.index("widen the scope"), text.index("and keep the tests"))
        self.assertEqual(2, text.count("\n## "))

    def test_the_tool_and_the_hook_agree_on_the_inbox_of_a_linked_worktree(self):
        import job_inbox_deliver
        worktree = self.base / "wt"
        worktree.mkdir()
        gitdir = self.root / ".git" / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: %s\n" % gitdir.as_posix(), encoding="utf-8")
        self.assertEqual(self.root.resolve(), Path(job_inbox_deliver.main_root(worktree)).resolve())

    def run_cli(self, *args):
        env = {**os.environ, "CLAUDE_PROJECT_DIR": str(self.root), "CLAUDE_CODE_SESSION_ID": SESSION,
               "HARNESS_HOOK_STATE_DIR": str(self.state), "CLAUDE_CONFIG_DIR": str(self.base / "login")}
        return subprocess.run([sys.executable, str(TOOL), *args], text=True, capture_output=True, env=env,
                              timeout=60)

    def test_the_cli_prints_the_path_it_wrote_and_how_it_keyed(self):
        done = self.run_cli("send", "ae4084af70f433908", "--text", "word from the owner")
        self.assertEqual(0, done.returncode, done.stderr)
        written = Path(done.stdout.splitlines()[0])
        self.assertEqual("ae4084af70f433908.md", written.name)
        self.assertIn("word from the owner", written.read_text(encoding="utf-8"))
        self.assertIn("native", done.stdout)

    def test_the_cli_reads_a_file_and_refuses_no_text_both_or_a_missing_file(self):
        note = self.base / "note.md"
        note.write_text("from a file\n", encoding="utf-8")
        done = self.run_cli("send", "seat-one", "--file", str(note))
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("from a file", Path(done.stdout.splitlines()[0]).read_text(encoding="utf-8"))
        self.assertIn("label-seat-one.md", done.stdout)
        for args in (("send", "seat-one"), ("send", "seat-one", "--text", "x", "--file", str(note)),
                     ("send", "seat-one", "--file", str(self.base / "absent.md")), ("send", "seat-one", "--text", " ")):
            with self.subTest(args=args):
                refused = self.run_cli(*args)
                self.assertEqual(2, refused.returncode)
                self.assertNotIn("Traceback", refused.stderr)


if __name__ == "__main__":
    unittest.main()
