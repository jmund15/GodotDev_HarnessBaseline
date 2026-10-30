"""Proof of hooks/_checkout_root.py: one answer to "where is the main checkout, and is this a linked worktree"."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
sys.path.insert(0, str(HOOKS))


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class CheckoutRootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name).resolve()
        cls.main = base / "main"
        cls.main.mkdir()
        git(cls.main, "init", "-q")
        git(cls.main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        cls.linked = base / "linked"
        git(cls.main, "worktree", "add", "-q", str(cls.linked), "-b", "side")
        (cls.linked / "sub").mkdir()
        cls.plain = base / "plain"
        cls.plain.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_main_checkout_resolves_to_itself(self):
        import _checkout_root
        self.assertEqual(Path(_checkout_root.main_checkout_root(self.main)), self.main)

    def test_linked_worktree_resolves_to_main(self):
        import _checkout_root
        self.assertEqual(Path(_checkout_root.main_checkout_root(self.linked)), self.main)
        self.assertEqual(Path(_checkout_root.main_checkout_root(self.linked / "sub")), self.main)

    def test_non_repo_falls_back_to_start(self):
        import _checkout_root
        self.assertEqual(Path(_checkout_root.main_checkout_root(self.plain)), self.plain)

    def test_linked_main_root_is_empty_outside_a_linked_worktree(self):
        import _checkout_root
        self.assertEqual(_checkout_root.linked_main_root(self.main), "")
        self.assertEqual(_checkout_root.linked_main_root(self.plain), "")
        self.assertEqual(Path(_checkout_root.linked_main_root(self.linked)), self.main)

    def test_git_dirs_tells_main_linked_and_non_repo_apart(self):
        import _checkout_root
        git_dir, common = _checkout_root.git_dirs(self.main)
        self.assertEqual(git_dir, common)
        git_dir, common = _checkout_root.git_dirs(self.linked)
        self.assertNotEqual(git_dir, common)
        self.assertIsNone(_checkout_root.git_dirs(self.plain))

    def test_session_loader_is_worktree_follows_the_linked_root(self):
        import os
        import session_context_loader
        here = os.getcwd()
        try:
            answers = []
            for path in (self.main, self.linked, self.plain):
                os.chdir(path)
                answers.append(session_context_loader.is_worktree())
        finally:
            os.chdir(here)
        self.assertEqual(answers, [False, True, False])

    def test_repo_arg_pops_the_flag_and_returns_the_resolved_checkout(self):
        import _checkout_root
        argv = ["guard.py", "--repo", str(self.linked / "sub" / ".."), "--quiet"]
        self.assertEqual(_checkout_root.repo_arg(argv), self.linked.resolve())
        self.assertEqual(argv, ["guard.py", "--quiet"])

    def test_repo_arg_is_none_without_the_flag(self):
        import _checkout_root
        argv = ["guard.py", "--quiet"]
        self.assertIsNone(_checkout_root.repo_arg(argv))
        self.assertEqual(argv, ["guard.py", "--quiet"])

    def test_cli_prints_the_linked_main_root_in_posix_form(self):
        out = subprocess.run([sys.executable, str(HOOKS / "_checkout_root.py"), str(self.linked)],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, self.main.as_posix())
        out = subprocess.run([sys.executable, str(HOOKS / "_checkout_root.py"), str(self.main)],
                             capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
