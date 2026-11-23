"""Which transcript store session_digest searches: the checkout's and its linked worktrees', never another project's.

Each case builds its own git repository and worktree under a temp folder and plants CLAUDE_CONFIG_DIR there.
"""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOL = Path(__file__).resolve().parents[1] / "tools" / "session_digest.py"
spec = importlib.util.spec_from_file_location("session_digest", TOOL)
digest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(digest)

SESSION = "abcd1234-launch-session"


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout


class Stores(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name).resolve()
        self.main, self.config = base / "main", base / "cfg"
        self.main.mkdir()
        git(self.main, "init", "-q")
        git(self.main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        self.worktree = self.main / ".claude" / "worktrees" / "wt"
        git(self.main, "worktree", "add", "-q", "-b", "wt", str(self.worktree))
        patch = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.config)})
        patch.start()
        self.addCleanup(patch.stop)
        self.main_store = digest.projects_dir(str(self.main))
        self.worktree_store = digest.projects_dir(str(self.worktree))

    def transcript(self, store, sid=SESSION):
        path = store / (sid + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        return path

    def test_project_dirs_are_the_checkout_then_its_worktrees(self):
        self.assertEqual([self.main_store, self.worktree_store], digest.project_dirs(str(self.main)))

    def test_a_directory_outside_git_has_only_its_own_store(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        plain = Path(outside.name).resolve()
        self.assertEqual([digest.projects_dir(str(plain))], digest.project_dirs(str(plain)))

    def test_a_session_in_a_worktree_store_is_found_from_the_checkout(self):
        path = self.transcript(self.worktree_store)
        self.assertEqual([path], digest.store_transcripts(digest.project_dirs(str(self.main)), SESSION))

    def test_exact_id_is_required_unless_a_prefix_is_asked_for(self):
        self.transcript(self.main_store)
        stores = digest.project_dirs(str(self.main))
        self.assertEqual([], digest.store_transcripts(stores, SESSION[:8]))
        self.assertEqual(1, len(digest.store_transcripts(stores, SESSION[:8], prefix=True)))

    def test_session_store_is_the_one_store_holding_the_session(self):
        self.transcript(self.worktree_store)
        self.assertEqual(self.worktree_store, digest.session_store(str(self.main), SESSION))

    def test_session_store_falls_back_to_the_checkout_when_none_holds_it(self):
        self.assertEqual(self.main_store, digest.session_store(str(self.main), "absent"))


if __name__ == "__main__":
    unittest.main()
